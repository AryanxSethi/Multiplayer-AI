import PySide6
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPropertyAnimation, QSize,
                            Qt, QTimer, QUrl, Slot)
from PySide6.QtGui import (QAccessible, QAccessibleAnnouncementEvent,
                           QGuiApplication, QIcon, QKeySequence, QColor, QDesktopServices, QPalette, QShortcut)
from PySide6.QtWidgets import (QApplication, QFileDialog,
    QFormLayout, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QScrollArea, QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget)

from network_a2a.persistence import HistoryStore, conversation_archive, model_context
from network_a2a.content import MAX_MESSAGE_BYTES, content_summary, validate_content
from network_a2a.adapters import PROVIDERS
from network_a2a.web_search import validate_search_settings
from network_a2a.orchestration import GENERAL_TARGET, TASK_TYPES, general_chat_state
from network_a2a.chat_commands import HELP_TEXT, is_command, leading_mention, parse_command, resolve_identity

from .bridge import NetworkThread
from .dialogs import InviteDialog, available_agent_name, parse_invitation
from .icons import navigation_icon, provider_logo, provider_pixmap
from .lan import lan_addresses
from .layout import minimum_size, sidebar_should_collapse, window_size
from .markdown import MarkdownMessage
from .model_purpose import ModelPurpose
from .resources import ResourceSampler
from .theme import (DARK, PROVIDER_NAMES, THEME_CHOICES, color, mix,
                   provider_color, provider_entry, provider_names, resolve_theme,
                   stylesheet, system_theme)
from .widgets import (Composer, ErrorLine, HoverRow, MessageBubble, OrbitArt,
                    Select, TickCheckBox, WorkspaceButton, action, app_mark, label)


def frame(name, layout_type=QVBoxLayout):
    widget = QFrame()
    widget.setObjectName(name)
    layout = layout_type(widget)
    return widget, layout


def clear_layout(layout):
    """Hide and schedule removal of layout widgets, recursively clearing child layouts."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


# Navigation is declarative because the nav buttons, the stacked pages and
# the page title all have to agree on order and length. Adding a page here
# and nowhere else used to mean fixing the title list as well, which is
# exactly the kind of duplication that goes stale.
PAGES = (
    ("Overview", "overview"),
    ("Agents", "agents"),
    ("Workspaces", "workspaces"),
    ("Conversations", "conversations"),
    ("Providers", "providers"),
    ("Resources", "resources"),
    ("Cookbook", "cookbook"),
    ("Settings", "settings"),
)
# The titles are plain text and styled as a heading. They used to carry a
# leading "#  ", which looked right in the source and was painted on screen:
# label() pins Qt.TextFormat.PlainText, so the hash was never markdown, it was
# a character in the largest text in the window, on every page, every session.
# The two chat empty states had the same prefix for the same reason.
PAGE_TITLES = tuple(key for _, key in PAGES)
# Looked up by key rather than counted. Adding a page used to shift every
# index below it, which broke the Ctrl+5 and Ctrl+, shortcuts without
# anything failing: the numbers were still in range, just pointing at the
# wrong page.
PAGE_INDEX = {key: index for index, (_, key) in enumerate(PAGES)}

# Tooltips for the navigation buttons. Every one of them is reachable from
# the keyboard and from the rail, so a page whose name does not describe its
# contents needs saying out loud.
PAGE_HELP = {
    "overview": "What is in this workspace, and what to do next",
    "agents": "Every model agent on this workspace, online or not",
    "workspaces": "The workspace you are in, and how to add another",
    "conversations": "Shared conversations and the messages in them",
    "providers": "Where models come from: local, hosted, or your own endpoint",
    "resources": "Disk, memory and what this device is using",
    "cookbook": "Which AI models this device's memory can hold, and how to run them",
    "settings": "Appearance, privacy and resetting this device's identity",
}
RESOURCES_PAGE = PAGE_INDEX["resources"]
SETTINGS_PAGE = PAGE_INDEX["settings"]
WORKSPACES_PAGE = PAGE_INDEX["workspaces"]
AGENTS_PAGE = PAGE_INDEX["agents"]
# Conversations was navigated to by the literal 2, which was the right number
# until Workspaces was inserted ahead of it. Selecting an agent has been
# landing the reader on the Workspaces page while the composer they had just
# focused sat on a page that was not showing.
CONVERSATIONS_PAGE = PAGE_INDEX["conversations"]
OVERVIEW_PAGE = PAGE_INDEX["overview"]
COOKBOOK_PAGE = PAGE_INDEX["cookbook"]
# The mark beside a message, and the width a grouped message is indented by
# when it follows its own speaker's earlier message.
SPEAKER_AVATAR = 34
# Qt's QWIDGETSIZE_MAX, which PySide6 does not export. ``setFixedHeight``
# pins both ends of a widget's range, and a bubble is pinned on every render,
# so it has to be let go with the number spelled out before it can be measured.
UNPINNED = 16777215
# How long a toast stays up. Long, because a failure has to be read and acted
# on; a copy has already happened by the time it appears.
TOAST_MS = 7500
COPY_TOAST_MS = 1000


def app_icon(theme_name=DARK):
    """The window and taskbar icon, drawn in the accent colour."""
    return QIcon(app_mark(theme_name))


def device_initial(identity):
    """The single letter this device's avatar carries.

    Taken from the identity so two devices on one screen can be told apart,
    which a fixed mark cannot do. The identity is only known once the
    runtime has started, so an unidentified device gets a question mark
    rather than a blank, and it is never allowed to be an empty string,
    which would draw an empty tile that looks like a missing image.
    """
    return (identity or "").strip()[:1].upper() or "?"


def refresh_join_icon(window, theme_name):
    """Redraw the rail's plus in this theme's success colour.

    Hand-painted, so like the orbit and the avatar it never sees a
    stylesheet and has to be told when the palette moves. Painted at twice
    the slot and marked as such, or it arrives soft on a dense display.
    The middle pixel of a cross is a stroke, so that is what a test reads
    to confirm the colour actually landed.
    """
    pixmap = navigation_icon("plus", color(theme_name, "success")).pixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    window.join_button.setIcon(QIcon(pixmap))


def device_avatar(theme_name, identity, size=34):
    """A circular avatar carrying this device's initial on the app's mark.

    The sidebar used to set its text to " M " with a fixed width but no
    height and a 14px radius, which drew a rounded rectangle of whatever
    height the layout gave it rather than the circle it was aiming at. It
    is painted at twice the size and scaled down instead, so the curve is
    the real one and it stays crisp on a dense display.
    """
    widget = QLabel()
    widget.setFixedSize(size, size)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    mark = app_mark(theme_name, device_initial(identity), size * 2)
    mark.setDevicePixelRatio(2)
    widget.setPixmap(mark)
    widget.setAccessibleName(f"This device, {identity or 'not identified yet'}")
    return widget


class MainWindow(QMainWindow):
    def __init__(self, storage):
        super().__init__()
        self.storage = storage
        self.agents = []
        self.identity = ""
        self.remote = False
        self.port = 0
        self.selected = None
        self.chats = {}
        self.conversations = {}
        self.workspace_id = None
        self.workspace_list = []
        self.workspace_meta = {}
        self.history_store = None
        self.live_requests = set()
        self.workspace_buttons = {}
        self.callbacks = {}
        self.command_request = None
        self.sidebar_expanded = False
        self.busy = False
        # Every message a card shows, keyed by the card that owns it. Held
        # here rather than in the card's widgets because cards are rebuilt on
        # every poll: a message kept in a widget is destroyed by the next
        # render, which is exactly what happened to the workspace page's
        # single shared line. Only set_card_message, its dismiss handler and
        # the card's own action may touch these.
        self.card_messages = {}
        # The card message line currently on screen for each key, so a
        # rebuild of the page can find it again. Lines hold no message of
        # their own.
        self.card_lines = {}
        # The bubbles from the last render, so their width can be recapped
        # when the panel is resized.
        self.message_bubbles = []
        # Whether each Workspaces operation is still in flight. The poll runs
        # every three seconds and this page re-enables its buttons from it, so
        # without this a join could be submitted a second time while the first
        # was still waiting on a relay that takes up to 30 seconds.
        self.workspace_busy = {"create": False, "join": False, "invite": False}
        # While a shared conversation is being ended at the relay, so its
        # actions cannot be fired twice from two places at once.
        self.chat_busy = False
        self.toast_error = False
        # An alert that arrived while a failure was still on screen and has not
        # been shown yet. Suppressing it lost it outright, which is right for a
        # confirmation -- what it confirmed has already visibly happened -- and
        # wrong for an alert, because nobody has been told anything yet. Held
        # here and shown when the failure is dismissed.
        self._pending_alert = None
        self.request_count = 0
        self.ready = False
        self.closing = False
        self.preparing_files = False
        self.setWindowTitle("Multiplayer AI")
        # Resolved before any widget is built so everything created below
        # picks up the right colours the first time.
        self.theme = resolve_theme(storage.settings)
        self.setWindowIcon(app_icon(self.theme))
        # The app always opens maximised, so no size is restored: a window
        # that reopens at whatever it happened to be last time is the reason
        # it read as an ordinary window rather than an app. A size is still
        # set here because Qt wants sane geometry before maximising, and the
        # minimum stays so restoring the window down cannot make it unusable.
        width, height = window_size(None, {}, self.primary_screen_size())
        self.resize(width, height)
        self.setMinimumSize(*minimum_size(self.primary_screen_size()))
        self.setStyleSheet(stylesheet(self.theme))
        self.style_placeholders()
        # Measuring the disk the preferences live on is what the Resources
        # page reports, so it is resolved once here.
        try:
            self.sampler = ResourceSampler(storage.directory)
        except Exception:
            self.sampler = None
        self.network = NetworkThread(storage, self)
        self.network.event.connect(self.network_event)
        self.network.completed.connect(self.command_success)
        self.network.failed.connect(self.command_failure)
        self.network.finished.connect(self.finish_close)
        self.build_ui()
        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(350)
        self.history_timer.timeout.connect(self.persist_history)
        self.composer.textChanged.connect(lambda: self.history_timer.start())
        self.network.start()

    def build_ui(self):
        """Build the workspace rail, navigation, pages, and application status controls."""
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        self.shell_layout = shell
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        rail, rail_layout = frame("rail")
        rail.setFixedWidth(72)
        rail_layout.setContentsMargins(12, 20, 12, 18)
        rail_layout.setSpacing(16)
        home = WorkspaceButton("M")
        home.setToolTip("Your local workspace")
        # The button's text is "M", which a screen reader announces as "M,
        # button". The name it carries is the same sentence the tooltip does.
        home.setAccessibleName("Your local workspace")
        home.clicked.connect(lambda: self.command("use_local"))
        rail_layout.addWidget(home)
        workspace_scroll = QScrollArea()
        workspace_scroll.setWidgetResizable(True)
        workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        workspace_content = QWidget()
        self.workspace_rail = QVBoxLayout(workspace_content)
        self.workspace_rail.setContentsMargins(0, 0, 0, 0)
        self.workspace_rail.setSpacing(12)
        self.workspace_rail.setAlignment(Qt.AlignmentFlag.AlignTop)
        workspace_scroll.setWidget(workspace_content)
        rail_layout.addWidget(workspace_scroll, 1)
        join = WorkspaceButton("")
        self.join_button = join
        join.setIconSize(QSize(24, 24))
        join.set_ink("success")
        join.setToolTip("Create or join a workspace")
        # Empty text and an icon, so without this it is announced as nothing at
        # all -- a 48x48 control in the rail that a screen reader walks past.
        join.setAccessibleName("Create or join a workspace")
        refresh_join_icon(self, self.theme)
        join.clicked.connect(self.add_workspace)
        rail_layout.addWidget(join)
        self.help_button = WorkspaceButton("?")
        self.help_button.clicked.connect(self.open_documentation)
        self.help_button.setToolTip("Project documentation")
        # Announced as "question mark, button" otherwise.
        self.help_button.setAccessibleName("Project documentation")
        rail_layout.addWidget(self.help_button)
        shell.addWidget(rail)
        sidebar, outer = frame("sidebar")
        sidebar.setFixedWidth(238)
        self.sidebar = sidebar
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # The user panel has to run edge to edge, the way the sidebar it
        # belongs to does. It used to be a child of a layout carrying 14px
        # side margins, so its background stopped that far short on both
        # sides and read as a panel clipped by the window rather than as a
        # footer. Everything above it keeps those margins; the footer is
        # separated by a hairline instead, as in the dark theme.
        body = QWidget()
        body.setObjectName("sidebarBody")
        side = QVBoxLayout(body)
        side.setContentsMargins(14, 22, 14, 0)
        side.setSpacing(8)
        outer.addWidget(body, 1)
        self.workspace_label = label("My workspace", "heading", True)
        workspace_heading = QHBoxLayout()
        workspace_heading.addWidget(self.workspace_label, 1)
        self.sidebar_close = action("×", self.toggle_sidebar, name="ghost")
        self.sidebar_close.setFixedSize(28, 28)
        self.sidebar_close.setToolTip("Close the sidebar")
        self.sidebar_close.setAccessibleName("Close the sidebar")
        workspace_heading.addWidget(self.sidebar_close)
        side.addLayout(workspace_heading)
        self.workspace_settings_button = action("Workspace settings", self.manage_workspace, name="ghost")
        self.workspace_settings_button.setEnabled(False)
        self.workspace_settings_button.setToolTip(
            "Rename, invite a device to, or delete this workspace")
        side.addWidget(self.workspace_settings_button)
        side.addSpacing(18)
        side.addWidget(label("WORKSPACE", "eyebrow"))
        self.nav_buttons = []
        for index, (name, key) in enumerate(PAGES):
            button = action(name, lambda checked=False, page=index: self.navigate(page), name="nav")
            button.setIcon(navigation_icon(key, color(self.theme, "text")))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setToolTip(PAGE_HELP[key])
            self.nav_buttons.append(button)
            # The Cookbook's button is built here and appended above, so the
            # nav list stays one-per-page and navigate() can check it, but it is
            # not laid out in the rail. It is put in the top bar instead, next
            # to the two actions somebody is most likely to take first, and it
            # behaves identically: same icon, same help text, same selected
            # state, same Ctrl+N.
            if key != "cookbook":
                side.addWidget(button)
        side.addStretch(1)
        # statusWarn, not a success: this device has no relay yet, which is what
        # starting up looks like. The four states are named rather than implied,
        # because this line is the one piece of status always on screen and it
        # used to be green through every failure it could report.
        self.connection_status = label("●  Starting your network…", "statusWarn", True)
        self.connection_status.setToolTip(
            "Whether this device is talking to its own relay. Unlocked keys are needed to send.")
        side.addWidget(self.connection_status)
        # The only retry anywhere in the app. Hidden unless the relay has been
        # heard from and then lost; see set_connection_state.
        self.connection_retry = action("Retry", self.retry_connection, name="ghost")
        self.connection_retry.setAccessibleName("Retry the connection to the relay")
        self.connection_retry.setToolTip("Ask the relay for an update now, without waiting")
        self.connection_retry.setVisible(False)
        side.addWidget(self.connection_retry)
        profile, row = frame("profile", QHBoxLayout)
        row.setContentsMargins(12, 11, 12, 12)
        row.setSpacing(10)
        avatar = device_avatar(self.theme, self.identity)
        self.avatar = avatar
        row.addWidget(avatar)
        copy = QVBoxLayout()
        copy.setSpacing(1)
        # The device identity used to sit on its own line just above this
        # card, next to a card that already said "This device". One line
        # carrying the actual id is both shorter and more use to somebody
        # who has to read it out to join someone else's workspace.
        self.profile_identity = label("Starting…")
        self.profile_identity.setToolTip(
            "This device's identity. Someone inviting you to a workspace will "
            "need it.")
        copy.addWidget(self.profile_identity)
        copy.addWidget(label("This device", "muted"))
        row.addLayout(copy)
        outer.addWidget(profile)
        shell.addWidget(sidebar)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        top, top_layout = frame("topbar", QHBoxLayout)
        top.setFixedHeight(68)
        top_layout.setContentsMargins(28, 0, 25, 0)
        self.page_title = label("overview", "heading")
        self.sidebar_toggle = action("Menu", self.toggle_sidebar, name="ghost")
        self.sidebar_toggle.setToolTip("Show workspace navigation")
        top_layout.addWidget(self.sidebar_toggle)
        top_layout.addWidget(self.page_title)
        top_layout.addStretch()
        self.invite_button = action("Invite a device", self.invite_device)
        self.add_button = action("+  Connect a model", self.add_agent, True)
        # The top bar carried no tooltips at all, so these two were the one
        # part of the shell a new user could hover with nothing to learn.
        self.invite_button.setToolTip(
            "Create an invitation that another device can use to join this workspace")
        self.add_button.setToolTip(
            "Connect a model agent to this workspace")
        # The Cookbook's navigation button, moved here from the rail. It is the
        # same object the rail loop built, so it checks and unchecks with the
        # rest of them; only its position differs. Restyled because it is a
        # 34px square beside two text buttons rather than a full-width row.
        self.cookbook_button = self.nav_buttons[COOKBOOK_PAGE]
        self.cookbook_button.setObjectName("ghost")
        self.cookbook_button.setText("")
        self.cookbook_button.setFixedSize(40, 34)
        self.cookbook_button.setAccessibleName("Cookbook")
        self.invite_button.setEnabled(False)
        self.add_button.setEnabled(False)
        top_layout.addWidget(self.cookbook_button)
        top_layout.addWidget(self.invite_button)
        top_layout.addWidget(self.add_button)
        body_layout.addWidget(top)
        # A message and, when it is a failure, a way to get rid of it. The same
        # shape as ErrorLine -- message, then a ghost dismiss -- so there is one
        # convention for it rather than two. The row is what is shown and hidden;
        # the label inside carries the text.
        self.toast_row = QFrame()
        # QFrame rather than QWidget because style_toast() puts a border and a
        # padding on this row, and Qt's style sheet applies the box model only to
        # frames. On a plain QWidget both are dropped without a word: contents()
        # comes back equal to rect() and the toast renders with no border and no
        # inset, which is why the layout below sets its own margins to zero --
        # the padding that was supposed to supply them was never applied.
        toast_layout = QHBoxLayout(self.toast_row)
        toast_layout.setContentsMargins(0, 0, 0, 0)
        toast_layout.setSpacing(6)
        self.toast = label("", wrap=True)
        toast_layout.addWidget(self.toast, 1)
        self.toast_dismiss = action("×", self.dismiss_notice, name="ghost")
        self.toast_dismiss.setObjectName("errorDismiss")
        self.toast_dismiss.setFixedSize(31, 31)
        self.toast_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toast_dismiss.setAccessibleName("Dismiss message")
        toast_layout.addWidget(self.toast_dismiss, 0, Qt.AlignmentFlag.AlignTop)
        self.style_toast()
        self.toast_row.hide()
        # One timer for the toast, restarted by every notice, rather than a
        # singleShot scheduled per notice. Two notices in quick succession used
        # to leave two timers pending and the older one took the toast down in
        # the middle of the newer: copy something, a failure arrives within the
        # second, and the failure vanished after the copy's second instead of
        # its own seven and a half. Created here rather than in __init__ so the
        # toast it hides already exists.
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast_row.hide)
        body_layout.addWidget(self.toast_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setToolTip("Waiting on the network")
        body_layout.addWidget(self.progress)
        # Beside the bar rather than replacing it, so the message and the
        # movement reinforce each other instead of competing.
        self.busy_label = label("", "muted", True)
        self.busy_label.hide()
        body_layout.addWidget(self.busy_label)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.overview_page())
        self.stack.addWidget(self.agents_page())
        self.stack.addWidget(self.workspaces_page())
        self.stack.addWidget(self.chat_page())
        self.stack.addWidget(self.providers_page())
        self.stack.addWidget(self.resources_page())
        # A placeholder in the Cookbook's place, not the page itself. Building
        # it costs a hardware probe, and that probe runs nvidia-smi with a
        # three second timeout -- on the GUI thread, during build_ui, before
        # anything is on screen. So every launch paid up to three seconds of
        # frozen window, on a machine that has an NVIDIA card, to construct a
        # page most sessions never open. It is built the first time it is
        # navigated to instead.
        self.stack.addWidget(self._cookbook_placeholder())
        self.cookbook_panel = None
        self.stack.addWidget(self.settings_page())
        body_layout.addWidget(self.stack, 1)
        shell.addWidget(body, 1)
        self.install_shortcuts()
        self.apply_sidebar_density()
        self.about_label.setText(
            f"Multiplayer AI desktop · Python {platform.python_version()} · "
            f"Qt {PySide6.__version__}"
        )
        self.update_theme_hint()
        self.navigate(OVERVIEW_PAGE)
        # Settle the welcome now rather than waiting for the first agents
        # event, so the first paint never shows the welcome and the
        # statistics at the same time.
        self.update_overview_state()

    def refresh_avatar(self):
        """Repaint the device avatar from the theme and the current identity.

        The avatar carries two things that both change under it: the accent
        it is filled with follows the theme, and the initial it carries
        follows the identity, which only arrives once the runtime starts.
        Called from both places rather than at construction, since the
        identity is empty until then.
        """
        if not hasattr(self, "avatar"):
            return
        mark = app_mark(self.theme, device_initial(self.identity),
                        self.avatar.width() * 2)
        mark.setDevicePixelRatio(2)
        self.avatar.setPixmap(mark)

    def install_shortcuts(self):
        """Keyboard navigation, so the app is usable without a mouse."""
        for index in range(len(PAGES)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda page=index: self.navigate(page))
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(
            lambda: self.navigate(SETTINGS_PAGE)
        )
        QShortcut(QKeySequence("F5"), self).activated.connect(self.refresh_resources)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh_resources)

    def apply_sidebar_density(self):
        """Keep workspace navigation reachable in a narrow window."""
        collapse = sidebar_should_collapse(self.width(), self.primary_screen_size())
        if collapse:
            self.shell_layout.removeWidget(self.sidebar)
            self.sidebar.setGeometry(72, 0, 238, self.centralWidget().height())
            if self.sidebar_expanded:
                self.sidebar.raise_()
        elif self.shell_layout.indexOf(self.sidebar) < 0:
            self.shell_layout.insertWidget(1, self.sidebar)
        self.sidebar.setVisible(not collapse or self.sidebar_expanded)
        self.sidebar_close.setVisible(collapse)
        if hasattr(self, "sidebar_toggle"):
            self.sidebar_toggle.setVisible(collapse)

    def toggle_sidebar(self):
        self.sidebar_expanded = not self.sidebar_expanded
        self.apply_sidebar_density()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_sidebar_density()

    def scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 27, 30, 27)
        layout.setSpacing(22)
        scroll.setWidget(content)
        return scroll, layout

    def overview_page(self):
        """Build the workspace overview, with a first-run welcome in place of it.

        A new user opening this page used to see three statistic cards all
        reading zero and a large empty activity list. None of that tells
        them anything, and the only action on offer was in the top bar.
        So when the workspace holds nothing at all, the statistics are
        replaced by a panel that says what the app is and offers the two
        ways to get started.
        """
        page, layout = self.scroll_page()
        layout.addWidget(label("Workspace overview", "title"))

        self.welcome_card, welcome = frame("hero")
        welcome.setContentsMargins(28, 26, 20, 26)
        welcome.setSpacing(12)
        # The orbit illustration was painted for this panel and never
        # mounted, so the first thing a new user saw had no motion in it at
        # all. Text and actions stay on the left; the art takes the right
        # and is allowed to shrink before the words are.
        hero_row = QHBoxLayout()
        hero_row.setSpacing(18)
        hero_words = QVBoxLayout()
        hero_words.setSpacing(10)
        hero_row.addLayout(hero_words, 1)
        self.welcome_art = OrbitArt(theme=self.theme)
        self.welcome_art.set_moving(not self.storage.settings.get("reduce_motion"))
        hero_row.addWidget(self.welcome_art, 0)
        welcome.addLayout(hero_row)
        hero_words.addWidget(label("Nothing here yet", "heroTitle"))
        hero_words.addWidget(label(
            "This workspace runs a private network on your own device. "
            "Connect a model to start talking to it, or join someone "
            "else's workspace with an invitation.", "muted", True))
        hero_words.addSpacing(6)
        # Held on the window so it can be disabled until the runtime is
        # ready, exactly as the top bar's own connect button is. Laid out in
        # a row so they keep their natural width instead of stretching the
        # full width of the card.
        self.welcome_connect = action("  +  Connect a model", self.add_agent, True)
        self.welcome_connect.setEnabled(False)
        self.welcome_join = action("  Join a workspace", self.add_workspace, name="ghost")
        self.welcome_connect.setToolTip(
            "Connect a model agent to this workspace")
        # Opens the Workspaces page, where both routes sit as cards. It used to
        # open a menu and then a dialog, and the note explaining that was left
        # behind when the menu and the dialog both went.
        self.welcome_join.setToolTip(
            "Open Workspaces, where you can create a workspace or join one with an invitation")
        choices = QHBoxLayout()
        choices.setSpacing(10)
        choices.addWidget(self.welcome_connect)
        choices.addWidget(self.welcome_join)
        choices.addStretch()
        hero_words.addLayout(choices)
        layout.addWidget(self.welcome_card)
        # State, not a toast, for a startup that failed. The toast is gone in
        # seven and a half seconds, and a fatal is the one event the app cannot
        # recover from by itself: the controls it disables stay disabled, so
        # after the toast the window was full of things that would not work
        # and no reason why. Shaped like pending_cleanup for the same reason.
        #
        # On the page rather than inside the welcome card, which hides itself
        # as soon as there is a conversation to show, because a fatal must be
        # readable whatever else the window holds.
        self.startup_error = label("", "errorMessage", True)
        self.startup_error.setVisible(False)
        self.startup_error.setWordWrap(True)
        layout.addWidget(self.startup_error)

        # Everything the welcome replaces, kept as one widget so it can be
        # hidden as a unit rather than six pieces.
        self.overview_content = QWidget()
        body = QVBoxLayout(self.overview_content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(22)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.stat_values = []
        for index, (title, subtitle) in enumerate((("Agents in this workspace", "Click to see your model agents"), ("Agents online", "Connected models"), ("Requests this visit", "Completed and pending requests"))):
            card, column = frame("stat")
            column.setContentsMargins(18, 15, 18, 15)
            column.addWidget(label(title, "muted"))
            value = action("0", self.show_workspace_agents, name="statValue") if index == 0 else label("0", "statValue")
            # The title and the subtitle are siblings of the value, not children
            # of it, so the button's own name was the digit alone -- announced
            # as "0, button". The subtitle is also the only place the click
            # affordance is written down, and a pointer-only hint is a hint no
            # keyboard or screen reader user ever receives.
            value.setAccessibleName(f"{title}. {subtitle}.")
            if index == 0:
                value.setFlat(True)
            column.addWidget(value)
            column.addWidget(label(subtitle, "muted"))
            self.stat_values.append(value)
            stats.addWidget(card, 1)
        body.addLayout(stats)
        body.addWidget(label("Your agents", "heading"))
        self.overview_cards = QGridLayout()
        self.overview_cards.setSpacing(14)
        body.addLayout(self.overview_cards)
        activity_card, column = frame("card")
        column.setContentsMargins(20, 18, 20, 18)
        column.addWidget(label("Workspace activity", "heading"))
        self.activity_list = QListWidget()
        self.activity_list.setMinimumHeight(140)
        self.activity_list.setMaximumHeight(210)
        column.addWidget(self.activity_list)
        body.addWidget(activity_card)
        body.addWidget(label("Private keys stay on your device. Possibilities go everywhere.", "muted"))
        body.addStretch()
        layout.addWidget(self.overview_content)
        layout.addStretch()
        return page

    def update_overview_state(self):
        """Show the welcome only while there is genuinely nothing to show.

        Deliberately narrow: an online count of zero with agents present is
        a real statistic, so the statistics only go away when there are no
        agents, no conversations and nothing has happened yet.

        The test is ``self.agents``, which always carries this device, and
        so could never make this true. The welcome has to key off the
        model agents a user can actually talk to, which is why it appeared
        on a first run and then never again.
        """
        empty = not self.model_agents() and not self.conversations
        self.welcome_card.setVisible(empty)
        self.overview_content.setVisible(not empty)

    def agents_page(self):
        page, layout = self.scroll_page()
        layout.addWidget(label("YOUR DISTRIBUTED TEAM", "eyebrow"))
        layout.addWidget(label("Agents", "title"))
        layout.addWidget(label("Models connected to this workspace.", "muted"))
        self.model_card = self.model_connection_card()
        layout.addWidget(self.model_card)
        layout.addWidget(label("CONNECTED", "eyebrow"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search agents or models…")
        # Without this the field is announced as an editable line with nothing
        # but its placeholder, which is the one part of a screen reader's
        # reading that can change under the reader's hands.
        self.search.setAccessibleName("Search connected agents and models")
        self.search.setAccessibleDescription(
            "Filters the agents below by name, model or provider.")
        self.search.textChanged.connect(self.render_agents)
        layout.addWidget(self.search)
        self.agent_cards = QGridLayout()
        self.agent_cards.setSpacing(16)
        layout.addLayout(self.agent_cards)
        layout.addStretch()
        return page

    def model_connection_card(self):
        """The whole model form, on the page, above the connected models.

        This is AgentDialog's 272 lines moved into the content area. It was a
        separate window for a form with no reason to be one: the fields were
        somewhere else from the list of models they add to, and the result of
        a save was a dialog closing rather than a new card appearing above.

        Built once and reused. Edit loads a profile into these same fields,
        so there is one place that knows what a model is, rather than a card
        and a dialog between them.
        """
        card, column = frame("createCard")
        column.setContentsMargins(22, 20, 22, 20)
        self.model_form_title = label("Connect a model", "heading")
        column.addWidget(self.model_form_title)
        column.addWidget(label(
            "Your model runs on this device. Its key stays in your OS "
            "credential store.", "muted", True))
        column.addSpacing(12)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.model_name = QLineEdit()
        form.addRow("Agent name", self.model_name)
        self.model_provider = Select()
        for key, info in PROVIDER_NAMES.items():
            self.model_provider.addItem(info[0], key)
        form.addRow("Provider", self.model_provider)
        self.model_id = Select()
        self.model_id.setEditable(True)
        self.model_id.lineEdit().setPlaceholderText("Model ID, e.g. llama3.2")
        self.model_models_button = action("Find models", self.find_models)
        self.model_models_button.setToolTip("Refresh the available models from this provider")
        self.model_id.setToolTip("Choose an available model, or enter a model ID")
        self.model_id.setMaxVisibleItems(20)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_id, 1)
        model_row.addWidget(self.model_models_button)
        form.addRow("Model", model_row)
        self.model_base = QLineEdit()
        form.addRow("API root", self.model_base)
        self.model_key = QLineEdit()
        self.model_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_key.setPlaceholderText("API key · leave blank to keep a saved key")
        form.addRow("API key", self.model_key)
        self.model_catalog_status = label("", "muted", True)
        form.addRow("", self.model_catalog_status)
        self.model_system = QPlainTextEdit()
        self.model_system.setMaximumHeight(90)
        self.model_system.setPlaceholderText("Optional instructions for this agent")
        form.addRow("Instructions", self.model_system)
        column.addLayout(form)

        self.model_purpose = ModelPurpose()
        column.addWidget(self.model_purpose)
        self.model_autostart = TickCheckBox("Start this agent automatically when the app opens")
        self.model_autostart.setChecked(True)
        self.model_autostart.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_autostart, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_insecure = TickCheckBox("Allow a provider's HTTP endpoint on a trusted LAN")
        self.model_insecure.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_insecure, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_vision = TickCheckBox("Enable image support for this model")
        self.model_vision.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_vision, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "Select a vision-capable model to understand images and scanned "
            "PDFs. Text PDFs work with any model.", "muted", True))

        column.addWidget(label("Internet access", "heading"))
        self.model_internet = TickCheckBox("Allow this model to search the web")
        self.model_internet.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_internet, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_search_settings = QWidget()
        search_layout = QVBoxLayout(self.model_search_settings)
        search_layout.setContentsMargins(0, 0, 0, 0)
        self.model_search_form = QFormLayout()
        self.model_search_provider = Select()
        self.model_search_provider.addItem("Ollama web search · no server setup", "ollama")
        self.model_search_provider.addItem("SearXNG · use your own server", "searxng")
        self.model_search_mode = Select()
        self.model_search_mode.addItem(
            "Automatic · when the model needs outside information", "auto")
        self.model_search_mode.addItem("Always · search for every question", "always")
        self.model_search_url = QLineEdit()
        self.model_search_url.setPlaceholderText(
            "https://your-server.example.com or http://localhost:8888")
        self.model_search_key = QLineEdit()
        self.model_search_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_search_key.setPlaceholderText(
            "Ollama account key · leave blank to keep a saved search key")
        self.model_search_form.addRow("Search provider", self.model_search_provider)
        self.model_search_form.addRow("Search mode", self.model_search_mode)
        self.model_search_form.addRow("Search API key", self.model_search_key)
        self.model_search_form.addRow("SearXNG server", self.model_search_url)
        search_layout.addLayout(self.model_search_form)
        self.model_search_insecure = TickCheckBox("Allow SearXNG over HTTP on a trusted LAN")
        self.model_search_insecure.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        search_layout.addWidget(self.model_search_insecure, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_search_help = label("", "muted", True)
        self.model_search_help.setTextFormat(Qt.TextFormat.RichText)
        self.model_search_help.setOpenExternalLinks(True)
        search_layout.addWidget(self.model_search_help)
        self.model_search_test = action("Test web search", self.test_web_search)
        self.model_search_testing = False
        search_layout.addWidget(self.model_search_test)
        self.model_search_status = label("", "muted", True)
        self.model_search_status.setWordWrap(True)
        search_layout.addWidget(self.model_search_status)
        search_layout.addWidget(label(
            "Automatic mode lets the model request a search for current or "
            "uncertain facts. Some models may miss when a search is needed; "
            "choose Always to search before every answer. Queries go to the "
            "search provider you select, and source links are included in the "
            "answer.", "muted", True))
        column.addWidget(self.model_search_settings)

        # Keyed model:connect, so a save failure belongs to this card alone
        # and survives the poll that rebuilds the grid below it.
        self.model_error_line = self.message_line("model:connect")
        column.addWidget(self.model_error_line)
        self.model_save_button = action("Connect agent", self.save_model, True)
        column.addWidget(self.model_save_button)

        # None means connecting a new model rather than editing one, which
        # decides whether the provider change may rename the field.
        self.model_profile_id = None
        self.model_discovery_generation = 0
        self.model_discovery_timer = QTimer(self)
        self.model_discovery_timer.setSingleShot(True)
        self.model_discovery_timer.setInterval(500)
        self.model_discovery_timer.timeout.connect(lambda: self.find_models(automatic=True))
        self.model_catalog_timer = QTimer(self)
        self.model_catalog_timer.setInterval(5000)
        self.model_catalog_timer.timeout.connect(self.refresh_ollama_catalog)
        self.model_catalog_timer.start()
        self.model_provider.currentIndexChanged.connect(self.change_model_provider)
        for signal in (self.model_base.textChanged, self.model_key.textChanged, self.model_insecure.toggled):
            signal.connect(self.schedule_model_discovery)
        self.model_error_fields = [self.model_name, self.model_provider, self.model_id, self.model_base,
                                  self.model_key, self.model_purpose.purpose, *self.model_purpose.tasks.values(),
                                  self.model_search_url, self.model_search_key]
        for field in self.model_error_fields:
            signal = (field.textChanged if isinstance(field, (QLineEdit, QPlainTextEdit)) else
                      field.currentTextChanged if isinstance(field, Select) else field.toggled)
            signal.connect(lambda *args, widget=field: self.clear_field_error(widget))
        self.model_internet.toggled.connect(self.change_model_internet)
        self.model_search_provider.currentIndexChanged.connect(
            self.change_model_internet)
        for signal in (self.model_search_url.textChanged,
                       self.model_search_key.textChanged,
                       self.model_search_insecure.toggled,
                       self.model_search_provider.currentIndexChanged,
                       self.model_internet.toggled):
            signal.connect(lambda *args: self.model_search_status.setText(""))
        self.change_model_provider()
        self.change_model_internet()
        return card

    def chat_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        self.chat_header, header = frame("chatHeader")
        header.setContentsMargins(22, 18, 22, 12)
        header.setSpacing(4)
        column.addWidget(self.chat_header)
        self.chat_title = label("Choose an agent", "heading")
        heading = QHBoxLayout()
        heading.addWidget(self.chat_title, 1)
        self.conversation_picker = Select()
        self.conversation_picker.setMinimumWidth(0)
        self.conversation_picker.setMaximumWidth(300)
        self.conversation_picker.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.conversation_picker.setToolTip("Switch between saved and shared conversations")
        self.conversation_picker.setAccessibleName("Conversation")
        self.conversation_picker.activated.connect(self.choose_conversation)
        self.conversation_picker.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.conversation_picker.customContextMenuRequested.connect(self.show_conversation_menu)
        heading.addWidget(self.conversation_picker)
        header.addLayout(heading)
        self.chat_subtitle = label("Start a conversation with a connected device.", "muted")
        header.addWidget(self.chat_subtitle)
        self.head_status = label("Head: Automatic · /help for commands", "muted", True)
        self.head_status.setToolTip("Use /set @agent as Head to choose the workspace's coordinator")
        header.addWidget(self.head_status)
        self.share_conversation_button = action("Invite to conversation", self.invite_conversation)
        self.share_conversation_button.setToolTip(
            "Invite another device into this conversation")
        chat_actions = QHBoxLayout()
        self.chat_agent_count = action("0 agents", self.show_chat_agents, name="ghost")
        self.chat_agent_count.setToolTip(
            "Show the agents taking part in this conversation")
        chat_actions.addWidget(self.chat_agent_count)
        chat_actions.addWidget(self.share_conversation_button)
        chat_actions.addStretch()
        # Managing the conversation is one button that opens the same menu a
        # right-click gives, rather than a button per action. Two text buttons
        # needed about 485px between them, the panel is 1020px wide, and the
        # rest of this row already wanted 918: the row overflowed and Qt laid
        # the buttons out past the edge of the window, where they looked fine
        # and could not be clicked. A menu also has room to spell the actions
        # out, and it is where the context menu already put them.
        self.manage_button = action("Manage", self.show_manage_menu, name="ghost")
        self.manage_button.setToolTip(
            "Clear or delete this conversation, and what else it can be given")
        chat_actions.addWidget(self.manage_button)
        header.addLayout(chat_actions)
        self.messages_scroll = QScrollArea()
        self.messages_scroll.setWidgetResizable(True)
        self._messages_follow_end = True
        self._rendering_messages = False
        self._restoring_message_scroll = False
        self.message_scroll_timer = QTimer(self)
        self.message_scroll_timer.setSingleShot(True)
        self.message_scroll_timer.timeout.connect(self.scroll_to_latest_message)
        self.messages_scroll.verticalScrollBar().rangeChanged.connect(self.queue_message_scroll)
        self.messages_scroll.verticalScrollBar().valueChanged.connect(self.remember_message_scroll)
        self.messages_widget = QWidget()
        self.messages = QVBoxLayout(self.messages_widget)
        # Inset here rather than on the column, because the column no longer has
        # margins of its own: the header needs to reach the panel's edges for
        # its own surface and hairline to read as an edge.
        self.messages.setContentsMargins(22, 16, 17, 16)
        self.messages.setSpacing(16)
        self.messages.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.messages_scroll.setWidget(self.messages_widget)
        # A bubble that hugs its text has to be told where to stop, and the
        # ceiling moves with the panel. Recapped on every viewport resize
        # rather than only on render, so dragging the window narrower wraps
        # the next message instead of leaving it running off the edge.
        self.messages_scroll.viewport().installEventFilter(self)
        column.addWidget(self.messages_scroll, 1)
        self.composer = Composer()
        self.composer.setPlaceholderText("Send a message, @mention someone, or type /help")
        self.composer.setMaximumHeight(105)
        self.composer.submitted.connect(self.send_message)
        self.attachment_rows = QVBoxLayout()
        self.attachment_rows.setSpacing(6)
        # The composer already sat outside the scrolling area, so it did not
        # move; what it lacked was any sign that it was a different surface
        # from the messages above it. A hairline is what says that.
        self.composer_bar, composer_column = frame("composerBar")
        composer_column.setContentsMargins(22, 12, 22, 14)
        composer_column.setSpacing(8)
        self.command_panel, command_column = frame("card")
        command_heading = QHBoxLayout()
        command_heading.addWidget(label("Workspace commands", "heading"), 1)
        # A cross rather than the word "Dismiss". Written out beside the heading
        # it read as the only way out of the panel, and every other surface in
        # this app that can be closed -- the toast, an error line -- closes with
        # the same glyph, so the word made this one read as a different kind of
        # thing.
        self.command_dismiss = action("×", self.command_panel.hide, name="ghost")
        self.command_dismiss.setFixedSize(31, 31)
        self.command_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.command_dismiss.setToolTip("Dismiss")
        self.command_dismiss.setAccessibleName("Dismiss workspace commands")
        # Declared rather than left on #ghost. Ghost carries 9px/12px of
        # padding for a text button, which makes its size hint 39px wide --
        # so a 31px box clipped the cross and drew half a glyph. Padding and a
        # border belong on a button with a word in it; this one has a glyph.
        # The colour is the ordinary muted text rather than the error red the
        # toast's cross uses, because this panel is a neutral card and a red
        # close would read as an error in it.
        self.style_command_dismiss()
        command_heading.addWidget(self.command_dismiss)
        command_column.addLayout(command_heading)
        self.command_scroll = QScrollArea()
        self.command_scroll.setWidgetResizable(True)
        self.command_scroll.setMinimumHeight(100)
        self.command_scroll.setMaximumHeight(210)
        command_column.addWidget(self.command_scroll)
        self.command_text = ""
        self.command_panel.hide()
        composer_column.addWidget(self.command_panel)
        composer_column.addLayout(self.attachment_rows)
        composer_column.addWidget(self.composer)
        footer = QHBoxLayout()
        self.attach_button = action("Attach images / PDFs", self.attach_files)
        # The hint beside this button states the keys but not what the button
        # itself accepts, and only agents with vision can read an image.
        self.attach_button.setToolTip(
            "Attach images or PDFs. An image is only read by an agent with vision enabled.")
        footer.addWidget(self.attach_button)
        # This hint used to be a plain unwrapped label, so its whole line was a
        # hard minimum width for the composer, and the composer's minimum was
        # the chat panel's minimum: 1111px inside a panel that is 1020px. The
        # panel then laid its header out at the wider figure, which put anything
        # after the header's stretch past the right edge of the window, where
        # the conversation buttons sat visible and unclickable. Wrapping lets it
        # shrink to its longest word instead of to its whole sentence.
        self.composer_hint = label("/help · @ to mention · Enter to send · Shift + Enter for a new line", "muted", True)
        self.composer_hint.setWordWrap(True)
        self.composer_hint.setMinimumWidth(120)
        footer.addWidget(self.composer_hint)
        footer.addStretch()
        self.send_button = action("Send request  ↑", self.send_message, True)
        self.send_button.setToolTip("Send this message to the selected agent")
        footer.addWidget(self.send_button)
        composer_column.addLayout(footer)
        self.composer.textChanged.connect(self.update_chat_controls)
        column.addWidget(self.composer_bar)
        layout.addWidget(panel, 1)
        return page

    def providers_page(self):
        """Build imported model controls and provider connection cards with brand logos."""
        page, layout = self.scroll_page()
        layout.addWidget(label("CHOOSE YOUR INTELLIGENCE", "eyebrow"))
        layout.addWidget(label("Models", "title"))
        layout.addWidget(label("Configure a provider once. The app starts its agent for you, every time.", "muted"))
        layout.addWidget(label("Imported models", "heading"))
        self.imported_model_rows = QVBoxLayout()
        self.imported_model_rows.setSpacing(10)
        layout.addLayout(self.imported_model_rows)
        layout.addWidget(label("Connect another model", "heading"))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.provider_logos = {}
        providers = provider_names(self.theme)
        for index, provider in enumerate(providers):
            card, column = frame("card")
            column.setContentsMargins(21, 20, 21, 20)
            info = providers[provider]
            logo = provider_logo(provider, self.theme)
            self.provider_logos[provider] = logo
            column.addWidget(logo)
            column.addWidget(label(info[0], "heading"))
            column.addWidget(label(info[1], "muted", True))
            column.addSpacing(10)
            # Ten of these, one per provider, all with the same text. A screen
            # reader hears "Connect model, button" ten times and cannot tell
            # them apart; the logo beside it is named, so the button should be.
            connect = action("Connect model  →", lambda checked=False, key=provider: self.add_agent(key), True)
            connect.setAccessibleName(f"Connect a {info[0]} model")
            column.addWidget(connect)
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch()
        return page

    def resources_page(self):
        """Live CPU, memory, disk and process usage.

        Sampling stops while this page is off screen. Polling a panel
        nobody is looking at would spend the app's own CPU to display
        nothing, which is self-defeating on a page about CPU usage.
        """
        page, layout = self.scroll_page()
        layout.addWidget(label("WHAT YOUR MACHINE IS DOING", "eyebrow"))
        layout.addWidget(label("Resources", "title"))
        layout.addWidget(label("Live readings from this device. Nothing is sent anywhere.", "muted", True))
        layout.addWidget(label("Device totals include all programs and local model runtimes such as Ollama. Multiplayer AI's own usage is shown separately below. Models hosted by a provider use that provider's hardware.", "muted", True))

        self.resource_timer = QTimer(self)
        self.resource_timer.setInterval(2000)
        self.resource_timer.timeout.connect(self.refresh_resources)

        def meter(key, title, subtitle):
            """Add a labeled usage card and return its reading label and progress bar."""
            card, column = frame("stat")
            column.setContentsMargins(18, 15, 18, 15)
            column.addWidget(label(title, "muted"))
            reading = label("—", "statValue")
            column.addWidget(reading)
            detail = label(subtitle, "muted", True)
            self.resource_details[key] = detail
            column.addWidget(detail)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setAccessibleName(title)
            column.addSpacing(6)
            column.addWidget(bar)
            layout.addWidget(card)
            return reading, bar

        self.resource_details = {}
        self.resource_rows = {
            "cpu": meter("cpu", "Processor · entire device", "Combined CPU load from all programs"),
            "memory": meter("memory", "Memory (RAM) · entire device", "RAM used by all programs and the operating system"),
            "disk": meter("disk", "Storage · app data drive", "Disk space used by all files on this drive; not disk activity"),
        }

        layout.addWidget(label("CPU load per logical processor · all programs", "heading", True))
        self.core_bars = []
        self.core_labels = []
        self.core_grid = QGridLayout()
        self.core_grid.setSpacing(10)
        layout.addLayout(self.core_grid)

        detail, column = frame("card")
        column.setContentsMargins(20, 18, 20, 18)
        column.addWidget(label("Multiplayer AI · this app process", "heading"))
        column.addWidget(label("Includes the desktop interface and its relay. Separate model processes, such as Ollama, are included in the device totals above.", "muted", True))
        self.app_detail = label("", "muted", True)
        self.app_detail.setWordWrap(True)
        column.addWidget(self.app_detail)
        layout.addWidget(detail)

        self.system_detail = label("", "muted", True)
        self.system_detail.setWordWrap(True)
        layout.addWidget(self.system_detail)
        layout.addWidget(action("Refresh now", self.refresh_resources))
        layout.addStretch()
        return page

    def cookbook_page(self):
        """Which models this device's memory can hold.

        The panel owns its own scroll area, because it is long enough to need
        one and short enough that measuring it first would only delay the
        first paint. Built once and then left alone: every page here is built
        at start-up and re-read on demand, and a catalogue does not change
        under the user.
        """
        from .cookbook_panel import CookbookPanel
        panel = CookbookPanel(self, self.scroll_page)
        self.cookbook_panel = panel
        return panel

    def settings_page(self):
        """Appearance, motion, and where preferences live."""
        page, layout = self.scroll_page()
        layout.addWidget(label("MAKE IT YOURS", "eyebrow"))
        layout.addWidget(label("Settings", "title"))

        appearance, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Appearance", "heading"))
        column.addWidget(label("Theme", "muted"))
        self.theme_picker = Select()
        for choice in THEME_CHOICES:
            self.theme_picker.addItem(choice[0], choice[1])
        self.theme_picker.setCurrentIndex(self._theme_choice_index())
        self.theme_picker.currentIndexChanged.connect(self.theme_choice_changed)
        column.addWidget(self.theme_picker)
        self.theme_hint = label("", "muted", True)
        self.theme_hint.setWordWrap(True)
        column.addWidget(self.theme_hint)
        layout.addWidget(appearance)

        motion, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Motion", "heading"))
        self.launch_screen_toggle = TickCheckBox("Show the launch screen")
        self.launch_screen_toggle.setChecked(
            self.storage.settings.get("launch_screen", True) is not False)
        self.launch_screen_toggle.toggled.connect(self.launch_screen_changed)
        column.addWidget(self.launch_screen_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "A brief branded screen while the network starts. Reduce "
            "animations below overrides it either way.", "muted", True))
        column.addSpacing(10)
        self.reduce_motion = TickCheckBox("Reduce animations")
        self.reduce_motion.setChecked(self.storage.settings.get("reduce_motion", False))
        self.reduce_motion.toggled.connect(self.motion_changed)
        column.addWidget(self.reduce_motion, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "Turns off page fades and the moving illustration, and the "
            "launch screen with them.", "muted", True))
        layout.addWidget(motion)

        cleanup, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Stored credentials", "heading"))
        self.pending_cleanup = label("", "muted", True)
        self.pending_cleanup.setWordWrap(True)
        self.pending_cleanup.setVisible(False)
        column.addWidget(self.pending_cleanup)
        layout.addWidget(cleanup)

        storage, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Data on this device", "heading"))
        self.storage_path = label(str(self.storage.directory), "muted", True)
        self.storage_path.setWordWrap(True)
        self.storage_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.storage_path)
        column.addWidget(action("Show in file manager", self.open_storage_folder))
        layout.addWidget(storage)
        identity, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Device identity", "heading"))
        column.addWidget(label(
            "Your device holds a private token in the operating system's credential "
            "store. Reset it if you think the token has been exposed. Workspaces "
            "joined with the old identity will need new invitations.",
            "muted", True))
        column.addWidget(action("Reset local identity", self.confirm_reset_identity,
                                name="danger"))
        layout.addWidget(identity)

        about, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("About", "heading"))
        self.about_label = label("", "muted", True)
        self.about_label.setWordWrap(True)
        column.addWidget(self.about_label)
        layout.addWidget(about)
        layout.addStretch()
        return page

    def confirm_reset_identity(self):
        """Ask before discarding the device token.

        The action cannot be undone and any joined workspace stops
        working, so it is confirmed rather than fired on a single click.
        """
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Reset local identity?")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText("Your device will get a new private identity.")
        confirm.setInformativeText(
            "Workspaces joined with the current identity will stop connecting and "
            "will need a new invitation. Connected agents are stopped and restarted.")
        confirm.setStandardButtons(QMessageBox.StandardButton.Cancel |
                                   QMessageBox.StandardButton.Reset)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() == QMessageBox.StandardButton.Reset:
            self.command("reset_identity")

    def open_storage_folder(self):
        """Reveal the preferences directory in the platform's file manager."""
        path = str(self.storage.directory)
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606 - Windows shell open is intended
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self.notice(f"Could not open {path}: {exc}")

    def refresh_resources(self):
        """Take one reading and update every meter."""
        if getattr(self, "sampler", None) is None:
            return
        reading = self.sampler.sample()

        self.resource_rows["cpu"][0].setText(f"{reading['cpu']:.0f}%")
        self.resource_rows["cpu"][1].setValue(int(reading["cpu"]))

        memory = reading["memory"]
        if memory:
            self.resource_rows["memory"][0].setText(f"{memory['percent']:.0f}%")
            self.resource_rows["memory"][1].setValue(int(memory["percent"]))
            self.resource_details["memory"].setText(
                f"{memory['used_human']} used of {memory['total_human']} RAM · {memory['available_human']} available · all programs and the operating system")
        else:
            self.resource_rows["memory"][0].setText("Unavailable")
            self.resource_rows["memory"][1].setValue(0)
            self.resource_details["memory"].setText("System RAM usage is unavailable.")

        disk = reading["disk"]
        if disk:
            self.resource_rows["disk"][0].setText(f"{disk['percent']:.0f}%")
            self.resource_rows["disk"][1].setValue(int(disk["percent"]))
            self.resource_details["disk"].setText(
                f"{disk['used_human']} used of {disk['total_human']} · {disk['free_human']} free\n"
                f"Drive containing {disk['path']} · all files on that drive, not disk activity")
        else:
            self.resource_rows["disk"][0].setText("Unavailable")
            self.resource_rows["disk"][1].setValue(0)
            self.resource_details["disk"].setText("Storage usage is unavailable.")

        cores = reading["per_core"] or []
        while len(self.core_bars) < len(cores):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            index = len(self.core_bars)
            caption = label(f"Logical CPU {index + 1}", "muted")
            bar.setAccessibleName(f"Logical CPU {index + 1} load from all programs")
            core = QWidget()
            column = QVBoxLayout(core)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(caption)
            column.addWidget(bar)
            self.core_labels.append(caption)
            self.core_bars.append(bar)
            self.core_grid.addWidget(core, index // 2, index % 2)
        for index, (caption, bar, value) in enumerate(zip(self.core_labels, self.core_bars, cores)):
            caption.setText(f"Logical CPU {index + 1} · {value:.0f}%")
            bar.setValue(int(value))

        process = reading["process"]
        if process:
            self.app_detail.setText(
                f"App RAM: {process['memory_human']} resident memory\n"
                f"App CPU: {process['cpu']:.0f}% of one logical CPU (can exceed 100% across several CPUs)\n"
                f"App threads: {process['threads']}"
            )
        else:
            self.app_detail.setText("Unavailable")

        self.system_detail.setText(
            f"Uptime {reading['uptime']} · {reading['core_count']} logical cores · "
            f"preferences at {self.storage.directory}"
        )

    def open_documentation(self):
        """Open the bundled offline guide in the browser, reporting unavailable files."""
        path = Path(__file__).resolve().parents[1] / "docs.html"
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.notice("Could not open the documentation. Open docs.html from the app folder in your browser.", error=True)

    def _theme_choice_index(self):
        """Where the stored preference sits in the picker.

        An absent key means follow the system, which is the default entry.
        """
        stored = self.storage.settings.get("theme")
        for index, choice in enumerate(THEME_CHOICES):
            if choice[1] == stored:
                return index
        return 0

    def update_theme_hint(self):
        """Explain what the picker is currently doing."""
        stored = self.storage.settings.get("theme")
        if stored is None:
            detected = system_theme() or "unknown"
            self.theme_hint.setText(
                f"Following your system, which reports {detected}. "
                "Pick a theme to override it."
            )
        else:
            # Name the theme in force. "Your choice is remembered and overrides
            # the system" describes the mechanism and never says what the reader
            # is now looking at, so picking a theme from the list gave no way to
            # confirm which one took effect.
            #
            # Kept no longer than the sentence it replaces. This sits on the
            # Settings page, and a longer hint wraps, grows the card and moves
            # the controls below it -- which is a page-wide layout change
            # arriving as a theme feature.
            picked = next((name for name, value in THEME_CHOICES if value == stored), None)
            self.theme_hint.setText(
                f"{picked} theme, remembered, overriding your system."
                if picked else
                "Your choice is remembered and overrides the system.")

    def theme_choice_changed(self, index):
        """Persist the picked theme and apply it.

        Signals are blocked while the index is set programmatically so
        applying a theme does not write the preference back and fight the
        user's click.
        """
        if index < 0 or index >= len(THEME_CHOICES):
            return
        name = THEME_CHOICES[index][1]
        if name is None:
            # discard() persists under the lock, which is also why removing the
            # preference takes effect at once: otherwise the theme reappears on
            # next launch because nothing else wrote the file yet.
            self.storage.discard("theme")
            self.apply_theme(resolve_theme({}))
        else:
            self.storage.update(theme=name)
            self.apply_theme(name)
        self.update_theme_hint()

    def primary_screen_size(self):
        """Usable area of the screen the window is opening on.

        Read from the screen the window actually lands on rather than the
        primary one, so a window dragged to a second monitor is sized for
        that monitor.
        """
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                return area.width(), area.height()
        except Exception:
            pass
        return None

    def style_toast(self, error=None):
        """Repaint the toast in the current theme's colours.

        ``error`` selects the error foreground so a failure is not
        reported in the success colour. Left as None it keeps the current
        state, because apply_theme() restyles on a theme switch and an
        error toast still on screen must not turn green.
        """
        if error is not None:
            self.toast_error = error
        # Styled on the row, not the label inside it. The row now carries the
        # dismiss button beside the message, and painting only the label would
        # put the button outside the bordered box -- so the toast would look
        # like a panel with a stray control next to it.
        foreground = color(self.theme, "error" if self.toast_error else "toast_fg")
        # The row carries the background, the border and the padding, and
        # deliberately not the colour. Qt propagates a widget's stylesheet to its
        # children and lets it win over the application stylesheet for that
        # subtree, so anything set here shadows #errorDismiss on the dismiss
        # button beside it. The label below sets its own colour for the same
        # reason.
        self.toast_row.setStyleSheet("background:" + color(self.theme, "toast_bg")
                                     + "; border:1px solid " + color(self.theme, "text")
                                     + "; padding:12px 24px;")
        # And the dismiss button declares itself, because being beside the row
        # puts it out of reach of the theme's #errorDismiss rule. Left to it, it
        # fell back to default chrome -- a dark fill, a square border, and 62px
        # of content clipped into the 28px box it is fixed at, so the cross was
        # not visible at all -- while still taking clicks, which is why it looked
        # like a dead space rather than a missing control. Declared here rather
        # than in the theme so it restyles with the toast.
        self.toast_dismiss.setStyleSheet(
            "background: transparent; border: none; border-radius: 4px; "
            "padding: 0; color: " + color(self.theme, "error")
            + "; font-weight: 700; font-size: 14px;")
        # The label sets its own foreground rather than relying on the row's
        # cascading down to it. A QLabel takes its text colour from the
        # palette, so a failure styled on the row alone is painted in the
        # ordinary notice colour -- the one thing this styling is for.
        self.toast.setStyleSheet("color:" + foreground)

    def style_command_dismiss(self):
        """Keep the commands panel's cross in the current theme's colour.

        It carries its own style sheet so the ghost padding -- meant for a
        labelled button -- cannot clip the glyph, and a widget that carries its
        own keeps the colour it was given. apply_theme() has to call this: the
        cross would otherwise carry the theme it was built in for the rest of
        the session, which is the same reason style_toast() is called there.
        """
        self.command_dismiss.setStyleSheet(
            "background: transparent; border: none; border-radius: 4px; "
            "padding: 0; color: " + color(self.theme, "text_nav")
            + "; font-weight: 700; font-size: 14px;")

    def style_placeholders(self):
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(color(self.theme, "text_muted")))
        self.setPalette(palette)

    def apply_theme(self, name):
        """Switch themes live.

        Re-applying the stylesheet is not enough on its own. A widget that
        carries its own stylesheet outranks the application sheet and
        keeps whatever colour it was given, so the hand-styled widgets are
        refreshed here and the visible page is re-rendered. Without the
        re-render, an open conversation would keep the accent colours from
        the theme it was drawn in.
        """
        self.theme = name
        self.setStyleSheet(stylesheet(name))
        self.style_placeholders()
        self.setWindowIcon(app_icon(name))
        self.style_toast()
        self.style_command_dismiss()
        # A card message carries its own colour, so it does not follow the
        # application sheet and would keep the old theme's error colour.
        for line in self.card_lines.values():
            line.set_theme(name)
        self.join_button.set_ink("success")
        refresh_join_icon(self, name)
        self.refresh_avatar()
        if hasattr(self, "welcome_art"):
            self.welcome_art.set_theme(name)
        for button, (_, key) in zip(self.nav_buttons, PAGES):
            button.setIcon(navigation_icon(key, color(name, "text")))
        for provider, logo in self.provider_logos.items():
            logo.setPixmap(provider_pixmap(provider, name))
        if hasattr(self, "theme_picker"):
            self.theme_picker.blockSignals(True)
            self.theme_picker.setCurrentIndex(self._theme_choice_index())
            self.theme_picker.blockSignals(False)
        if hasattr(self, "selected"):
            self.render_agents()
            self.render_messages()
        if hasattr(self, "command_panel") and not self.command_panel.isHidden() and self.command_text:
            self.show_command_result(self.command_text)

    def launch_screen_changed(self, wanted):
        """Store whether to show the launch screen next time.

        A preference rather than a motion setting, so it is stored on its
        own. It only takes effect on the next start; the screen is
        already up or already gone by the time anybody can reach this.
        """
        self.storage.update(launch_screen=bool(wanted))

    def motion_changed(self, reduced):
        self.storage.update(reduce_motion=reduced)
        if reduced and hasattr(self, "page_animation"):
            self.page_animation.stop()
            effect = self.stack.currentWidget().graphicsEffect()
            if effect:
                effect.setOpacity(1.0)
        if hasattr(self, "welcome_art"):
            self.welcome_art.set_moving(not reduced)

    def is_model_agent(self, agent):
        return bool(agent.get("kind") == "model" or agent.get("provider") or agent.get("model") or any(
            member["id"] == agent["id"] and member.get("role") == "Model" for member in self.workspace_meta.get("members", [])))

    def model_agents(self, chat_only=False):
        agents = [agent for agent in self.agents if self.is_model_agent(agent)]
        if chat_only:
            room = self.conversations.get(self.selected)
            target = room["target"] if room else self.selected
            if target == GENERAL_TARGET:
                agents = [agent for agent in agents if agent.get("orchestration_authorized", True)]
                routing = (room or self.chats.get(self.selected, {})).get("routing", {})
                coordinator = general_chat_state(agents, self.workspace_meta.get("coordinator"))["conversation_model"]
                identities = {routing.get("coordinator") or (coordinator["id"] if coordinator else None),
                              *(task["agent_id"] for task in routing.get("assignments", []))}
                if not routing:
                    identities.update(agent["id"] for agent in agents if agent.get("delegation_enabled"))
            else:
                identities = {room["target"], *room["members"]} if room else {self.selected}
            agents = [agent for agent in agents if agent["id"] in identities]
        return agents

    def show_workspace_agents(self):
        self.show_agent_list(False)

    def show_chat_agents(self):
        self.show_agent_list(True)

    def show_agent_list(self, chat_only):
        from .agent_list_dialog import AgentListDialog
        dialog = AgentListDialog(self, chat_only)
        dialog.exec()
        dialog.deleteLater()

    def render_imported_models(self):
        models = self.model_agents()
        signature = (self.workspace_id, json.dumps(models, sort_keys=True))
        if getattr(self, "_model_rows_signature", None) == signature:
            return
        self._model_rows_signature = signature
        clear_layout(self.imported_model_rows)
        if not models:
            self.imported_model_rows.addWidget(label("No models connected yet. Choose a provider below.", "muted", True))
        for agent in models:
            card, row = frame("card", QHBoxLayout)
            copy = QVBoxLayout()
            copy.addWidget(label(agent["id"], "heading"))
            copy.addWidget(label(f"{agent.get('provider') or 'Model'} · {agent.get('model') or 'Configured model'}", "muted", True))
            copy.addWidget(label(self.model_purpose_summary(agent), "muted", True))
            if agent["online"]:
                copy.addWidget(label("Online", "online"))
            elif agent.get("provider") == "ollama" and agent.get("local"):
                copy.addWidget(label("Unavailable · Reconnects automatically when Ollama and the model are ready.", "muted", True))
            else:
                copy.addWidget(label("Offline", "muted"))
            profile = agent.get("profile", {})
            search = profile.get("web_search", "off")
            if profile:
                copy.addWidget(label("Web search: " + {"off": "Off", "auto": "Automatic", "always": "Always"}[search] +
                    (" · Images enabled" if profile.get("vision") else " · Text and PDFs"), "muted"))
            row.addLayout(copy, 1)
            if profile:
                row.addWidget(action("Edit model", lambda checked=False, current=agent: self.edit_agent(current)))
            else:
                row.addWidget(label("Managed on its device", "muted"))
            self.imported_model_rows.addWidget(card)

    def _cookbook_placeholder(self):
        """The Cookbook's slot in the stack until somebody opens it.

        A plain widget rather than None: QStackedWidget needs a real widget at
        every index so that navigate() can select by index and the keyboard
        shortcuts keep pointing at the right page.
        """
        placeholder = QWidget()
        placeholder.setObjectName("cookbookPlaceholder")
        return placeholder

    def _build_cookbook_page(self):
        """Build the Cookbook the first time it is opened, and only then."""
        if self.cookbook_panel is not None:
            return
        placeholder = self.stack.widget(COOKBOOK_PAGE)
        previous = self.stack.currentIndex()
        self.stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self.cookbook_panel = None
        self.stack.insertWidget(COOKBOOK_PAGE, self.cookbook_page())
        self.stack.setCurrentIndex(previous)

    def navigate(self, index):
        """Select a page, update navigation, and sample resources only while visible."""
        if index == COOKBOOK_PAGE:
            self._build_cookbook_page()
        self.sidebar_expanded = False
        self.apply_sidebar_density()
        self.stack.setCurrentIndex(index)
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index)
        self.page_title.setText(PAGE_TITLES[index])
        # Sampling runs only while the Resources page is on screen.
        if getattr(self, "resource_timer", None) is not None:
            if index == RESOURCES_PAGE:
                self.refresh_resources()
                self.resource_timer.start()
            else:
                self.resource_timer.stop()
        if not self.storage.settings.get("reduce_motion"):
            widget = self.stack.currentWidget()
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", widget)
            animation.setDuration(200)
            animation.setStartValue(.25)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.page_animation = animation
            animation.start()

    def clear_agent_search(self):
        """Empty the search box, from the empty state that offers to.

        Clearing it rather than hiding the field: the reader can see that the
        filter was what hid their team, rather than having it disappear and
        leave them guessing.
        """
        self.search.clear()
        self.search.setFocus()

    def render_agents(self):
        """Refresh model listings while retaining unchanged cards and scroll positions."""
        models = self.model_agents()
        for grid, agents in ((self.overview_cards, models[:3]),
                             (self.agent_cards, [agent for agent in models if self.search.text().lower() in f"{agent['id']} {agent.get('model') or ''} {agent.get('provider') or ''}".lower()])):
            signature = (self.workspace_id, self.theme, json.dumps(agents, sort_keys=True))
            if getattr(grid, "render_signature", None) == signature:
                continue
            grid.render_signature = signature
            clear_layout(grid)
            if not agents:
                card, column = frame("card")
                # Matches the agent cards below it in the same grid. It was
                # left at Qt's default, which is 11px against their 18/17 --
                # so the empty state sat in a visibly tighter box than the
                # cards it stands in for, on the one screen where the
                # difference is easiest to notice. The Cookbook fixed the same
                # class of defect on its own page with a CARD_PADDING constant.
                column.setContentsMargins(18, 17, 18, 17)
                # Two different situations, and they are not interchangeable.
                # "Your team is getting ready" is a claim about the reader's
                # own setup, and with a search box open it was being made while
                # a nine-model team was connected and simply did not match what
                # was typed. This is the Cookbook's empty_reason() rule: say
                # which situation this is, never let one stand in for another.
                query = self.search.text().strip()
                if query:
                    column.addWidget(label(f"No agents match “{query}”.", "heading", True))
                    column.addWidget(label(
                        f"{len(self.agents)} are connected. The search looks at names, models and providers.",
                        "muted", True))
                    column.addSpacing(7)
                    clear = action("Clear search", self.clear_agent_search, name="ghost")
                    column.addWidget(clear)
                else:
                    column.addWidget(label("Your team is getting ready.", "heading"))
                    column.addWidget(label("Connect a model or invite a device to collaborate.", "muted", True))
                grid.addWidget(card, 0, 0)
            for index, agent in enumerate(agents):
                card, column = frame("card")
                column.setContentsMargins(18, 17, 18, 17)
                top = QHBoxLayout()
                info = provider_entry(self.theme, agent.get("provider"))
                top.addWidget(provider_logo(agent.get("provider"), self.theme, 28))
                top.addStretch()
                status = label("● Online" if agent["online"] else "● Offline", "online" if agent["online"] else "muted")
                top.addWidget(status)
                column.addLayout(top)
                column.addWidget(label(agent["id"], "heading", True))
                column.addWidget(label(agent.get("model") or info[0], "muted", True))
                column.addWidget(label(self.model_purpose_summary(agent), "muted", True))
                column.addSpacing(7)
                talk = action("Open conversation  →", lambda checked=False, id=agent["id"]: self.select_agent(id))
                talk.setEnabled(agent["online"] or bool(agent.get("can_start")))
                talk.setToolTip(f"Start a conversation with {agent['id']}")
                talk.setAccessibleName(f"Open a conversation with {agent['id']}")
                column.addWidget(talk)
                if agent.get("profile"):
                    row = QHBoxLayout()
                    # A column of identical "Edit" and "Stop" buttons, one pair
                    # per card. Each names the agent it belongs to, for the same
                    # reason the Remove button on a file does.
                    edit = action("Edit", lambda checked=False, data=agent: self.edit_agent(data), name="ghost")
                    edit.setToolTip(f"Edit the settings for {agent['id']}")
                    edit.setAccessibleName(f"Edit the settings for {agent['id']}")
                    row.addWidget(edit)
                    if agent.get("local"):
                        stop = action("Stop", lambda checked=False, id=agent["id"]: self.command("stop_agent", id), name="ghost")
                        stop.setToolTip(f"Stop {agent['id']} on this device")
                        stop.setAccessibleName(f"Stop {agent['id']} on this device")
                        row.addWidget(stop)
                    column.addLayout(row)
                grid.addWidget(card, index // 3 if grid is self.overview_cards else index // 2,
                               index % 3 if grid is self.overview_cards else index % 2)
        self.stat_values[0].setText(str(len(models)))
        self.stat_values[1].setText(str(sum(agent["online"] for agent in models)))
        self.stat_values[2].setText(str(self.request_count))
        self.update_overview_state()
        self.update_chat_controls()
        self.render_imported_models()

    def refresh_conversation_picker(self):
        workspace_chat = self.workspace_chat_id()
        choices = [("Chat", workspace_chat or GENERAL_TARGET)]
        for room in self.conversations.values():
            if room["id"] == workspace_chat:
                continue
            unread = self.chats.get(room["id"], {}).get("unread", 0)
            choices.append((room["title"] + (f" ({unread} new)" if unread else ""), room["id"]))
        included = {target for _, target in choices}
        for target, chat in self.chats.items():
            if workspace_chat and target == GENERAL_TARGET:
                continue
            if target not in included and (chat.get("messages") or target == self.selected):
                unread = chat.get("unread", 0)
                choices.append((target + (f" ({unread} new)" if unread else ""), target))
                included.add(target)
        signature = (self.workspace_id, self.selected, tuple(choices))
        if getattr(self, "_conversation_signature", None) != signature:
            self._conversation_signature = signature
            self.conversation_picker.clear()
            for title, target in choices:
                self.conversation_picker.addItem(title, target)
            self.conversation_picker.setCurrentIndex(max(0, self.conversation_picker.findData(self.selected)))
        self.conversation_picker.setVisible(len(choices) > 1)

    def choose_conversation(self, index):
        target = self.conversation_picker.itemData(index)
        if target:
            self.select_agent(target)

    def forget_conversation(self, target, store=None):
        """Drop a conversation from this device: its chat and its saved rooms.

        ``store`` is another workspace's history store, so a conversation can
        be forgotten in a workspace that is not the one being viewed.

        The room record and the ``("ui","state")`` blob are edited separately
        because they are stored separately: a room is one record of its own,
        while every chat in a workspace shares one blob and has to be read,
        edited and written back.
        """
        if store is None:
            store = self.history_store
        self.conversations.pop(target, None)
        self.chats.pop(target, None)
        if self.selected == target:
            self.selected = None
        if store:
            store.delete("rooms", target)
            state = store.load("ui").get("state", {})
            if state.get("chats", {}).pop(target, None) is not None \
                    or target in state.get("conversations", {}):
                state.get("conversations", {}).pop(target, None)
                if state.get("selected") == target:
                    state["selected"] = None
                store.save("ui", "state", state)
        return target

    def clear_conversation(self, target):
        """Empty a conversation but keep the thread open.

        For a shared conversation this clears the copy on this device only. The
        thread still exists on the relay for everyone else, and comes back the
        next time somebody posts in it, so the confirmation says so rather than
        letting it read as a delete. The room's revision is copied over to keep
        the next poll from rebuilding the messages from the relay straight
        away, which is what it does when the revision has moved on.
        """
        chat = self.chats.get(target)
        if not chat:
            return
        room = self.conversations.get(target)
        chat["messages"] = []
        chat["unread"] = 0
        chat["local_pending"] = False
        if room:
            chat["revision"] = room["revision"]
        if target == self.selected:
            self.render_messages()
        self.render_agents()
        self.persist_history()

    def answered_yes(self, answer):
        """Whether a confirmation dialog was answered Yes.

        Compared with ``!=``, never with ``is not``. A dialog returns a plain
        ``int`` -- 16384 -- while ``QMessageBox.StandardButton.Yes`` is a
        Shiboken flag enum. The two are equal by value and are never the same
        object, so an identity test always said no and the confirmation became a
        no-op: the dialog opened, the answer was given, and the action quietly
        did nothing. Every test of it passed, because they all replaced the
        dialog with one returning the very enum member being compared against.
        """
        return answer == QMessageBox.StandardButton.Yes

    def delete_conversation(self, target):
        """Remove a conversation's history from this device, after asking."""
        room = self.conversations.get(target)
        count = len((self.chats.get(target) or {}).get("messages") or [])
        if room:
            title = room["title"]
            question = (f"Delete this conversation from this device?\n\n"
                        f"Its {count} messages will be removed and cannot be "
                        f"read again.\n\nIt is shared, so it stays on the relay "
                        f"for the other devices and will come back at the next "
                        f"poll. Deleting it for everyone needs the "
                        f"conversation's owner.")
        else:
            title = target
            question = (f"Delete this conversation from this device?\n\n"
                        f"Its {count} messages will be removed and cannot be "
                        f"read again.")
        if not self.answered_yes(QMessageBox.question(
                self, "Delete conversation", f"{title}\n\n{question}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)):
            return
        # Forgot before anything is drawn, because render_messages() ends by
        # saving the history: redrawing first would write the conversation back
        # out again on the way past.
        self.forget_conversation(target)
        self.render_agents()
        self.render_messages()
        self.render_attachments()
        self.persist_history()
        self.notice(f"{count} messages deleted from this device. The conversation is gone from this device.")

    def clear_conversation_confirm(self, target):
        """Ask before emptying a conversation, saying what it will and will not do."""
        room = self.conversations.get(target)
        title = room["title"] if room else target
        count = len((self.chats.get(target) or {}).get("messages") or [])
        if room:
            detail = (f"This clears your copy of the conversation on this device.\n\n"
                      f"Its {count} messages will be removed. It is shared, so "
                      f"they stay on the relay for the other devices and reappear "
                      f"here the next time somebody posts in it.")
        else:
            detail = (f"This removes the {count} messages in this conversation "
                      f"on this device. They cannot be read again.")
        if not self.answered_yes(QMessageBox.question(
                self, "Clear conversation", f"{title}\n\n{detail}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)):
            return
        self.clear_conversation(target)
        self.notice(f"{count} messages cleared. The conversation is still open.")

    def conversation_actions(self, target):
        """What can be done to a conversation, as ``(text, tooltip, callback)``.

        Described rather than built, so the header and the context menu make
        their own controls from one list and cannot drift apart. Each consumer
        builds only what it needs: a button for the header, a menu entry for a
        right-click.

        A shared conversation follows the workspace's own rule: whoever started
        it may end it, and anyone else steps out of it. Delete and leave go to
        the relay first, so what is claimed to the reader is what happens.
        """
        if not target:
            return []
        chat = self.chats.get(target) or {}
        room = self.conversations.get(target)
        entries = []
        if chat.get("messages"):
            entries.append((
                "Clear messages",
                "Empty your copy of this conversation on this device, and keep the thread",
                lambda id=target: self.clear_conversation_confirm(id)))
        if room:
            owned = room["owner"] == self.identity
            entries.append((
                "Delete for everyone" if owned else "Leave conversation",
                "End this conversation for every device in it" if owned
                else "Step out of this conversation, keeping it for the others",
                lambda id=target, remove=owned: self.end_conversation(id, remove)))
        else:
            entries.append((
                "Delete conversation",
                "Remove this conversation and its messages from this device",
                lambda id=target: self.delete_conversation(id)))
        return entries

    def end_conversation(self, target, delete):
        """Ask the relay to end a shared conversation, then forget it here.

        The local copy goes only once the relay has agreed. Forgetting it first
        would leave the row on screen until the next poll brought it back, which
        reads as though the delete had failed.
        """
        room = self.conversations.get(target)
        title = room["title"] if room else target
        count = len((self.chats.get(target) or {}).get("messages") or [])
        if delete:
            question = (f"Delete this conversation for every device in it?\n\n"
                        f"Its {count} messages will be removed from the relay "
                        f"and cannot be read again by anyone.")
        else:
            question = (f"Leave this conversation?\n\n"
                        f"You will stop seeing it, and its {count} messages stay "
                        f"for everyone else in it.")
        if not self.answered_yes(QMessageBox.question(
                self, "Delete conversation" if delete else "Leave conversation",
                f"{title}\n\n{question}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)):
            return
        method = "delete_conversation" if delete else "leave_conversation"
        self.chat_busy = True
        self.update_chat_controls()
        def failed(message):
            self.chat_busy = False
            self.notice(message, error=True)
            self.update_chat_controls()
        def done(result):
            self.chat_busy = False
            self.forget_conversation(target)
            self.render_agents()
            self.render_messages()
            self.render_attachments()
            self.persist_history()
            self.notice("Conversation deleted." if delete
                        else "You have left the conversation.")
        self.command(method, target, success=done, failure=failed)

    def build_conversation_menu(self, target):
        """A menu of what can be done to a conversation, or None if nothing can.

        Built from the same description the header and the right-click both use,
        so there is one answer to what a conversation can be given and it is the
        same in every place it is offered.
        """
        entries = self.conversation_actions(target)
        if not entries:
            return None
        menu = QMenu(self)
        for text, tip, callback in entries:
            entry = menu.addAction(text)
            entry.setToolTip(tip)
            entry.setEnabled(not self.chat_busy)
            entry.triggered.connect(lambda checked=False, run=callback: run())
        return menu

    def show_manage_menu(self):
        """Open the conversation menu from the header, under the button."""
        menu = self.build_conversation_menu(self.selected)
        if menu:
            menu.exec(self.manage_button.mapToGlobal(
                QPoint(0, self.manage_button.height())))

    def show_conversation_menu(self, point):
        """Offer the selected conversation's actions beside the compact picker."""
        picker = self.sender()
        if picker is not self.conversation_picker:
            return
        menu = self.build_conversation_menu(picker.currentData())
        if menu:
            menu.exec(picker.mapToGlobal(point))

    @staticmethod
    def model_purpose_summary(agent):
        profile = agent.get("profile") or agent
        tasks = ", ".join(TASK_TYPES[key] for key in profile.get("tasks", []) if key in TASK_TYPES and key != "general")
        return (profile.get("purpose") or "Ready to chat") + (" · " + tasks if tasks else "") + (
            " · Specialist routing enabled" if profile.get("delegation_enabled") else "")

    def chat_target_agent(self):
        room = self.conversations.get(self.selected)
        target = room["target"] if room else self.selected
        if target != GENERAL_TARGET:
            return next((item for item in self.agents if item["id"] == target), None)
        state = general_chat_state(self.model_agents(), self.workspace_meta.get("coordinator"))
        startable = [model for model in self.model_agents()
                     if model.get("can_start") and model.get("local")
                     and model.get("orchestration_authorized", True)]
        return {"id": GENERAL_TARGET, "kind": "model", "online": state["online"],
                "vision": state["vision"] or any(model.get("vision") for model in startable),
                "can_start": bool(startable),
                "conversation_model": state["conversation_model"]["id"] if state["conversation_model"] else None,
                "coordinator_online": bool(state["coordinator"] and state["coordinator"].get("online")),
                "delegating": state["delegating"], "coordinator": state["coordinator"]["id"] if state["coordinator"] else None}

    def refresh_head_status(self):
        state = general_chat_state(self.model_agents(), self.workspace_meta.get("coordinator"))
        head = state["coordinator"]
        active = state["conversation_model"]
        text = "Head: @" + head["id"] if head else "Head: Automatic"
        if head and not head.get("online"):
            text += " · using @" + active["id"] + " while unavailable" if active else " · Offline"
        self.head_status.setText(text + " · /help for commands")

    def update_chat_controls(self):
        self.refresh_conversation_picker()
        self.refresh_head_status()
        self.composer.set_mentions([agent["id"] for agent in self.agents] +
                                   [member["id"] for member in self.workspace_meta.get("members", [])])
        room = self.conversations.get(self.selected)
        target = room["target"] if room else self.selected
        self.head_status.setVisible(target == GENERAL_TARGET)
        agent = self.chat_target_agent()
        chat = self.chats.get(self.selected, {})
        pending = chat.get("pending") or chat.get("local_pending")
        can_start = bool(agent and agent.get("can_start"))
        text = self.composer.toPlainText()
        typed_action = is_command(text) or bool(leading_mention(text))
        command_pending = self.command_request is not None and self.command_request[0] == self.workspace_id
        self.send_button.setEnabled(bool((typed_action or agent and (agent["online"] or can_start) and not pending)
                                        and not command_pending and not self.preparing_files))
        self.attach_button.setEnabled(bool(self.ready and agent and self.is_model_agent(agent) and not pending and not self.preparing_files))
        count = len(self.model_agents(chat_only=True))
        self.chat_agent_count.setText(f"{count} " + ("agent" if count == 1 else "agents"))
        self.chat_agent_count.setEnabled(bool(self.selected))
        self.send_button.setText("Working…" if pending else "Send request  ↑")
        self.chat_title.setText(room["title"] if room else "Chat" if target == GENERAL_TARGET else self.selected or "Choose an agent")
        subtitle = "Your agent is working…" if pending else "Online · Ready to collaborate" if agent and agent["online"] else "Start this agent on its device to continue" if agent else "Choose a connected agent to begin"
        if agent and not agent["online"] and can_start:
            subtitle = "Starting Ollama…" if pending else "Send a message to start Ollama automatically"
        if target == GENERAL_TARGET:
            if pending:
                subtitle = "Starting Ollama…" if can_start and not agent["online"] else "Your model is working…"
            elif agent["online"]:
                subtitle = "Online · Ready to chat with " + agent["conversation_model"]
            else:
                subtitle = "Send a message to start Ollama automatically" if can_start else "Connect or start a model to chat"
        self.chat_subtitle.setText((f"Shared with {len(room['members'])} devices · " if room else "") + subtitle)
        self.share_conversation_button.setEnabled(bool(self.ready and not self.remote and agent and agent["online"] and not pending))
        # The header's one conversation button is always the same widget. It
        # used to be two, rebuilt from scratch on every agents poll, and the
        # poll threw them away mid-click; and then two text buttons overflowed
        # the panel and were laid out past the edge of the window, where they
        # could not be clicked at all. It is held and only enabled or disabled.
        entries = self.conversation_actions(self.selected)
        self.manage_button.setEnabled(bool(entries) and not self.chat_busy)
        self.manage_button.setToolTip(
            f"Clear or delete this conversation: {len(entries)} "
            + ("action on offer" if len(entries) == 1 else "actions on offer")
            if entries else "There is nothing to do to this conversation yet")

    def workspace_chat_id(self):
        """The shared thread used by the workspace's main Chat entry."""
        return next((room["id"] for room in self.conversations.values() if room.get("workspace_chat")), None)

    def select_agent(self, agent_id):
        if agent_id == GENERAL_TARGET:
            agent_id = self.workspace_chat_id() or agent_id
        self.sidebar_expanded = False
        self.apply_sidebar_density()
        self.selected = agent_id
        self.chats.setdefault(agent_id, {"messages": [], "history": [], "pending": False})
        self.chats[agent_id]["unread"] = 0
        self.navigate(CONVERSATIONS_PAGE)
        self.render_agents()
        self.render_messages()
        self.render_attachments()
        self.composer.setFocus()

    @staticmethod
    def saved_chat(chat):
        return {key: value for key, value in chat.items() if key != "message_ids"}

    def persist_history(self):
        if self.history_store:
            self.history_store.save("ui", "state", {"chats": {key: self.saved_chat(chat) for key, chat in self.chats.items()},
                "conversations": self.conversations, "selected": self.selected, "workspace_info": self.workspace_meta,
                "draft": self.composer.toPlainText()})

    def persist_reply(self, workspace_id, store, target, chat):
        if not any(entry["id"] == workspace_id for entry in self.workspace_list):
            return
        if workspace_id == self.workspace_id:
            self.chats[target] = chat
            self.persist_history()
            if self.selected == target:
                self.render_messages()
        else:
            state = store.load("ui").get("state", {})
            state.setdefault("chats", {})[target] = self.saved_chat(chat)
            store.save("ui", "state", state)

    def restore_history(self, directory):
        self.history_store = HistoryStore(directory)
        state = self.history_store.load("ui").get("state", {})
        self.chats = state.get("chats", {})
        self.conversations = state.get("conversations", {})
        for room in self.conversations.values():
            if room["target"] == GENERAL_TARGET and room["title"] in ("General chat · Jev", "General chat"):
                room["title"] = "Chat"
        self.workspace_meta = state.get("workspace_info", {})
        self.selected = state.get("selected")
        self.composer.setPlainText(state.get("draft", ""))
        self.render_attachments()
        for target, chat in self.chats.items():
            chat["messages"] = [tuple(message) for message in chat.get("messages", [])]
            if (self.workspace_id, target) not in self.live_requests:
                if chat.get("pending") and target not in self.conversations:
                    chat["messages"].append(("error", "The app closed during this request. It was not replayed."))
                chat["pending"] = False
                chat["local_pending"] = False
            if target in self.conversations:
                chat["message_ids"] = {message["id"] for message in self.conversations[target]["messages"]}

    def render_workspaces(self):
        identities = {entry["id"] for entry in self.workspace_list}
        for identity in list(self.workspace_buttons):
            if identity not in identities:
                button = self.workspace_buttons.pop(identity)
                button.animation.stop()
                self.workspace_rail.removeWidget(button)
                button.hide()
                button.deleteLater()
        for entry in self.workspace_list:
            button = self.workspace_buttons.get(entry["id"])
            if button is None:
                button = WorkspaceButton(entry["name"][:2].upper())
                button.setCheckable(True)
                button.clicked.connect(lambda checked=False, identity=entry["id"]: self.command("switch_workspace", identity))
                self.workspace_rail.addWidget(button)
                self.workspace_buttons[entry["id"]] = button
            button.setText(entry["name"][:2].upper())
            button.setToolTip(entry["name"])
            button.setChecked(entry["id"] == self.workspace_id)

    def speaker_avatar(self, speaker, role, provider=None, kind=None):
        """The mark beside a message.

        The provider's own logo when the speaker is a known model agent, so
        a conversation can be read at a glance, and the shared device mark
        otherwise. Both are painted rather than styled, so they follow the
        theme on the same terms as the orbit art.

        The provider is passed in rather than looked up again, because the
        caller has already resolved it and looking it up by the speaker's
        display name fails as soon as that name is the provider's.
        """
        if role == "user":
            return device_avatar(self.theme, self.identity, SPEAKER_AVATAR)
        if provider:
            return provider_logo(provider, self.theme, SPEAKER_AVATAR)
        agent = next((entry for entry in self.agents if entry["id"] == speaker), None)
        if agent and agent.get("provider"):
            return provider_logo(agent["provider"], self.theme, SPEAKER_AVATAR)
        return device_avatar(self.theme, speaker, SPEAKER_AVATAR)

    def copy_message(self, text):
        """Put a message on the clipboard.

        Says so afterwards, because nothing on screen changes when a copy
        succeeds and silence would leave the button looking inert. It is gone
        in a second: this confirms something the user just watched happen,
        rather than telling them something they have to act on.
        """
        QApplication.clipboard().setText(text)
        self.notice("Message Copied", duration=COPY_TOAST_MS)

    def speaker_identity(self, role, room=None, responder=None):
        """Who is speaking, as ``(name, subtitle, provider, kind)``.

        Resolved in one place because it used to be spread across the role map
        and a fallback that labelled a message with whatever conversation
        happened to be open. Two roles fell through that fallback: ``peer``,
        which named another device's message after the reader's own agent, and
        any assistant whose id the reader then had to decode.

        ``kind`` drives the layout rather than the role string, so a peer's
        message and an agent's are told apart by the bubble and not by the
        text alone.

        The subtitle carries the agent's own id when it differs from the
        provider name, which is what tells two Ollama agents apart.
        """
        if role == "user":
            return ("You", "", None, "user")
        if role == "error":
            return ("Request unsuccessful", "", None, "error")
        if role == "assistant" and responder:
            agent_id = responder["agent_id"]
            entry = next((agent for agent in self.agents if agent["id"] == agent_id), {})
            model = responder.get("model") or entry.get("model") or agent_id
            provider = responder.get("provider") or entry.get("provider")
            return (model, agent_id if agent_id != model else "", provider, "agent")
        if role == "assistant" and (room["target"] if room else self.selected) == GENERAL_TARGET:
            return ("Model response", "", None, "agent")
        if role == "local_agent":
            return self.agent_identity(room["target"] if room else self.selected)
        if role.startswith("local_agent:"):
            # This device's own agent answering a peer's request. The responder
            # is named in the role, so it is read from there rather than
            # resolved. The fallback below could not do it: there is no room in
            # a peer conversation, so ``self.selected`` is the peer who asked
            # the question, and the reply came out headed by the asker with the
            # real responder left inline in the body text.
            return self.agent_identity(role.removeprefix("local_agent:"))
        if role == "peer":
            # Another device. It has no provider of ours and no avatar, so it
            # is named by its own identity and tinted as a peer.
            #
            # The sender is the key of the chat the message is in, which is
            # ``self.selected`` whenever this is being rendered: a peer chat is
            # opened by the sender's identity and has no room behind it, since
            # rooms only ever exist for agents. So ``self.selected`` is who
            # wrote it.
            #
            # Deliberately not ``room["target"]``, the way the fallback at the
            # end of this method resolves an agent: that names the local agent
            # the reader is talking to, which would label another device's
            # message with the reader's own agent.
            return (self.selected or "Another device", "", None, "peer")

        speaker = role.removeprefix("member:") if role.startswith("member:") else None
        if speaker is not None:
            return (speaker, "", None, "peer")

        return self.agent_identity(room["target"] if room else self.selected)

    def agent_identity(self, agent_id):
        """An agent's display name, subtitle and provider.

        Falls back to the raw id when the agent is not in the current list,
        which is the honest thing to show rather than a placeholder.
        """
        entry = next((agent for agent in self.agents if agent["id"] == agent_id), None)
        if entry is None:
            return (agent_id, "", None, "agent")
        provider = entry.get("provider")
        name = provider_names(self.theme).get(provider, (agent_id,))[0] if provider else agent_id
        subtitle = agent_id if agent_id and agent_id != name else ""
        return (name, subtitle, provider, "agent")

    def message_bubble_fill(self, provider, kind):
        """The fill behind a message, which is what tells them apart.

        Your own messages get the neutral raised surface. An agent's is tinted
        toward that provider's own accent, so which model is answering reads
        without the name. A failure is tinted with the theme's danger colour
        rather than a provider's, because a failed call is not that model's
        fault to be branded with.
        """
        surface = color(self.theme, "surface")
        if kind == "user":
            return color(self.theme, "surface_raised")
        if kind == "error":
            return color(self.theme, "danger_bg")
        if provider:
            return mix(provider_color(self.theme, provider), surface, 0.14)
        return color(self.theme, "surface")

    def message_title_colour(self, provider, kind):
        if kind == "error":
            return color(self.theme, "error")
        if provider:
            return provider_color(self.theme, provider)
        return color(self.theme, "agent_title")

    def cap_message_widths(self):
        """Give every bubble its own size: as wide as its text, up to a share of the panel.

        Set explicitly rather than left to the layout's size policy. A bubble
        has to be told where to stop or one long line runs off the edge, and
        it has to be told how big it is as well: relying on the box layout
        inside a freshly built row left it at the widget's default 640x480,
        which is the full-bleed slab this replaced.

        The width is ``min(preferred, limit)``: a short reply gets the width of
        its text and reads as a bubble, a long one gets the ceiling and wraps.

        The height is worked out from each child's own idea of its height, not
        read back from the bubble. Both obvious readings are wrong:

        ``column.sizeHint()`` reports whatever the column is currently laid out
        at, and the height used to be pinned from that reading. Once pinned, the
        column's hint reports the pin back, so the pin became its own input and
        the height could never change again. A message laid out at a wide panel
        kept that height, and narrowing the panel re-wrapped the text into a
        bubble far too short for it, cutting off the bottom of the message.

        Reading a child's ``height()`` is wrong too. A plain label in a fresh row
        still has whatever height it was last given, which is the widget
        default, so a one-line title came out hundreds of pixels tall. So each
        kind is asked the way it decides for itself: a Markdown reply works its
        height out from its text, a wrapped label from ``heightForWidth`` at the
        width it has just been given, and a plain label from its size hint.

        The pin goes back on at the end, because the scrolling panel squeezes
        its rows to fit rather than scrolling them, which left every bubble one
        line high. It is set from what the children actually need, so it holds
        the text rather than cutting it.
        """
        available = self.messages_scroll.viewport().width()
        limit = max(240, int(available * 0.74)) if available > 0 else 240
        for bubble in self.message_bubbles:
            width = bubble.preferred_width()
            bubble.setFixedWidth(max(1, min(width if width > 0 else limit, limit)))
            # Released before anything is measured: a fixed height is one of
            # the column's inputs, so a pin left from the last render is what
            # this one would be measured against.
            bubble.setMinimumHeight(0)
            bubble.setMaximumHeight(UNPINNED)
            # The column first, so the body is handed the new width and can set
            # its wrap. Measuring before the layout ran gave the height of one
            # enormous line: 24000px for a paragraph.
            bubble.column.activate()
            margins = bubble.column.contentsMargins()
            children = [bubble.column.itemAt(index).widget()
                        for index in range(bubble.column.count())]
            needed = margins.top() + margins.bottom()
            for child in children:
                refit = getattr(child, "fit_height", None)
                if callable(refit):
                    refit()
                    needed += child.height()
                elif isinstance(child, QLabel) and child.wordWrap():
                    # A wrapped label does not re-wrap on its own once a height
                    # has been fixed on it, so it has to be told.
                    child.setFixedHeight(child.heightForWidth(child.width()))
                    needed += child.height()
                else:
                    needed += child.sizeHint().height()
            needed += bubble.column.spacing() * max(0, len(children) - 1)
            bubble.setFixedHeight(max(1, needed))

    def settle_message_layout(self):
        """Force the chat's layout to run now.

        A render leaves rows at their default size until the event loop gets
        round to them, so anything reading the geometry straight afterwards
        sees an unlaid-out widget. Invalidating alone only resizes the row, it
        does not reach inside it, so each row's own layout is activated too,
        and the bubbles are sized before the pass that places them.
        """
        for row in self.messages_widget.findChildren(HoverRow):
            row.outer.invalidate()
        self.messages.invalidate()
        self.messages.activate()
        self.cap_message_widths()
        for row in self.messages_widget.findChildren(HoverRow):
            row.outer.activate()
            row.content.activate()
            # The stack holding the bubble and the actions under it. Left out,
            # the bubble sits at its default 640x480 geometry until the event
            # loop gets to it, which is the same fault one level down.
            row.message.activate()
            if row.has_actions:
                row.actions.layout().activate()
            for bubble in row.findChildren(MessageBubble):
                # The bubble's own column, or the title and body keep their
                # default 640x480 geometry and the bubble ends up one line
                # tall whatever its size hint says.
                bubble.column.activate()

    def eventFilter(self, watched, event):
        # The filter is installed on the viewport, so ``watched`` is always the
        # viewport and never the scroll widget. Asking for both used to be a
        # test nothing could pass: a widget is never identical to its own
        # viewport, so the branch was dead and bubble widths were never
        # re-capped when the window was resized, which is the whole reason
        # this filter is installed.
        scroll = getattr(self, "messages_scroll", None)
        if scroll is not None and watched is scroll.viewport() \
                and event.type() == QEvent.Type.Resize:
            self.cap_message_widths()
        return super().eventFilter(watched, event)

    def split_legacy_responder(self, role, text):
        """Recover the responder from replies saved before the role carried it.

        Those replies arrived with this device's agent id glued onto the front
        of the text as ``id:\\n``, because there was nowhere else to put it. With
        no room to resolve, the only identity available was the chat's key,
        which is the peer who asked, so those conversations render with the
        asker heading a reply their own agent wrote.

        Split only when that first line is an agent currently in the list. The
        old prefix and an ordinary opening line are both bare words within the
        same character set, so the shape alone cannot tell them apart, and a
        reply that begins "Note:" must not lose its first line. The colon the
        old prefix ended with is kept as a second test, so a first line with no
        colon is never split at all.

        Rendering only: the stored message is left as it was, so saving the
        history again writes the same bytes and this reapplies cleanly.
        """
        if role != "local_agent" or "\n" not in text:
            return role, text
        head, _, rest = text.partition("\n")
        identity = head[:-1] if head.endswith(":") else ""
        if rest and identity and any(agent["id"] == identity for agent in self.agents):
            return "local_agent:" + identity, rest
        return role, text

    def queue_message_scroll(self, *args):
        if self._messages_follow_end:
            self.message_scroll_timer.start(0)

    def remember_message_scroll(self, value):
        if self._rendering_messages or self._restoring_message_scroll or self.message_scroll_timer.isActive():
            return
        self._messages_follow_end = self.messages_scroll.verticalScrollBar().maximum() - value <= 2

    def scroll_to_latest_message(self):
        if self._messages_follow_end:
            self._restoring_message_scroll = True
            bar = self.messages_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._restoring_message_scroll = False

    def render_messages(self):
        self._rendering_messages = True
        self._messages_follow_end = True
        clear_layout(self.messages)
        chat = self.chats.get(self.selected, {})
        if not chat.get("messages"):
            # Two different empty states. Saying "the beginning of your
            # collaboration" while the title above still reads "Choose an
            # agent" told a first-run user they had already started one.
            if self.selected:
                self.messages.addWidget(
                    label("This is the beginning of your collaboration.", "heading", True))
                self.messages.addWidget(
                    label("Ask a question, explore an idea, or simply say hello.", "muted", True))
            else:
                self.messages.addWidget(
                    label("No conversation open", "heading", True))
                self.messages.addWidget(
                    label("Choose a saved conversation above, open an agent from Agents, or connect a "
                          "model from Providers to start one.", "muted", True))
        room = self.conversations.get(self.selected)
        routing = chat.get("routing", {})
        if routing:
            assignments = ", ".join(task["agent_id"] + " (" + TASK_TYPES.get(task["task_type"], task["task_type"]) + ")"
                                    for task in routing.get("assignments", []))
            detail = routing["reason"] if routing.get("mode") in ("direct", "conversation", "fallback") else "Models · " + (assignments or "Model reply") + (
                " · " + routing["reason"] if routing.get("reason") else "")
            self.messages.addWidget(label(detail, "muted", True))
        self.message_bubbles = []
        previous = None
        messages = [self.split_legacy_responder(role, text)
                    for role, text in chat.get("messages", [])]
        # Every speaker, resolved once up front. The run a message belongs to
        # is decided by what comes after it, so the whole history has to be
        # known before the first row can be laid out.
        resolved = []
        for index, (role, _) in enumerate(messages):
            responder = chat.get("responders", {}).get(str(index), {}) if role == "assistant" else {}
            if role == "assistant" and room and not responder and index < len(room["messages"]):
                message = room["messages"][index]
                responder = message.get("responder") or {"agent_id": message["from"]}
            resolved.append(self.speaker_identity(role, room, responder))
        identities = [(name, subtitle, provider, kind)
                      for name, subtitle, provider, kind in resolved]
        for index, (role, text) in enumerate(messages):
            name, subtitle, provider, kind = resolved[index]
            # Discord groups consecutive messages from one speaker under a
            # single header rather than boxing each one. A row per message
            # with a card around it reads as a stack of documents, which is
            # what this used to look like.
            #
            # The identity is the name, the subtitle and the provider together,
            # not the name on its own. Two agents on the same provider are both
            # called "Ollama", so grouping on the name alone tucked one
            # agent's reply under another agent's header and dropped the
            # subtitle that tells them apart.
            identity = identities[index]
            grouped = identity == previous
            # The last message of a run, which is where the actions go. Putting
            # them under every message instead would stack a copy button under
            # each of a run's replies with nothing between them.
            last_of_run = index + 1 >= len(identities) \
                or identities[index + 1] != identity
            mine = kind == "user"
            row = HoverRow(mine=mine)
            row.setProperty("invalid", role == "error")
            if grouped:
                # Hold the place the mark would take, so a follow-on message
                # lines up under the one above instead of sliding across.
                mark = QWidget()
                mark.setFixedWidth(SPEAKER_AVATAR)
            else:
                mark = self.speaker_avatar(name, role, provider, kind)

            bubble = MessageBubble()
            bubble.set_background(self.message_bubble_fill(provider, kind))
            bubble.setAccessibleName(f"{name}: {text[:60]}")
            if not grouped:
                title = label(name)
                title.setStyleSheet("font-weight:650; color:"
                                    + self.message_title_colour(provider, kind) + ";")
                title.setAlignment(Qt.AlignmentFlag.AlignRight if mine
                                   else Qt.AlignmentFlag.AlignLeft)
                bubble.add_content(title)
                if subtitle:
                    detail = label(subtitle, "muted", True)
                    detail.setAlignment(Qt.AlignmentFlag.AlignRight if mine
                                        else Qt.AlignmentFlag.AlignLeft)
                    bubble.add_content(detail)
            # ``local_agent:<id>`` is a reply from an agent too, so it is matched
            # by prefix rather than by equality. Compared exactly, it would stop
            # matching the moment the responder's id rode along in the role, and
            # the reply would quietly fall back to being drawn as raw text with
            # its markdown left in it.
            if role == "assistant" or role.startswith("local_agent"):
                body = MarkdownMessage(text, theme_name=self.theme)
            else:
                body = label(text, wrap=True)
                body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bubble.add_content(body)
            self.message_bubbles.append(bubble)

            # Your messages sit on the right with the mark beside them, an
            # agent's on the left. The stretch is what carries the bubble to
            # that side, so the row itself still spans the panel.
            row.set_message(bubble)
            row.add_mark(mark)

            # Copy belongs to the reply, the way it does in a browser
            # assistant, and only the last of a run carries it. Your own
            # messages have none: there is nothing there to want back, and a
            # button under every question would be a row of them.
            if kind in ("agent", "peer") and last_of_run:
                copy = action("Copy",
                              lambda checked=False, value=text: self.copy_message(value),
                              name="ghost")
                copy.setToolTip("Copy this message")
                row.add_action(copy)
            self.messages.addWidget(row)
            previous = identity
        self.cap_message_widths()
        if chat.get("pending") or chat.get("local_pending"):
            self.messages.addWidget(label("● ● ●   Waiting for your agent…", "muted"))
        if chat.get("send_error") and (not chat.get("messages") or chat["messages"][-1][0] != "error"):
            error_row = HoverRow()
            error_row.setProperty("invalid", True)
            bubble = MessageBubble()
            bubble.set_background(self.message_bubble_fill(None, "error"))
            bubble.add_content(label(chat["send_error"], "errorMessage", True))
            self.message_bubbles.append(bubble)
            error_row.set_message(bubble)
            error_row.add_mark(self.speaker_avatar("Request unsuccessful", "error"))
            self.messages.addWidget(error_row)
        # Force the layout now rather than on the next event-loop turn. A
        # bubble that has not been laid out has no width, so the width cap
        # below it and anything that reads the geometry afterwards would be
        # working from a widget still sitting at its default size.
        self.settle_message_layout()
        self.update_chat_controls()
        self.persist_history()
        self._rendering_messages = False
        # Markdown rows settle their heights after the initial render. Follow
        # subsequent range changes instead of scrolling before layout finishes.
        self.queue_message_scroll()

    def render_attachments(self):
        clear_layout(self.attachment_rows)
        files = self.chats.get(self.selected, {}).get("draft_attachments", [])
        for index, item in enumerate(files):
            row = QHBoxLayout()
            row.addWidget(label(item["name"] + " · " + item["note"], "muted", True), 1)
            remove = action("Remove", lambda checked=False, position=index: self.remove_attachment(position), name="ghost")
            remove.setEnabled(not self.preparing_files)
            # A column of identical "Remove" buttons is ambiguous, so each
            # one names the file it drops.
            remove.setToolTip(f"Remove {item['name']} from this message")
            row.addWidget(remove)
            self.attachment_rows.addLayout(row)

    def remove_attachment(self, position):
        files = self.chats.get(self.selected, {}).get("draft_attachments", [])
        if 0 <= position < len(files):
            files.pop(position)
            self.render_attachments()
            self.persist_history()

    def attach_files(self):
        if not self.selected or not self.attach_button.isEnabled():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach images or PDFs", "",
            "Images and PDFs (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.pdf);;PDF documents (*.pdf);;Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if paths:
            self.prepare_files(paths)

    def prepare_files(self, paths):
        agent = self.chat_target_agent()
        if not agent or not self.is_model_agent(agent) or self.preparing_files:
            return
        workspace_id, target, store = self.workspace_id, self.selected, self.history_store
        self.preparing_files = True
        self.attach_button.setText("Reading files…")
        self.update_chat_controls()
        def finished():
            self.preparing_files = False
            self.attach_button.setText("Attach images / PDFs")
            self.render_attachments()
            self.update_chat_controls()
        def success(prepared):
            if not any(entry["id"] == workspace_id for entry in self.workspace_list):
                finished()
                return
            state = None
            if workspace_id == self.workspace_id:
                chat = self.chats.setdefault(target, {"messages": [], "history": [], "pending": False})
            else:
                state = store.load("ui").get("state", {})
                chat = state.setdefault("chats", {}).setdefault(target, {"messages": [], "history": [], "pending": False})
            combined = [*chat.get("draft_attachments", []), *prepared]
            if len(combined) > 8 or len(json.dumps(combined).encode()) > MAX_MESSAGE_BYTES - 65536:
                self.notice("Attach up to 8 files within the request limit. Send the existing files first.")
            else:
                chat["draft_attachments"] = combined
                try:
                    validate_content([{"type": "text", "text": "Analyze attached files"},
                                      *(part for item in combined for part in item["content"])])
                except ValueError as exc:
                    chat["draft_attachments"] = combined[:-len(prepared)]
                    self.notice(str(exc))
                if state is None:
                    self.persist_history()
                else:
                    store.save("ui", "state", state)
            finished()
        def failed(message):
            finished()
            self.record_chat_error(workspace_id, store, target, message)
        self.command("prepare_attachments", paths, agent.get("vision", False), success=success, failure=failed)

    def show_command_result(self, text):
        """Show local command feedback separately from model and shared history."""
        self.command_text = text
        old = self.command_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.command_output = MarkdownMessage(text, theme_name=self.theme)
        self.command_scroll.setWidget(self.command_output)
        self.command_panel.show()
        if not self.selected:
            self.select_agent(GENERAL_TARGET)

    def submit_chat_command(self, text):
        if self.command_request is not None and self.command_request[0] == self.workspace_id:
            return
        try:
            parsed = parse_command(text)
        except ValueError as exc:
            self.show_command_result(str(exc))
            return
        if parsed.name == "help":
            self.show_command_result(HELP_TEXT)
            self.composer.clear()
            return
        if not self.ready:
            self.show_command_result("Your network is still starting. Try the command again in a moment.")
            return
        request = (self.workspace_id, object())
        self.command_request = request
        self.update_chat_controls()
        def finish():
            if self.command_request == request:
                self.command_request = None
            self.update_chat_controls()
        def success(result):
            finish()
            if self.workspace_id == request[0]:
                self.show_command_result(result["text"])
                if self.composer.toPlainText().strip() == text:
                    self.composer.clear()
        def failure(message):
            finish()
            if self.workspace_id == request[0]:
                self.show_command_result("Command unsuccessful. " + message)
        try:
            self.command("chat_command", text, self.workspace_id, success=success, failure=failure)
        except RuntimeError as exc:
            failure(str(exc))

    def send_message(self):
        text = self.composer.toPlainText().strip()
        if is_command(text):
            self.submit_chat_command(text)
            return
        mention = leading_mention(text)
        recipient_id = None
        if mention and not self.preparing_files:
            try:
                identities = [agent["id"] for agent in self.agents] + [
                    member["id"] for member in self.workspace_meta.get("members", [])]
                identity = resolve_identity(mention[0], identities)
                recipient = next((agent for agent in self.agents if agent["id"] == identity), None)
                if not self.ready:
                    raise ValueError("Your network is still starting. Try again in a moment.")
                if recipient is None:
                    raise ValueError(f"@{identity} is not currently available. Try again in a moment.")
                if not (recipient["online"] or recipient.get("can_start")):
                    raise ValueError(f"@{identity} is offline. Start it on its device before sending a message.")
                source = self.chats.get(self.selected, {})
                attached = source.get("draft_attachments", [])
                text = mention[1]
                if self.is_model_agent(recipient):
                    if source.get("pending") or source.get("local_pending"):
                        raise ValueError("Wait for this conversation's current reply before sending another message.")
                    if not text and not attached:
                        self.show_command_result(f"Add a message after @{identity} to ask it in this conversation.")
                        return
                    recipient_id = identity
                    if not self.selected:
                        self.select_agent(GENERAL_TARGET)
                else:
                    if not text and not attached:
                        self.select_agent(identity)
                        self.composer.clear()
                        return
                    destination = self.chats.get(identity, {})
                    if destination.get("pending") or destination.get("local_pending"):
                        raise ValueError(f"Wait for @{identity}'s current reply before sending another message.")
                    if self.selected != identity and destination.get("draft_attachments"):
                        raise ValueError(f"@{identity}'s conversation has draft attachments. Finish that draft first.")
                    if len(json.dumps(text).encode()) > 180000:
                        raise ValueError("This message is too large. Send a shorter request.")
                    if self.selected != identity:
                        self.chats.setdefault(identity, {"messages": [], "history": [], "pending": False})["draft_attachments"] = attached
                        source["draft_attachments"] = []
                    self.select_agent(identity)
                    self.composer.setPlainText(text)
            except ValueError as exc:
                self.show_command_result(str(exc))
                return
        if not self.selected or not self.send_button.isEnabled():
            return
        if text.startswith("//") and mention is None:
            text = text[1:]
        attached = self.chats[self.selected].get("draft_attachments", [])
        if not text and not attached:
            return
        if not text:
            text = "Please analyze the attached files."
        if len(json.dumps(text).encode()) > 180000:
            self.record_chat_error(self.workspace_id, self.history_store, self.selected,
                                   "This message is too large. Send a shorter request.")
            return
        target = self.selected
        request_target = recipient_id or target
        chat = self.chats[target]
        chat.pop("send_error", None)
        target_profile = dict(next((agent for agent in self.agents if agent["id"] == request_target), {}))
        content = [{"type": "text", "text": text}, *(part for item in attached for part in item["content"])] if attached else text
        try:
            content = validate_content(content)
        except ValueError as exc:
            self.record_chat_error(self.workspace_id, self.history_store, target, str(exc))
            return
        self.composer.setFocus()
        display_text = content_summary(content)
        workspace_id, store = self.workspace_id, self.history_store
        self.live_requests.add((workspace_id, target))
        if target in self.conversations:
            chat["local_pending"] = True
            chat["draft_attachments"] = []
            self.composer.clear()
            self.render_attachments()
            self.request_count += 1
            self.stat_values[2].setText(str(self.request_count))
            self.render_messages()
            def finished(result):
                self.live_requests.discard((workspace_id, target))
                if workspace_id == self.workspace_id and target in self.chats:
                    self.chats[target]["local_pending"] = False
                    self.persist_history()
                    if target == self.selected:
                        self.render_messages()
                elif any(entry["id"] == workspace_id for entry in self.workspace_list):
                    state = store.load("ui").get("state", {})
                    if target in state.get("chats", {}):
                        state["chats"][target]["local_pending"] = False
                        store.save("ui", "state", state)
            def failed(message):
                finished(None)
                chat["send_error"] = message
                self.persist_reply(workspace_id, store, target, chat)
                self.notice(message, error=True)
                self.show_chat_error(workspace_id, target)
            arguments = (target, content, recipient_id) if recipient_id else (target, content)
            self.command("send_conversation", *arguments, success=finished, failure=failed)
            return
        payload = {"messages": model_context([*chat["history"], {"role": "user", "content": content}])} if chat["history"] or attached else {"text": text}
        chat["pending"] = True
        chat.pop("routing", None)
        chat["messages"].append(("user", display_text))
        chat["draft_attachments"] = []
        self.composer.clear()
        self.render_attachments()
        self.request_count += 1
        self.stat_values[2].setText(str(self.request_count))
        self.render_messages()
        def success(result):
            self.live_requests.discard((workspace_id, target))
            response = result.get("text") if isinstance(result, dict) else result
            if not isinstance(response, str):
                response = json.dumps(result, indent=2)
            chat["messages"].append(("assistant", response))
            metadata = result if isinstance(result, dict) else {}
            responder = metadata.get("responder") or {"agent_id": request_target,
                "model": metadata.get("model") or target_profile.get("model") or request_target,
                "provider": metadata.get("provider") or target_profile.get("provider") or "model"}
            chat.setdefault("responders", {})[str(len(chat["messages"]) - 1)] = responder
            if isinstance(result, dict) and result.get("routing"):
                chat["routing"] = result["routing"]
            if recipient_id or isinstance(result, dict) and result.get("provider"):
                chat["history"] = [*chat["history"], {"role": "user", "content": content}, {"role": "assistant", "content": response}]
            chat["pending"] = False
            if workspace_id == self.workspace_id:
                self.add_activity(f"{responder['model']} replied", "Request completed")
            self.persist_reply(workspace_id, store, target, chat)
        def failure(message):
            self.live_requests.discard((workspace_id, target))
            chat["pending"] = False
            chat["messages"].append(("error", message + "\nThe app did not replay this request."))
            self.persist_reply(workspace_id, store, target, chat)
            self.show_chat_error(workspace_id, target)
        self.command("send", request_target, payload, success=success, failure=failure)

    def show_chat_error(self, workspace_id, target):
        """Open the conversation that failed, including after a workspace switch."""
        def reveal(result=None):
            if self.workspace_id == workspace_id:
                self.select_agent(target)
                self.composer.setFocus()
        if workspace_id == self.workspace_id:
            reveal()
        elif any(entry["id"] == workspace_id for entry in self.workspace_list):
            self.command("switch_workspace", workspace_id, success=reveal)

    def record_chat_error(self, workspace_id, store, target, message):
        """Keep input and attachment failures beside their conversation."""
        if workspace_id == self.workspace_id:
            chat = self.chats.get(target)
        elif store:
            chat = store.load("ui").get("state", {}).get("chats", {}).get(target)
        else:
            chat = None
        if chat is not None:
            chat["send_error"] = message
            self.persist_reply(workspace_id, store, target, chat)
            self.show_chat_error(workspace_id, target)

    def add_activity(self, title, detail):
        stamp = datetime.now().strftime("%H:%M")
        self.activity_list.insertItem(0, f"{title}   ·   {stamp}\n{detail}")
        while self.activity_list.count() > 8:
            self.activity_list.takeItem(self.activity_list.count() - 1)

    def notice(self, message, error=False, duration=None, alert=False):
        """Show the toast, then take it away again.

        ``duration`` is how long it stays. It defaults to 7.5s because a
        failure has to be read and acted on, but a copy has already happened:
        it is only confirming something the user just watched succeed, so it
        gets a second and gone. Long enough to notice, short enough not to be
        left sitting across the chat.

        The window's one timer is restarted rather than a new one scheduled, so
        the notice that is actually on screen owns the countdown. Anything else
        and a stale timer from a previous notice hides this one early, which is
        at its worst on a short notice followed by a long one.

        ``alert`` marks a notice that is not a confirmation: something arriving
        from outside rather than a result the user just watched happen. It is
        held back while a failure is standing rather than dropped, and shown
        once that failure is dismissed. Only ``dismiss_notice`` releases it,
        which needs no timer of its own -- an alert is only ever held behind a
        failure, and a failure stops the timer.
        """
        # isHidden and not isVisible: the window is never shown in tests, and
        # more to the point a reader who has the window minimised has the
        # same problem -- the row is on screen in the layout either way.
        if self.toast_error and not error and not self.toast_row.isHidden():
            # A confirmation must not overwrite a failure that is still on
            # screen. Nothing is shown for it: what it confirmed has already
            # visibly happened, and the thing still standing is the one that
            # was not acted on.
            if alert:
                # An alert is not that. A peer has sent something, or the
                # runtime has hit something it cannot continue past, and
                # nobody has been told. Dropping it is what made an incoming
                # message vanish while a failure stood -- the unread badge and
                # the activity log still recorded it, but the prompt that
                # points at it was gone and never came back. Held here instead,
                # and shown when the failure is dismissed.
                #
                # One slot, latest wins. Two alerts arriving behind a single
                # failure keep the most recent; the other is still in the chat
                # and the activity log, which is where it was anyway.
                self._pending_alert = message
            return
        self.style_toast(error)
        self.toast.setText(message)
        # The toast is visual and brief, and there is no live region in Qt to
        # announce it. The accessible name is how a screen reader is told at
        # all, so a failure that nobody heard is the same as a failure that
        # never happened.
        self.toast.setAccessibleName(message)
        if error:
            # The name above is metadata. Without this it is written and never
            # spoken, so a screen reader is still told nothing -- which was the
            # whole reason the message was made to persist. Announced politely
            # rather than asserted, so it does not interrupt whatever the reader
            # is in the middle of, and without moving focus.
            QAccessible.updateAccessibility(
                QAccessibleAnnouncementEvent(self.toast, message))
        self.toast_row.show()
        if error:
            # Errors stand until dismissed or replaced by a later one. Seven
            # and a half seconds is long enough to miss: look away, come back,
            # and the reason the button did nothing is gone.
            self.toast_timer.stop()
        else:
            self.toast_timer.start(TOAST_MS if duration is None else duration)

    def dismiss_notice(self):
        """Take the message away. There is a control for it, not just a timer."""
        self.toast_timer.stop()
        self.toast_row.hide()
        # Whatever was held back while that message stood goes up now, or it
        # was held for nothing. Cleared first so the notice it triggers cannot
        # re-enter here and find it.
        held, self._pending_alert = self._pending_alert, None
        if held is not None:
            self.notice(held, alert=True)

    def command(self, method, *args, success=None, failure=None):
        if not self.ready and method != "close":
            self.notice("Your network is still starting. Please wait a moment.")
            if failure:
                failure("Networking is still starting")
            return
        # Nothing here gates on whether the relay answered, which is worth
        # recording because it was tried. Refusing commands while "offline"
        # looked principled and was not: a single offline event during startup
        # set the flag, and the poll backs off to a five minute ceiling, so the
        # reader could be refused for minutes after the relay was fine again --
        # including on the one command that would have fixed it. A stale network
        # flag is worse than a failed request, because the failed request says
        # what went wrong. What the reader gets instead is the honest status
        # line and a Retry beside it.
        request_id = self.network.submit(method, *args)
        self.callbacks[request_id] = (success, failure)
        self.busy_changed(len(self.callbacks) + 1)

    def busy_changed(self, in_flight):
        """Show that the app is waiting on the network, and what for.

        Every command goes through here, so joining a workspace, switching
        to one and connecting a model all get the same feedback rather than
        only the ones that happened to remember it. Counted rather than
        toggled, because two commands can overlap and the first to finish
        must not clear the indicator while the second is still running.
        """
        if in_flight and not self.busy:
            self.busy = True
            self.progress.show()
            self.busy_label.setText("  Working…")
        elif not in_flight and self.busy:
            self.busy = False
            self.progress.hide()
            self.busy_label.setText("")
        if in_flight:
            self.busy_label.setVisible(True)
        else:
            self.busy_label.setVisible(False)

    @Slot(str, object)
    def command_success(self, request_id, result):
        success, _ = self.callbacks.pop(request_id, (None, None))
        self.busy_changed(len(self.callbacks))
        if success:
            success(result)

    @Slot(str, str)
    def command_failure(self, request_id, message):
        _, failure = self.callbacks.pop(request_id, (None, None))
        self.busy_changed(len(self.callbacks))
        if failure:
            failure(message)
        else:
            self.notice(message, error=True)

    def show_pending_cleanup(self, count, files=0):
        """State, rather than a passing notice, for a cleanup still owed.

        The toast is gone in a few seconds, and this can be true for as
        long as the credential store stays locked or something keeps hold of
        a deleted workspace's folder, so Settings keeps it visible until the
        cleanup actually completes.

        The two are reported separately because they need opposite things:
        an undeleted credential needs the keyring unlocked, a stranded file
        needs whatever is holding the folder closed.
        """
        count, files = int(count or 0), int(files or 0)
        if not hasattr(self, "pending_cleanup"):
            return
        self.pending_cleanup.setVisible(count > 0 or files > 0)
        sentences = []
        if count:
            sentences.append(
                f"{count} saved {'credential' if count == 1 else 'credentials'} from a "
                "deleted workspace could not be removed. Unlock your credential store "
                "and restart; the app will try again.")
        if files:
            sentences.append(
                f"{files} deleted {'workspace' if files == 1 else 'workspaces'}"
                f"{' has' if files == 1 else ' have'} files that could not be removed. "
                "Close anything still using that folder; the app will try again.")
        if sentences:
            self.pending_cleanup.setText(" ".join(sentences))

    @Slot(str, object)
    def network_event(self, event, data):
        if event == "cookbook":
            self.cookbook_state = data
            if self.cookbook_panel is not None:
                self.cookbook_panel.update_local_state(data)
        elif event == "ready":
            self.ready = True
            self.port = data["port"]
            self.progress.hide()
            self.add_button.setEnabled(True)
            self.welcome_connect.setEnabled(True)
            # The Workspaces page holds its buttons disabled until the
            # runtime is up. It refreshed on the agents poll, so opening it
            # in the first second would have shown controls that were not
            # yet usable and gave no sign that they were coming.
            self.render_workspace_page()
            self.invite_button.setEnabled(not self.remote)
            self.workspace_settings_button.setEnabled(True)
            self.update_chat_controls()
            self.add_activity("Your desktop is connected", "Local relay and device identity started automatically")
        elif event == "workspace":
            changed = self.workspace_id != data["id"]
            if changed:
                self.command_panel.hide()
                self.command_text = ""
                self.composer.hide_completion()
                self.persist_history()
                self.workspace_id = data["id"]
                self.restore_history(data["history_directory"])
                self.agents = []
            self.remote = data["remote"]
            self.identity = data["self"]
            self.port = data["port"]
            self.workspace_label.setText(data["name"])
            self.profile_identity.setText(data["self"])
            self.invite_button.setEnabled(self.ready and not self.remote)
            if changed:
                self.request_count = 0
            self.refresh_avatar()
            self.render_agents()
            self.render_messages()
        elif event == "workspaces":
            self.workspace_list = data["workspaces"]
            self.render_workspaces()
        elif event == "workspace_removed":
            self.workspace_list = [entry for entry in self.workspace_list if entry["id"] != data]
            if self.workspace_id == data:
                self.history_store = None
                self.chats.clear()
                self.conversations.clear()
                self.selected = None
        elif event == "workspace_info":
            changed = self.workspace_meta != data
            self.workspace_meta = data
            self.workspace_label.setText(data["name"])
            self.update_chat_controls()
            if changed:
                self.persist_history()
        elif event == "agents":
            self.agents = data["agents"]
            self.set_connection_state(
                "statusOk" if data["connected"] else "statusWarn",
                "●  Relay connected" if data["connected"] else "●  Reconnecting…")
            if not self.selected and any(agent["online"] for agent in self.model_agents()):
                self.select_agent(GENERAL_TARGET)
            else:
                self.render_agents()
            self.render_workspace_page()
        elif event == "conversations":
            changed = set(self.conversations) != {room["id"] for room in data}
            self.conversations = {room["id"]: room for room in data}
            selected_changed = False
            if self.selected in (None, GENERAL_TARGET) and self.workspace_chat_id():
                self.selected = self.workspace_chat_id()
                selected_changed = True
            # A room that is no longer listed is gone: deleted on another
            # device, or the reader was removed from it. This used to leave the
            # matching chat behind, and leave ``selected`` pointing at it, so
            # the conversation stayed in the list and stayed open. Pruned here
            # so a removal made anywhere is reflected everywhere.
            gone = [target for target in self.chats
                    if target.startswith("conversation-") and target not in self.conversations]
            for target in gone:
                self.chats.pop(target, None)
                if self.selected == target:
                    self.selected = None
                    selected_changed = True
                changed = True
            for room in data:
                chat = self.chats.setdefault(room["id"], {"messages": [], "history": [], "pending": False})
                if chat.get("revision") == room["revision"]:
                    continue
                changed = True
                selected_changed = selected_changed or self.selected == room["id"]
                previous_ids = chat.get("message_ids", set())
                new_ids = {message["id"] for message in room["messages"]}
                if self.selected != room["id"]:
                    chat["unread"] = chat.get("unread", 0) + len(new_ids - previous_ids)
                chat["message_ids"] = new_ids
                chat["revision"] = room["revision"]
                chat["messages"] = [("user" if message["role"] == "user" and message["from"] == self.identity
                    else "member:" + message["from"] if message["role"] == "user" else message["role"], content_summary(message["content"]))
                    for message in room["messages"]]
                chat["responders"] = {str(index): message["responder"] for index, message in enumerate(room["messages"])
                                      if message["role"] == "assistant" and message.get("responder")}
                chat["pending"] = room["pending"]
                if room.get("routing"):
                    chat["routing"] = room["routing"]
                else:
                    chat.pop("routing", None)
            self.render_agents()
            if selected_changed:
                self.render_messages()
            elif changed:
                self.persist_history()
        elif event == "conversation_joined":
            self.select_agent(data)
        elif event in ("incoming", "incoming_reply"):
            source = data["from"]
            chat = self.chats.setdefault(source, {"messages": [], "history": [], "pending": False})
            if event == "incoming":
                chat["messages"].append(("peer", data["text"]))
                if self.selected != source:
                    chat["unread"] = chat.get("unread", 0) + 1
                self.add_activity(f"Message from {source}", f"Received by {data['to']}")
                self.notice(f"Message from {source}. Open their conversation to view it.",
                            alert=True)
            else:
                # ``data["to"]`` is this device's own agent, which is the one
                # that answered: ``data["from"]`` is the peer that asked, and
                # is the chat we are filing this under. The responder's id
                # travels in the role rather than glued onto the front of the
                # text, so the reply is headed by whoever actually replied
                # instead of by the peer who asked. A role with an id in it is
                # the same shape as ``member:<id>`` has always used.
                role = "error" if data.get("error") else "local_agent:" + data["to"]
                chat["messages"].append((role, data["text"]))
            self.render_agents()
            self.persist_history()
            if self.selected == source:
                self.render_messages()
        elif event == "activity":
            self.add_activity(data["title"], data["detail"])
        elif event in ("notice", "fatal"):
            # A fatal arrives here as an ordinary notice, and a failure standing
            # on screen used to swallow it. Nothing has been told at that point
            # and the runtime cannot continue, so it is held rather than
            # dropped.
            #
            # Not toasted as a failure, and deliberately: startup_error below
            # is already the lasting surface, styled as an error message and
            # never hidden. Raising the toast to a failure as well would say
            # the same thing twice, and would set toast_error for the rest of
            # the session -- which would suppress every later confirmation,
            # including the ones a user would need in order to try to recover.
            self.notice(data, alert=event == "fatal")
            if event == "fatal":
                self.progress.hide()
                self.set_connection_state("statusBad", "●  Startup needs attention")
                # Stated, not toasted. See startup_error: this is the one event
                # the app cannot undo, and a notice that disappears leaves a
                # window of dead controls with nothing to explain them.
                self.startup_error.setText(str(data))
                self.startup_error.setVisible(True)
                # And announced, because this is the event most worth hearing:
                # the app has stopped and every control it is showing is dead.
                # The label carries it as its accessible name for the same
                # reason a toast does, and is announced politely rather than
                # asserted, so it does not interrupt or move focus.
                self.startup_error.setAccessibleName(str(data))
                QAccessible.updateAccessibility(
                    QAccessibleAnnouncementEvent(self.startup_error, str(data)))
        elif event == "pending_cleanup":
            self.show_pending_cleanup(data["credentials"], data.get("files", 0))
        elif event == "offline":
            self.set_connection_state("statusBad", "●  Relay unavailable")
            for agent in self.agents:
                agent["online"] = False
            for member in self.workspace_meta.get("members", []):
                member["online"] = False
            self.render_agents()

    def change_model_provider(self):
        """Point the form at whichever provider is selected.

        Same rule the dialog used, so the model it can reach and the key it
        needs are decided in one place.
        """
        key = self.model_provider.currentData()
        spec = PROVIDERS[key]
        if self.model_profile_id is None:
            self.model_name.setText(available_agent_name(key, self.agents))
        if self.remote:
            self.model_name.setText(self.identity)
            self.model_name.setReadOnly(True)
        self.model_base.setText(spec.base_url or "")
        self.model_base.setPlaceholderText("https://your-deployment.example.com/v1")
        self.model_models_button.setVisible(True)
        self.model_key.setPlaceholderText(
            "Optional for local Ollama" if not spec.key_required
            else "API key · leave blank to keep a saved key")
        self.model_id.clear()
        self.model_id.clearEditText()
        self.schedule_model_discovery()

    def change_model_internet(self):
        """Show the chosen search provider's fields and allow a test."""
        enabled = self.model_internet.isChecked()
        hosted = self.model_search_provider.currentData() == "ollama"
        self.model_search_settings.setVisible(enabled)
        self.model_search_form.setRowVisible(self.model_search_key, hosted)
        self.model_search_form.setRowVisible(self.model_search_url, not hosted)
        self.model_search_insecure.setVisible(not hosted)
        self.model_search_test.setEnabled(enabled and not self.model_search_testing)
        self.model_search_help.setText(
            f'Get a key from <a href="https://ollama.com/settings/keys" '
            f'style="color: {color(self.theme, "accent")}">your Ollama account</a> '
            'and paste it into Search API key. It works with any connected model, '
            'including local Ollama. The key stays in your OS credential store.'
            if hosted else
            "SearXNG needs a running search server; it is separate from your "
            "model's API root. Enter its address above. The server owner must "
            "enable JSON search results. Choose Ollama web search if you do not "
            "have a server.")

    def model_search_state(self):
        """Everything that would make a test result out of date."""
        return (self.model_search_provider.currentData(), self.model_search_mode.currentData(),
                self.model_search_url.text().strip(), self.model_search_key.text().strip(),
                self.model_search_insecure.isChecked())

    def validate_model_search(self):
        """Check the search settings with the runtime's own rules.

        The rules live in network_a2a.web_search and are used by the adapter
        when the model is actually built, so they are asked here rather than
        restated. Restating them had already drifted in seven places: loopback
        http was refused here while the runtime exempts localhost, and port
        validity, embedded credentials, query strings and fragments were not
        checked at all. The loopback case matters most, because
        ``http://localhost:8888`` is the field's own placeholder and the
        example the runtime's own error message gives.

        One thing is deliberately not delegated. A blank key on a profile that
        already has one means keep the saved key, which is what the field
        promises and what the runtime does when it falls back to the vault.
        The validator cannot know that, and the dialog this replaced had the
        carve-out, so it is kept here.
        """
        if not self.model_internet.isChecked():
            return True
        provider = self.model_search_provider.currentData()
        key = self.model_search_key.text().strip() or None
        if provider == "ollama" and key is None and self.model_profile_id:
            return True
        try:
            validate_search_settings(provider, self.model_search_url.text().strip(),
                                     key, self.model_search_insecure.isChecked())
        except ValueError as exc:
            # The runtime's own sentence, so the page and the adapter cannot
            # disagree about what is wrong with a search address.
            self.set_card_message(self.model_error_line, str(exc))
            self.focus_model_search()
            return False
        return True

    def focus_model_search(self):
        """Put the caret in the search field the problem is about."""
        hosted = self.model_search_provider.currentData() == "ollama"
        (self.model_search_key if hosted else self.model_search_url).setFocus()

    def model_discovery_state(self):
        return (self.workspace_id, self.model_provider.currentData(), self.model_base.text().strip(),
                self.model_key.text(), self.model_insecure.isChecked(), self.model_profile_id)

    def schedule_model_discovery(self, *args):
        """Refresh the dropdown after provider connection settings settle."""
        self.model_discovery_generation += 1
        self.model_discovery_timer.stop()
        self.model_models_button.setEnabled(True)
        selected = self.model_id.currentText()
        self.model_id.clear()
        self.model_id.setCurrentText(selected)
        if not self.model_base.text().strip():
            self.model_catalog_status.setText("Enter the API root to load available models.")
        elif (PROVIDERS[self.model_provider.currentData()].key_required
              and not self.model_key.text().strip() and not self.model_profile_id):
            self.model_catalog_status.setText("Enter your API key to load available models.")
        else:
            self.model_catalog_status.setText("Available models will load from this provider.")
            if self.ready:
                self.model_discovery_timer.start()

    def find_models(self, automatic=False):
        """Load every available model; discard replies for superseded settings."""
        if automatic and self.stack.currentIndex() != AGENTS_PAGE:
            return
        self.model_discovery_timer.stop()
        self.model_discovery_generation += 1
        generation, state = self.model_discovery_generation, self.model_discovery_state()
        self.model_models_button.setEnabled(False)
        self.model_catalog_status.setText("Loading available models…")

        def current():
            return generation == self.model_discovery_generation and state == self.model_discovery_state()

        def success(models):
            if not current():
                return
            self.model_models_button.setEnabled(True)
            selected = self.model_id.currentText()
            self.model_id.clear()
            models = sorted(set(models))
            self.model_id.addItems(models)
            if models and self.model_error_line.message.text() == getattr(self, "_model_catalog_error", None):
                self.clear_card_message(self.model_error_line)
                self._model_catalog_error = None
            if selected and (self.model_provider.currentData() != "ollama" or selected in models):
                self.model_id.setCurrentText(selected)
            elif not models:
                self.model_id.clearEditText()
            self.model_catalog_status.setText(f"{len(models)} models available · Select a model from the dropdown.")
            if not models:
                self.model_catalog_status.setText("No models available from this endpoint.")
                self._model_catalog_error = ("No models found. Install a model in Ollama, then refresh the list."
                                             if self.model_provider.currentData() == "ollama" else
                                             "No models found. Enter your provider's model ID directly.")
                self.set_card_message(self.model_error_line, self._model_catalog_error)

        def failure(message):
            if not current():
                return
            self.model_models_button.setEnabled(True)
            selected = self.model_id.currentText()
            self.model_id.clear()
            if self.model_provider.currentData() == "ollama":
                self.model_id.clearEditText()
            else:
                self.model_id.setCurrentText(selected)
            ollama = self.model_provider.currentData() == "ollama"
            self.model_catalog_status.setText("Ollama is unavailable. Check its installation and API root." if ollama else
                                             "Model discovery failed. You can still enter a model ID directly.")
            if automatic and self.stack.currentIndex() != AGENTS_PAGE:
                return
            self._model_catalog_error = "Could not list models. " + message + (
                " Check the Ollama installation and API root. Local Ollama starts automatically when available." if ollama else
                " Check the API root and key, or enter the model ID directly.")
            self.set_card_message(self.model_error_line, self._model_catalog_error)

        self.command("provider_models", self.model_provider.currentData(),
                     self.model_base.text().strip(), self.model_key.text() or None,
                     self.model_insecure.isChecked(), self.model_profile_id,
                     success=success, failure=failure)

    def refresh_ollama_catalog(self):
        """Remove stale choices when Ollama stops while the import form is open."""
        if (self.ready and self.model_provider.currentData() == "ollama"
                and self.stack.currentIndex() == AGENTS_PAGE
                and self.model_models_button.isEnabled() and not self.model_discovery_timer.isActive()):
            self.find_models(automatic=True)

    def test_web_search(self):
        """Test the configured search service, and report it where it is set."""
        self.clear_card_message(self.model_error_line)
        if not self.validate_model_search():
            return
        self.model_search_testing = True
        self.change_model_internet()
        state = self.model_search_state()
        self.model_search_status.setText("Testing web search…")

        def success(count):
            self.model_search_testing = False
            self.change_model_internet()
            if self.model_search_state() == state:
                self.model_search_status.setText(
                    f"Web search is reachable · {count} results returned" if count else
                    "Connected to the search service, but no results were returned. "
                    "Try again before relying on web search.")

        def failure(message):
            self.model_search_testing = False
            self.change_model_internet()
            if self.model_search_state() == state:
                self.model_search_status.setText(message)
                self.focus_model_search()

        self.command("test_web_search", self.model_search_url.text().strip(),
                     self.model_search_insecure.isChecked(),
                     self.model_search_provider.currentData(),
                     self.model_search_key.text().strip() or None,
                     self.model_profile_id, success=success, failure=failure)

    def save_model(self):
        """Validate and save the profile, with separate model and search keys."""
        self.clear_card_message(self.model_error_line)
        if not self.validate_model_search():
            return
        profile = {"id": self.model_name.text().strip(),
                   "provider": self.model_provider.currentData(),
                   "model": self.model_id.currentText().strip(),
                   "base_url": self.model_base.text().strip(),
                   "system_prompt": self.model_system.toPlainText().strip(),
                   "autostart": self.model_autostart.isChecked(),
                   "allow_insecure": self.model_insecure.isChecked(),
                   "vision": self.model_vision.isChecked(),
                   "web_search": self.model_search_mode.currentData()
                   if self.model_internet.isChecked() else "off",
                   "search_provider": self.model_search_provider.currentData(),
                   "searxng_url": self.model_search_url.text().strip(),
                   "searxng_allow_insecure": self.model_search_insecure.isChecked(),
                   **self.model_purpose.values()}
        connecting = self.model_profile_id is None
        self.model_save_button.setEnabled(False)

        def success(result):
            self.model_save_button.setEnabled(True)
            self.model_form_title.setText("Connect a model")
            self.model_save_button.setText("Connect agent")
            self.reset_model_form()
            if connecting:
                self.select_agent(GENERAL_TARGET)
            else:
                self.navigate(AGENTS_PAGE)
            self.notice(f"{profile['id']} is connected.")

        def failure(message):
            self.model_save_button.setEnabled(True)
            self.set_card_message(self.model_error_line, message)
            if self.model_internet.isChecked() and (
                    "search" in message.lower() or "searxng" in message.lower()):
                self.focus_model_search()

        search_key = (self.model_search_key.text().strip() or None) \
            if self.model_search_provider.currentData() == "ollama" else None
        self.command("save_agent", profile, self.model_key.text() or None,
                     search_key, success=success, failure=failure)

    def clear_model_form(self):
        """Back to a blank connect form.

        One reset for both routes out of a used form. It was only reachable
        from a successful save, so clicking "Connect a model" after editing
        left the edited profile's id, model, instructions and every search
        setting in place with the name field still read-only, and saving that
        silently overwrote the model the reader had just been looking at.

        The search provider, mode and insecure box are reset here too: they
        are chosen once and then persist, which is right while editing and
        wrong for a new agent.
        """
        self.model_key.clear()
        self.model_search_key.clear()
        self.model_system.clear()
        self.model_purpose.load({})
        self.model_search_url.clear()
        self.model_search_status.setText("")
        self.model_name.clear()
        self.model_name.setReadOnly(False)
        # clear() empties a combo box's items but leaves the text of an
        # editable one, which is where the previous model's id was sitting.
        self.model_id.clearEditText()
        self.model_autostart.setChecked(True)
        self.model_insecure.setChecked(False)
        self.model_vision.setChecked(False)
        self.model_internet.setChecked(False)
        self.model_search_insecure.setChecked(False)
        self.model_search_provider.setCurrentIndex(
            max(0, self.model_search_provider.findData("ollama")))
        self.model_search_mode.setCurrentIndex(
            max(0, self.model_search_mode.findData("auto")))
        self.change_model_internet()
        self.clear_card_message(self.model_error_line)

    def reset_model_form(self, provider="ollama"):
        """Blank the form and point it at ``provider``.

        Separated from the clear so add_agent can choose the provider and then
        ask for the provider's own fields to be filled in. setCurrentIndex is a
        no-op when the index is already current and emits nothing, so the
        provider change cannot be relied on to reset anything.
        """
        self.model_profile_id = None
        self.clear_model_form()
        self.model_provider.setCurrentIndex(self.model_provider.findData(provider))
        self.change_model_provider()

    def add_agent(self, provider="ollama"):
        """Open the page's own card, blank, set to this provider.

        Every one of the five entry points lands here now, so connecting a
        model happens in one place. A dialog was a separate window for a form
        that has no reason to be one, and it meant the model you connected
        appeared in a different part of the app from the card that made it.
        """
        if not self.ready:
            self.notice("Your network is still starting. Please wait a moment.")
            return
        if not isinstance(provider, str):
            provider = "ollama"
        self.model_form_title.setText("Connect a model")
        self.model_save_button.setText("Connect agent")
        self.reset_model_form(provider)
        self.navigate(AGENTS_PAGE)
        self.model_name.setFocus()

    def edit_agent(self, agent):
        """Load this model into the same card, so there is one form."""
        if not self.ready:
            self.notice("Your network is still starting. Please wait a moment.")
            return
        profile = agent.get("profile") or {}
        self.model_profile_id = profile.get("id") or agent["id"]
        self.model_form_title.setText(f"Edit {self.model_profile_id}")
        self.model_save_button.setText("Save changes")
        self.model_provider.setCurrentIndex(
            self.model_provider.findData(agent.get("provider") or "ollama"))
        self.change_model_provider()
        self.model_name.setText(self.model_profile_id)
        self.model_name.setReadOnly(True)
        self.model_id.setCurrentText(profile.get("model") or "")
        self.model_base.setText(profile.get("base_url") or "")
        self.model_system.setPlainText(profile.get("system_prompt") or "")
        self.model_purpose.load(profile)
        self.model_autostart.setChecked(profile.get("autostart", True))
        self.model_insecure.setChecked(profile.get("allow_insecure", False))
        self.model_vision.setChecked(profile.get("vision", False))
        self.model_search_provider.setCurrentIndex(max(0, self.model_search_provider.findData(
            profile.get("search_provider", "searxng"))))
        self.model_internet.setChecked(profile.get("web_search", "off") != "off")
        self.model_search_mode.setCurrentIndex(max(0, self.model_search_mode.findData(
            profile.get("web_search", "auto"))))
        self.model_search_url.setText(profile.get("searxng_url", ""))
        self.model_search_insecure.setChecked(profile.get("searxng_allow_insecure", False))
        self.change_model_internet()
        self.navigate(AGENTS_PAGE)
        self.schedule_model_discovery()
        self.model_id.setFocus()

    def set_connection_state(self, state, text):
        """Name the connection state as well as writing what it says.

        This label used to be created as "online" and have only its text
        replaced, so "Startup needs attention" and "Relay unavailable" were both
        painted in the success green. The line is always on screen and it is the
        only status the app offers at a glance, so it was reporting a failure in
        the colour of a success.

        Colour is what carries the meaning here: the leading glyph does not
        change, and the words need reading rather than glancing at. So the state
        is a name the stylesheet can match, and it is changed with unpolish and
        polish, because Qt does not re-match a selector when only the object
        name moved -- the same three lines set_field_error uses, for the same
        reason.

        The Retry button appears only when the relay has been heard from and
        then lost, which is the one state where waiting is not what the reader
        should do. During startup and after a fatal there is nothing to retry
        yet, and a button that does nothing is worse than none.
        """
        self.connection_status.setObjectName(state)
        self.connection_status.style().unpolish(self.connection_status)
        self.connection_status.style().polish(self.connection_status)
        self.connection_status.setText(text)
        self.connection_retry.setVisible(state == "statusBad" and self.ready)

    def retry_connection(self):
        """Ask the runtime to poll again now, rather than waiting out the backoff.

        The poll backs off to a five minute ceiling while the relay is gone, so
        a host that comes back would otherwise stay unnoticed for minutes after
        the reader has put it right. This does not bypass the backoff for the
        background loop; it runs one pass now.

        This is the whole of the answer to "there is no retry in the app". What
        it deliberately does not do is refuse commands while offline: see
        command().
        """
        self.network.submit("retry_poll")
        self.set_connection_state("statusWarn", "●  Reconnecting…")

    def set_field_error(self, widget, invalid):
        widget.setProperty("invalid", invalid)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def clear_field_error(self, widget):
        if widget.property("invalid"):
            self.set_field_error(widget, False)
            if not any(field.property("invalid") for field in self.model_error_fields):
                self.clear_card_message(self.model_error_line)

    def highlight_model_error(self, message):
        """Mark the relevant input and scroll it into view on the Agents page."""
        lower = message.lower()
        lower = lower.replace("check the api root and key, or enter the model id directly.", "")
        if "search" in lower or "searxng" in lower:
            field = self.model_search_key if self.model_search_provider.currentData() == "ollama" else self.model_search_url
        elif "name" in lower or "identity" in lower:
            field = self.model_name
        elif "purpose" in lower or "permitted task" in lower:
            field = self.model_purpose.purpose if not self.model_purpose.purpose.toPlainText().strip() else next(iter(self.model_purpose.tasks.values()))
        elif "key" in lower or "401" in lower or "403" in lower or "authentication" in lower:
            field = self.model_key
        elif "no models" in lower or "model id" in lower or "model" in lower and "list models" not in lower:
            field = self.model_id
        elif any(word in lower for word in ("api root", "url", "endpoint", "connect", "http", "list models")):
            field = self.model_base
        else:
            field = self.model_save_button
        self.set_field_error(self.model_card, True)
        self.set_field_error(field, True)
        self.navigate(AGENTS_PAGE)
        field.setFocus()
        page = self.stack.widget(AGENTS_PAGE)
        page.ensureWidgetVisible(field, 24, 36)
        QTimer.singleShot(0, lambda: page.ensureWidgetVisible(field, 24, 36))

    def name_rule(self):
        """The one wording for the workspace-name limit, used in both places."""
        return "Use a workspace name with 1-80 characters"

    def set_card_message(self, line, message, tone="error"):
        """Put a message on a card's own line, in the window's state.

        The line widget is only the view. Holding the message here is what
        lets it outlive the rebuild of the card it sits on, and keying it by
        the line means no card can overwrite another's message.
        """
        self.card_messages[line.key] = (message, tone)
        line.set_tone(tone)
        line.show_message(message)
        if line is getattr(self, "model_error_line", None) and tone == "error" and message:
            self.highlight_model_error(message)

    def clear_card_message(self, line):
        """Remove one card's message. Only its own action may do this for it."""
        self.card_messages.pop(line.key, None)
        line.clear()
        if line is getattr(self, "model_error_line", None) and hasattr(self, "model_card"):
            for field in [self.model_card, self.model_save_button, *self.model_error_fields]:
                self.set_field_error(field, False)

    def refresh_card_messages(self):
        """Re-apply every stored message to the lines now on screen.

        Called after a page rebuild, so a message survives the render that
        would otherwise destroy the widget holding it. Render methods must
        not write messages themselves: a background poll that clears them is
        exactly how the workspace page's errors used to vanish three seconds
        after they appeared.
        """
        for key, line in self.card_lines.items():
            entry = self.card_messages.get(key)
            if entry is not None:
                message, tone = entry
                line.set_tone(tone)
                line.show_message(message)

    def message_line(self, key):
        """A card's own message line, registered so a rebuild can find it.

        A rebuilt page replaces the line for its key rather than adding a
        second one, so the old widget is released with the old page instead
        of being held for the life of the window.
        """
        line = ErrorLine(self.dismiss_card_message, theme=self.theme)
        line.key = key
        self.card_lines[key] = line
        entry = self.card_messages.get(key)
        if entry is not None:
            message, tone = entry
            line.set_tone(tone)
            line.show_message(message)
        return line

    def dismiss_card_message(self, line):
        self.clear_card_message(line)

    def workspaces_page(self):
        """The workspace you are in, and the two ways of adding another.

        This used to be a pop-up menu off the rail followed by a modal:
        creating was a bare prompt with one field, and joining was a
        dialog that had been built properly but did not belong in a modal
        for a choice this size. Both now sit in the content area beside
        the other pages, and the rail's plus comes straight here.
        """
        page, layout = self.scroll_page()
        layout.addWidget(label("WHERE YOU ARE", "eyebrow"))
        layout.addWidget(label("Workspaces", "title"))
        layout.addWidget(label(
            "A workspace is a private network. This device belongs to one at a "
            "time, and you can move between them or bring in another device.",
            "muted", True))

        current, column = frame("card")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("This workspace", "heading"))
        self.workspace_name_label = label(self.workspace_label.text(), "statValue")
        column.addWidget(self.workspace_name_label)
        self.workspace_members_label = label("", "muted", True)
        self.workspace_members_label.setWordWrap(True)
        column.addWidget(self.workspace_members_label)
        column.addSpacing(10)
        self.workspace_invite_button = action("Invite a device", self.invite_device)
        self.workspace_invite_button.setToolTip(
            "Create an invitation another device can use to join this workspace")
        # Leaving lives here, on the page, rather than only behind Workspace
        # settings. It was there and worked, but a member looking at this card
        # had no way out and no sign that one existed: the only way to find it
        # was a danger button in a dialog whose wording led with the loss of
        # saved history. Next to the invitation it says what it does, and it is
        # shown only when there is somewhere to leave to.
        self.workspace_leave_button = action("Leave workspace", self.leave_workspace, name="danger")
        self.workspace_leave_button.setToolTip(
            "Stop being a member of this workspace and remove it from this device")
        self.workspace_leave_button.hide()
        current_actions = QHBoxLayout()
        current_actions.setSpacing(10)
        current_actions.addWidget(self.workspace_invite_button)
        current_actions.addWidget(self.workspace_leave_button)
        current_actions.addStretch()
        column.addLayout(current_actions)
        layout.addWidget(current)

        # The invitation generator, on the page rather than in a dialog.
        # This used to open InviteDialog, which is a separate window for a
        # short form: the fields were somewhere else from the description of
        # what an invitation is for, and the JSON it produces appeared in the
        # dialog while the card it belongs to stayed empty.
        layout.addWidget(label("BRING IN ANOTHER DEVICE", "eyebrow"))
        invite, column = frame("inviteCard")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("Invite a device", "heading"))
        column.addWidget(label(
            "Invite another laptop or desktop into this workspace's shared chat, "
            "including its existing messages and attachments. Each device gets "
            "its own identity, and the invitation is private: "
            "send it only to the machine you mean.", "muted", True))
        column.addSpacing(12)
        self.workspace_invite_name = QLineEdit()
        self.workspace_invite_name.setPlaceholderText("Device name · e.g. alex-laptop")
        self.workspace_invite_name.textChanged.connect(
            self.workspace_invite_typed)
        self.workspace_invite_name.returnPressed.connect(self.create_invitation)
        column.addWidget(self.workspace_invite_name)
        self.workspace_invite_lan = TickCheckBox("Share this relay on my local network")
        self.workspace_invite_lan.setChecked(True)
        self.workspace_invite_lan.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.workspace_invite_lan, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "Host network · choose the Wi-Fi or hotspot the other device is on",
            "muted", True))
        self.workspace_invite_network = Select()
        # What this card last wrote into the address box, so a later refresh
        # can tell its own address from one the reader typed.
        self._invite_generated = ""
        for name, ip in lan_addresses():
            self.workspace_invite_network.addItem(f"{name} · {ip}", ip)
        self.workspace_invite_network.currentIndexChanged.connect(
            self.workspace_invite_address)
        self.workspace_invite_lan.toggled.connect(self.workspace_invite_address)
        column.addWidget(self.workspace_invite_network)
        column.addWidget(label("Address the other device can reach", "muted", True))
        self.workspace_invite_url = QLineEdit()
        column.addWidget(self.workspace_invite_url)
        column.addWidget(label(
            "Keep the host app open and allow Multiplayer AI through its "
            "firewall. After changing Wi-Fi or hotspot, create a new "
            "invitation with the current address. Campus and guest Wi-Fi may "
            "block devices from reaching each other even on the same name; for "
            "those, use a reachable WSS relay.", "muted", True))
        self.workspace_invite_error = self.message_line("workspace:invite")
        column.addWidget(self.workspace_invite_error)
        self.workspace_invite_create_button = action(
            "Create invitation", self.create_invitation, True)
        self.workspace_invite_create_button.setEnabled(False)
        column.addWidget(self.workspace_invite_create_button)
        self.workspace_invite_result = QPlainTextEdit()
        self.workspace_invite_result.setReadOnly(True)
        self.workspace_invite_result.setMaximumHeight(180)
        self.workspace_invite_result.hide()
        column.addWidget(self.workspace_invite_result)
        self.workspace_invite_copy_button = action(
            "Copy private invitation", self.copy_invitation)
        self.workspace_invite_copy_button.hide()
        column.addWidget(self.workspace_invite_copy_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(invite)
        self.workspace_invite_address()

        # One heading per section with the name of the job in it, because two
        # cards under a shared "Add another" heading read as one form split
        # in half: both had a text box, a line of help text and a button, and
        # nothing said that create makes a network here while join connects
        # to one somewhere else.
        layout.addWidget(label("MAKE A NEW ONE HERE", "eyebrow"))
        # Stacked rather than side by side. Two cards with fields in them
        # need about 950px together, and the window may be as narrow as
        # 720px, so a row either clipped the second card or gave the page
        # a horizontal scrollbar. One above the other fits every width, and
        # the page already scrolls. Each has its own accent edge so they
        # cannot be read as halves of a single form.
        create, column = frame("createCard")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("Create a workspace", "heading"))
        column.addWidget(label(
            "Starts a new private network on this device. Nothing to connect "
            "to, and no invitation needed — you can bring in other devices "
            "once it exists.", "muted", True))
        column.addSpacing(12)
        self.workspace_name_input = QLineEdit()
        self.workspace_name_input.setPlaceholderText("Workspace name")
        self.workspace_name_input.textChanged.connect(self.workspace_name_typed)
        self.workspace_name_input.returnPressed.connect(self.create_workspace)
        column.addWidget(self.workspace_name_input)
        # The rail shows a workspace as its first two letters, so the name
        # is previewed as it is typed rather than after the fact.
        preview_row = QHBoxLayout()
        preview_row.setSpacing(10)
        self.workspace_preview = WorkspaceButton("")
        self.workspace_preview.setEnabled(False)
        preview_row.addWidget(self.workspace_preview)
        self.workspace_preview_label = label("", "muted", True)
        preview_row.addWidget(self.workspace_preview_label)
        preview_row.addStretch()
        column.addLayout(preview_row)
        # Two separate lines rather than one. The name rule is a live hint
        # under the field it constrains and changes as you type; the create
        # outcome is what the runtime said about the attempt. They were one
        # line, so typing over a failure replaced it with the name rule, and
        # a failure arrived where a hint was expected.
        self.workspace_name_line = self.message_line("workspace:name")
        column.addWidget(self.workspace_name_line)
        column.addSpacing(8)
        self.workspace_create_line = self.message_line("workspace:create")
        column.addWidget(self.workspace_create_line)
        self.workspace_create_button = action(
            "Create workspace", self.create_workspace, True)
        self.workspace_create_button.setEnabled(False)
        column.addWidget(self.workspace_create_button)
        layout.addWidget(create)

        layout.addWidget(label("OR CONNECT TO ONE SOMEWHERE ELSE", "eyebrow"))
        join, column = frame("joinCard")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("Join a workspace", "heading"))
        column.addWidget(label(
            "Connects this device to a network hosted on another machine. "
            "Someone has to invite this device first — paste their invitation, "
            "or enter their relay address and your device token.", "muted", True))
        column.addSpacing(12)
        self.workspace_invitation = QPlainTextEdit()
        self.workspace_invitation.setPlaceholderText("Paste an invitation")
        self.workspace_invitation.setMaximumHeight(96)
        column.addWidget(self.workspace_invitation)
        self.workspace_relay = QLineEdit()
        self.workspace_relay.setPlaceholderText("wss://your-relay.example.com/connect")
        column.addWidget(self.workspace_relay)
        self.workspace_token = QLineEdit()
        self.workspace_token.setPlaceholderText("Your device's relay token")
        self.workspace_token.setEchoMode(QLineEdit.EchoMode.Password)
        column.addWidget(self.workspace_token)
        self.workspace_lan = TickCheckBox("Trusted LAN, so ws:// is allowed")
        # A checkbox's minimum is the width of its whole label and it has no
        # word wrap to fall back on, so a long one sets the width of its
        # card and of the page, and the page gains a horizontal scrollbar.
        # The label is kept short for the reader's sake and the horizontal
        # policy is Ignored so that a longer one in future cannot do it
        # again; the checkbox then takes the width it is given and draws
        # from the left like every other control here.
        self.workspace_lan.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.workspace_lan, 0, Qt.AlignmentFlag.AlignLeft)
        column.addSpacing(8)
        self.workspace_join_line = self.message_line("workspace:join")
        column.addWidget(self.workspace_join_line)
        self.workspace_join_button = action("Join workspace", self.join_workspace, True)
        column.addWidget(self.workspace_join_button)
        layout.addWidget(join)
        layout.addStretch()
        self.render_workspace_page()
        self.refresh_card_messages()
        return page

    def render_workspace_page(self):
        """Refresh the parts of the page that follow the runtime.

        Called on every agents event, because joining or creating changes
        what the workspace is, and the page has to stop claiming to be
        creating something you already created.

        Deliberately writes no messages. This runs on the three-second
        agents poll, and it used to end in workspace_name_typed(), which
        owned the card's single shared line, so any error on this page was
        erased within three seconds of appearing. State refresh belongs
        here; messages belong to the card's own action.
        """
        if not hasattr(self, "workspace_name_label"):
            return
        self.workspace_name_label.setText(self.workspace_label.text())
        members = self.workspace_meta.get("members", [])
        people = [m for m in members if m.get("role") != "Model"]
        models = [m for m in members if m.get("role") == "Model"]
        summary = f"{len(people)} device{'s' if len(people) != 1 else ''} here"
        if models:
            summary += f", {len(models)} model agent{'s' if len(models) != 1 else ''}"
        if self.remote:
            summary += " · joined from another device"
        self.workspace_members_label.setText(summary)
        self.workspace_invite_button.setEnabled(self.ready and not self.remote)
        self.workspace_leave_button.setVisible(self.remote)
        self.workspace_leave_button.setEnabled(self.ready)
        self.workspace_invite_create_button.setEnabled(
            self.ready and bool(self.workspace_invite_name.text().strip())
            and not self.workspace_busy["invite"])
        self.workspace_invite_error.setVisible(bool(
            self.workspace_invite_error.message.text()))
        self.workspace_name_input.setEnabled(
            self.ready and not self.workspace_busy["create"])
        self.workspace_create_button.setEnabled(
            self.ready and bool(self.workspace_name_input.text().strip())
            and not self.workspace_busy["create"])
        # Held for the whole attempt, which the progress message says can take
        # 30 seconds. The poll used to re-enable this within three, so a join
        # could be submitted twice and the second attempt's failure could land
        # on top of the first attempt's success.
        #
        # One shared workspace at a time, so this is refused while this device
        # is a member of another one. The line says which and why rather than
        # leaving a greyed button to be puzzled at: a control that is off with
        # no reason reads as broken, and this one is off for a decision the
        # reader can go and reverse.
        joined = next((entry["name"] for entry in self.workspace_list
                    if entry.get("kind") == "remote"), None)
        self.workspace_join_button.setEnabled(
            self.ready and not self.workspace_busy["join"] and not joined)
        # Where to leave it depends on whether this device is in that
        # workspace right now. The Leave control sits on the current
        # workspace's card and is shown only when this device is a member of
        # the one it is looking at, so "the card above" is only true while
        # that is the case. Hosting your own workspace while holding a
        # membership elsewhere is the other half, and there the card above is
        # yours and has no Leave on it at all -- the way out is to switch to
        # that workspace first.
        #
        # Cleared when there is nothing joined any more, but only when what the
        # line is holding is the hint that was put there. It used to be written
        # only when there was, so the line outlived the workspace and went on
        # naming one this device had already left; but this line also carries
        # the error from a join that actually failed, and clearing that because
        # some unrelated workspace was left would throw away the only account
        # of what went wrong.
        if joined:
            hint = f"You are in '{joined}'. " + (
                "Leave it from the card above before joining another."
                if self.remote else
                "Switch to it in the rail to leave it, before joining another.")
            self._join_derived = hint
            self.set_card_message(self.workspace_join_line, hint)
        elif self.workspace_join_line.message.text() == getattr(self, "_join_derived", ""):
            self.set_card_message(self.workspace_join_line, "")
        # The relay's port is not known when this card is built, so the
        # address it generated first carried port 0 and the invitation would
        # have advertised it. Harmless to repeat here: a typed address is
        # never overwritten.
        self.workspace_invite_address()
        self.workspace_preview.setEnabled(False)
        # The button and preview follow the runtime here, but the name hint
        # belongs to typing, so only that one line is re-derived.
        name = self.workspace_name_input.text().strip()
        self.workspace_preview.setText(name[:2].upper())
        self.workspace_preview_label.setText(
            f"appears in the rail as {name[:2].upper()!r}" if name else "")
        self.workspace_create_button.setEnabled(
            self.ready and 1 <= len(name) <= 80 and not self.workspace_busy["create"])

    def workspace_name_typed(self, text):
        """Keep the create button honest, preview the rail button, hint the rule.

        The name is checked here rather than on submit, so the constraint is
        visible before anything is attempted, using the same rule and the
        same wording the runtime applies, so the two cannot disagree.

        Writes only the name hint, never the create outcome. It used to own
        the card's single shared line, so a poll reaching here three seconds
        after a failure erased it, and typing replaced a real failure with
        the name rule.
        """
        name = text.strip()
        allowed = 1 <= len(name) <= 80
        self.workspace_preview.setText(name[:2].upper())
        self.workspace_preview_label.setText(
            f"appears in the rail as {name[:2].upper()!r}" if name else "")
        if allowed:
            self.clear_card_message(self.workspace_name_line)
        else:
            self.set_card_message(self.workspace_name_line, self.name_rule())
        self.workspace_create_button.setEnabled(
            bool(self.ready and allowed and not self.workspace_busy["create"]))

    def create_workspace(self):
        """Create from the page, and report the outcome where it happened.

        The command is asynchronous, so the failure arrives after this
        returns; routing it to the page's own line keeps the message on
        screen beside the field that caused it, rather than in a toast that
        has gone by the time it is read.
        """
        name = self.workspace_name_input.text().strip()
        if not 1 <= len(name) <= 80:
            self.set_card_message(self.workspace_name_line, self.name_rule())
            return
        self.clear_card_message(self.workspace_name_line)
        # Retrying is the one thing that clears the previous outcome, so the
        # message never lingers over a button the reader has just pressed.
        self.clear_card_message(self.workspace_create_line)
        self.workspace_create_button.setEnabled(False)
        self.workspace_name_input.setEnabled(False)
        self.workspace_busy["create"] = True
        self.command("create_workspace", name,
                     success=self.workspace_created,
                     failure=self.workspace_failed)

    def workspace_created(self, entry):
        self.workspace_busy["create"] = False
        self.workspace_name_input.clear()
        self.clear_card_message(self.workspace_create_line)
        self.render_workspace_page()
        self.notice(f"Now in {entry.get('name', 'the new workspace')}.")

    def workspace_failed(self, message):
        self.workspace_busy["create"] = False
        self.set_card_message(self.workspace_create_line, message)
        self.render_workspace_page()

    def join_workspace(self):
        """Join from the page, with the whole connection done here.

        This used to open the old modal, which made the page's own fields
        decoration: paste an invitation, press the button, and the page was
        thrown away in favour of being asked for the same information again.
        The rules about what counts as an invitation are not restated here;
        they live in ``parse_invitation`` so this route and the dialog's
        cannot disagree.

        The command is asynchronous, so a failure arrives after this returns
        and is reported on the page's own line, beside the fields that
        caused it, rather than in a toast that has gone by the time it is
        read.
        """
        if not self.ready:
            self.set_card_message(self.workspace_join_line,
                                  "Your network is still starting.")
            return
        self.clear_card_message(self.workspace_join_line)
        try:
            conversation_id = None
            invitation = self.workspace_invitation.toPlainText().strip()
            if invitation:
                url, token, allow_insecure, conversation_id = parse_invitation(invitation)
                self.workspace_relay.setText(url)
                self.workspace_token.setText(token)
                self.workspace_lan.setChecked(allow_insecure)
            if len(self.workspace_token.text().strip()) < 32:
                raise ValueError("Enter the device token from your invitation")
            self.workspace_join_button.setEnabled(False)
            self.workspace_join_button.setText("Connecting…")
            self.workspace_busy["join"] = True
            # Progress belongs to this card and stays until the attempt
            # resolves, so a slow relay says why it is slow rather than
            # looking hung.
            self.set_card_message(
                self.workspace_join_line,
                "Contacting the relay. Keep the host app open; this can take up "
                "to 30 seconds.", "progress")

            def failure(message):
                self.workspace_busy["join"] = False
                self.workspace_join_button.setEnabled(self.ready)
                self.workspace_join_button.setText("Join workspace")
                self.set_card_message(
                    self.workspace_join_line,
                    message or "The connection failed. Check the host address, network, "
                               "and firewall.")

            def success(result):
                self.workspace_busy["join"] = False
                self.workspace_token.clear()
                self.workspace_invitation.clear()
                self.clear_card_message(self.workspace_join_line)
                self.render_workspace_page()
                shared_chat = conversation_id or (result or {}).get("conversation_id") or self.workspace_chat_id()
                self.select_agent(shared_chat or GENERAL_TARGET)

            self.command("join", self.workspace_relay.text().strip(),
                         self.workspace_token.text().strip(),
                         self.workspace_lan.isChecked(), True, conversation_id,
                         success=success, failure=failure)
        except (ValueError, KeyError, TypeError) as exc:
            self.workspace_busy["join"] = False
            self.workspace_join_button.setEnabled(self.ready)
            self.workspace_join_button.setText("Join workspace")
            self.set_card_message(self.workspace_join_line, str(exc))

    def workspace_invite_typed(self, text):
        """Keep the button honest, and say what the network address will be.

        The address is shown as soon as the card exists rather than after the
        invitation is made, because the wrong Wi-Fi is the usual reason a
        device cannot reach this one.
        """
        self.workspace_invite_create_button.setEnabled(
            bool(self.ready and text.strip() and not self.workspace_busy["invite"]))
        self.workspace_invite_address()

    def workspace_invite_address(self):
        """Keep the address in step with the network, and clear it when LAN goes off.

        Written only when the text is empty or is an address this card
        generated. A relay address the reader typed in is theirs: turning off
        local sharing is a statement about this machine, and a WSS relay
        somebody reached for on purpose is not something to overwrite.

        That is also what makes the relay's port arriving late harmless. The
        card is built before the runtime is up, so the address it wrote first
        carried port 0, and create_invitation submits whatever is in the box.
        Tracking the last value this card wrote is what lets the real port
        replace it on the next refresh without ever touching a typed one.

        The toggle used to be connected to nothing at all, so turning it off
        changed neither the address nor the network picker: the box kept
        advertising the ws:// address while the invitation it produced was
        made with LAN sharing off, and the picker stayed live doing nothing.
        """
        sharing = self.workspace_invite_lan.isChecked()
        # The picker only means something while the address is taken from it.
        self.workspace_invite_network.setEnabled(sharing)
        current = self.workspace_invite_url.text()
        ours = not current or current == self._invite_generated
        if sharing:
            if not ours:
                return
            generated = self.generated_lan_address()
            self._invite_generated = generated
            self.workspace_invite_url.setText(generated)
        elif current == self._invite_generated:
            self.workspace_invite_url.clear()

    def generated_lan_address(self):
        """The ws:// address this card would generate right now."""
        address = self.workspace_invite_network.currentData() or "YOUR_LAN_IP"
        return f"ws://{address}:{self.port}/connect"

    def create_invitation(self):
        """Make the invitation from the page, and show it here.

        The command is asynchronous, so the result arrives after this returns;
        it is put back on the card that asked for it rather than in a dialog
        that has already closed.
        """
        if not self.ready:
            self.set_card_message(self.workspace_invite_error,
                                  "Your network is still starting.")
            return
        self.clear_card_message(self.workspace_invite_error)
        self.workspace_busy["invite"] = True
        self.workspace_invite_create_button.setEnabled(False)
        self.workspace_invite_create_button.setText("Creating…")

        def failure(message):
            self.workspace_busy["invite"] = False
            self.workspace_invite_create_button.setText("Create invitation")
            self.set_card_message(self.workspace_invite_error, message)
            self.render_workspace_page()

        def success(data):
            self.workspace_busy["invite"] = False
            self.workspace_invite_create_button.setText("Create invitation")
            self.workspace_invite_name.clear()
            self.workspace_invite_result.setPlainText(json.dumps(data, indent=2))
            self.workspace_invite_result.show()
            self.workspace_invite_copy_button.show()
            self.render_workspace_page()

        self.persist_history()
        self.command("invite", self.workspace_invite_name.text().strip(),
                     self.workspace_invite_url.text().strip(),
                     self.workspace_invite_lan.isChecked(),
                     None, GENERAL_TARGET, None, success=success, failure=failure)

    def copy_invitation(self):
        QApplication.clipboard().setText(self.workspace_invite_result.toPlainText())
        self.notice("Private invitation copied. Send it only to the intended device.")

    def add_workspace(self):
        """The rail's plus goes to the page, where both routes are offered."""
        self.navigate(WORKSPACES_PAGE)

    def leave_workspace(self):
        """Leave a workspace this device joined, from the page that shows it.

        The same ``delete_workspace`` the Workspace dialog uses, asked with
        different words. That wording is the whole difference and it matters:
        the dialog leads with the loss of saved history, which is true, and
        reads to somebody trying to get out of a workspace they were added to
        as though the cost of leaving were the messages. The messages do go,
        so this says so, but after saying what is about to happen to the
        membership rather than before.

        The host is told first and this device's copy goes only once it has
        agreed, so the row does not come back on the next poll.
        """
        if not self.ready or not self.remote:
            return
        title = self.workspace_label.text() or "this workspace"
        message = (f"Leave '{title}'? The host will remove this device, so you will no "
                   f"longer be a member and will not see its conversations.\n\n"
                   f"Saved messages for this workspace are also deleted from this "
                   f"device. If the host is offline, only this device forgets it.")
        if not self.answered_yes(QMessageBox.question(
                self, "Leave workspace", message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)):
            return
        self.workspace_leave_button.setEnabled(False)

        def failed(reason):
            # Put the button back rather than leaving a control that cannot be
            # pressed and no line saying why. The host refusing is the likely
            # case: it may be gone, or it may never have granted this device.
            self.workspace_leave_button.setEnabled(True)
            self.notice(reason, error=True)

        def done(result):
            self.notice(f"You have left '{title}'.")

        self.command("delete_workspace", success=done, failure=failed)

    def reload_workspace_invitations(self, dialog):
        """Re-read the invitations this device can still issue, and show them.

        The list is fetched once, when the dialog opens, and the dialog's own
        refresh() returns early whenever the membership has not changed. Making
        an invitation does not change who is in the workspace, so the section
        went on showing what it had at open time -- a new invitation never
        appeared, and a removed one stayed. Removing does change membership, but
        it still read the names captured at open, so it stayed too.

        So the two are driven separately: refresh() keeps its signature guard,
        which is what stops the members table being rebuilt every second, and
        the invitations are re-read by whoever changed them.
        """
        def loaded(names):
            self.outstanding_invitations = names
            dialog.refresh_invitations()

        self.command("outstanding_invitations", success=loaded)

    def manage_workspace(self):
        """Open workspace settings, with the invitations this device can reissue.

        The list is fetched once, when the dialog opens, rather than polled:
        it changes only when this reader creates or removes an invitation, and
        both of those happen from this window rather than in the background.
        Reading it through the network thread would mean a second poll ticking
        every second for a list that is almost always empty.
        """
        from .workspace_dialog import WorkspaceDialog
        if self.ready:
            self.outstanding_invitations = []
            dialog = WorkspaceDialog(self)
            self.reload_workspace_invitations(dialog)
            dialog.exec()

    def invite_device(self):
        """Go to the page's own invitation card, ready to fill in.

        Mirrors add_agent(), which has done this for the model form since its
        dialog went: navigate, then put the cursor in the first field. Opening
        InviteDialog here was the last route to a window for a form that has a
        home, and it meant the card on the Workspaces page -- the one carrying
        the explanation of what an invitation is -- was not where the
        invitation was made.
        """
        if not self.ready:
            self.notice("Your network is still starting. Please wait a moment.")
            return
        if self.remote:
            return
        self.navigate(WORKSPACES_PAGE)
        self.workspace_invite_name.setFocus()

    def invite_conversation(self):
        if not self.ready or self.remote or not self.selected:
            return
        if self.selected in self.conversations:
            InviteDialog(self, conversation_id=self.selected).exec()
        else:
            chat = self.chats.get(self.selected, {})
            history = conversation_archive(chat)
            InviteDialog(self, target=self.selected, messages=history).exec()

    def closeEvent(self, event):
        if self.closing and not self.network.isRunning():
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.persist_history()
            self.closing = True
            self.setEnabled(False)
            self.toast.setText("Disconnecting agents and shutting down your local relay…")
            # The accessible name goes with it. It is written on every notice
            # and is the only channel a screen reader has for this toast, so
            # leaving it describing whatever notice came before means the window
            # says one thing and announces another for the whole of shutdown --
            # which is when it is least welcome to be told the wrong thing.
            self.toast.setAccessibleName(
                "Disconnecting agents and shutting down your local relay…")
            # Stopped first: this message is not going anywhere, and a copy
            # notice still counting down would take it off the screen a second
            # later -- during shutdown, on a window that is already disabled.
            self.toast_timer.stop()
            self.toast_row.show()
            self.network.shutdown()

    @Slot()
    def finish_close(self):
        if self.closing:
            self.close()
