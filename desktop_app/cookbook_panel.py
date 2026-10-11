"""Which models this machine can run, and what to do about the ones it can't.

A page in the navigation stack rather than a window, and reached from the top
bar rather than the rail. Both are about the same thing: it is a destination
like any other, it scrolls and resizes with the rest of the app, and opening a
second window for it put a form somewhere the rest of the interface could not
reach.

Everything it shows is decided by ``desktop_app.cookbook``, which holds no Qt.
This module draws conclusions and does no arithmetic, so there is one place to
be wrong about a recommendation and it is not here.

Nothing leaves the machine until Refresh is pressed. The Resources page says
this device's readings are sent nowhere, and a Cookbook that quietly called
Hugging Face on open would make that untrue. The only request this page makes
is the one a person asked for by pressing the button, and the button says what
it sends before it is pressed.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QVBoxLayout,
                             QWidget)

from .cookbook import (EXPLANATIONS, FITTING, LIKELY, NOT_ENOUGH_PUBLISHED,
                       NO_CATALOGUE, POSSIBLE, PREVIOUS_NAME, VERIFIED,
                       CatalogError, TASKS, describe_changes, detect_hardware,
                       empty_reason, human_bytes, kv_cache_bytes, load_cache,
                       load_catalog, ollama_commands, parse_model, recommend,
                       rollback_catalog, save_catalog)
from .cookbook_hub import check_base
from .widgets import Select, action, label

# The name of the badge beside each model, and the label style it wears.
#
# "errorMessage" is the Qt objectName, not the theme token: there is no
# ``QLabel#error`` rule, so a badge given "error" matched nothing and came out
# in the default text colour. A model this device should not run then looked
# the same as one it should, which is the one thing a badge exists to prevent.
# It went unnoticed in a dialog and would not in a page, where the surrounding
# stylesheet is in reach.
TONE = {
    "likely to run": "verdictOk",
    "verified compatible": "verdictOk",
    "possible with compromises": "verdictWarn",
    "not recommended": "verdictBad",
    "unknown": "verdictUnknown",
}

# The card each verdict is drawn on, which is what carries the colour.
#
# A coloured left border rather than a badge, because that is what this app
# already does for a card whose status matters: #joinCard for success and
# #createCard for accent. A pill would have been a second visual vocabulary
# for a thing the interface already expresses.
#
# "unknown" and "possible with compromises" used to share one grey, which is
# not a small difference: one says we do not know, the other says it runs but
# slowly. A reader who cannot see the difference is being told nothing.
CARD = {
    "likely to run": "modelCardOk",
    "verified compatible": "modelCardOk",
    "possible with compromises": "modelCardWarn",
    "not recommended": "modelCardBad",
    "unknown": "modelCardUnknown",
}

# How many model cards one page holds.
#
# Ten is not a layout preference. A card is a dozen lines of wrapped text, and a
# catalogue of any size rendered at once turns the page into a single undifferentiated
# column that nobody scrolls through -- which is the same failure as having no
# scrollbar at all, reached from the other direction. Ten is about two screens,
# enough to compare against each other and few enough that the next page is
# something you expect to exist.
PAGE_SIZE = 10

# One card's padding, for every card. Left, top, right, bottom.
CARD_PADDING = (20, 17, 20, 17)

# How many builds one card's table shows at once.
#
# Eight, and then it says how many it is not showing. A repository with twenty
# builds produced a card over a thousand pixels tall, which is the clustering
# problem arriving from the other direction.
#
# The table is smallest first, so what falls past the cap is the larger builds
# -- which makes the truncation drop the ones that were going to be least
# useful on this device anyway. That is a consequence of the ordering, not a
# claim about what the device can run: on a machine with no graphics card and
# memory to spare, a build past the eighth may run perfectly well.
BUILDS_SHOWN = 8


# Off by default. Set COOKBOOK_DEBUG_WINDOWS=1 to have the page report any
# top-level widget that is not the main window, at the moments a build is
# chosen.
#
# It exists because the symptom is a window that lasts a few milliseconds, which
# is too short for a screenshot and too short for a test to catch by polling. A
# reader who sees one can turn this on and get the widget's class and geometry
# printed instead of having it described again.
#
# The QComboBox popups every picker carries are excluded: they are windows by
# design and have been since before any of this.
DEBUG_WINDOWS = os.environ.get("COOKBOOK_DEBUG_WINDOWS") == "1"


def _report_stray_windows(tag):
    """Name any top-level widget that should not be one. Debug builds only."""
    if not DEBUG_WINDOWS:
        return
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    if application is None:
        return
    for widget in application.topLevelWidgets():
        try:
            parent = widget.parent()
            if parent is not None and type(parent).__name__ == "Select":
                continue
            if widget.isWidget() and widget.isWindow() and widget.isVisible():
                print(f"[cookbook {tag}] {type(widget).__name__} "
                      f"name={widget.objectName()!r} "
                      f"parent={type(parent).__name__ if parent else None} "
                      f"geom={widget.geometry().getRect()}", flush=True)
        except RuntimeError:
            continue


# What a refresh may not overwrite, because the Hub does not carry it.
#
# fetch_entry returns parameter counts, geometry, licences and file sizes --
# every figure this page does its arithmetic on. It returns nothing at all
# about what a model is *for*, and parse_model then defaults those fields,
# which meant a refresh silently undid every one of them:
#
#   tasks      -> empty, so the model vanished from every task filter while
#                 still appearing under "Everything"
#   name       -> the repository's last path segment, "Qwen2.5-0.5B-Instruct"
#                 in place of the curated "Qwen2.5 0.5B"
#   deprecated -> False, so a model its publisher had withdrawn came back
#                 as recommendable
#
# The tests missed it because their stub responses carried a "tasks" key. The
# Hub's own shape does not, and running one through is what the check below
# does.
CURATED_FIELDS = ("tasks", "name", "vision", "deprecated", "deprecated_reason")


def carry_over(model, result):
    """A refreshed record with the curated fields put back on top.

    Only for a model already in the catalogue. A response naming something
    nobody asked about still merges nothing, so what gets refreshed is what
    was requested.
    """
    merged = dict(result) if isinstance(result, dict) else {}
    if model is None:
        return merged
    for field in CURATED_FIELDS:
        merged[field] = getattr(model, field)
    return merged


class CookbookPanel(QWidget):
    """A list of models sized against the machine they are being read on."""

    def __init__(self, window, scroll_page):
        super().__init__()
        self.window = window
        self._busy = False
        self._page = 0
        # Set while a change to "Looking for" is waiting for its turn of the
        # event loop, so repeated presses collapse into one rebuild.
        self._task_pending = False
        # The build each card is showing, by repository. Not by position, and
        # not reset on every rebuild: switching the filter to Coding and back
        # should not quietly put a reader back on the build they rejected.
        # A repository that no longer has it -- a refresh, a rollback -- simply
        # falls back to the engine's choice, because every read goes through
        # _chosen_entry rather than trusting what is here.
        self._chosen = {}
        # Which cards have their build table open, by repository, for the same
        # reason. Choosing a build re-draws the card, and the reader was in the
        # middle of a list when they did it; a table that closes the instant it
        # is used is a table people stop trusting.
        self._open = set()
        # The model card currently on screen for each repository, so choosing a
        # build can replace that one card instead of rebuilding the page.
        self._cards = {}
        # Where a cached catalogue lives, and the host a refresh reads from.
        # Both are per workspace: two workspaces can be looking at different
        # catalogues, and a shared cache would have them overwrite each other.
        self.directory = getattr(window.storage, "directory", None)
        self.setAccessibleName("Cookbook: models this device can run")

        page, layout = scroll_page()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        page.setAccessibleName("Cookbook contents")
        # Kept, because the page is rebuilt from scratch on any change and the
        # scroll bar is the one thing a rebuild silently resets. Held as
        # ``scroller`` rather than ``scroll``, which is QWidget's own method.
        self.scroller = page

        # Everything below goes into ``layout`` -- the scroll area's own content
        # layout -- and not into ``outer``. ``outer`` holds the scroll area and
        # nothing else.
        #
        # Adding the cards beside the scroll area instead of inside it leaves
        # the scroll area empty, and an empty scroll area is one that measures
        # its content as nothing: its scrollbar range stays at zero so it never
        # draws, and the page grows taller than the window with everything past
        # the bottom edge clipped and no way to reach it. That is the whole
        # reason this page was unreadable, and it hid behind a test that only
        # checked a scroll area existed.
        #
        # ``layout`` is also where the shared 30/27 margins and 22px spacing come
        # from, so the page matches every other one in the stack instead of the
        # 0 margins and default spacing it was using.

        layout.addWidget(label("WHICH MODELS FIT THIS MACHINE", "eyebrow"))
        layout.addWidget(label("Cookbook", "title"))

        self.hardware = detect_hardware(
            window.storage.directory if hasattr(window, "storage") else None)
        card, column = self._card()
        column.addWidget(label("This device", "heading"))
        self.hardware_label = label(self.hardware.describe(), "muted", True)
        self.hardware_label.setWordWrap(True)
        column.addWidget(self.hardware_label)
        unknown = [name for name, origin in self.hardware.sources.items()
                   if origin not in ("detected", "manual")]
        self.unknown_label = label(
            ("Not detected here: " + ", ".join(sorted(unknown)) + ". Where an "
             "adapter shares system memory instead of having its own, models "
             "are sized against RAM, which is what the card would use anyway.")
            if unknown else self.hardware.graphics_note(),
            "muted", True)
        self.unknown_label.setWordWrap(True)
        self.unknown_label.setVisible(bool(unknown))
        column.addWidget(self.unknown_label)
        layout.addWidget(card)

        self.catalog_note = label("", "muted", True)
        self.catalog_note.setWordWrap(True)
        layout.addWidget(self.catalog_note)

        filters = QHBoxLayout()
        filters.addWidget(label("Looking for", "muted"))
        self.task_picker = Select()
        self.task_picker.setToolTip(
            "Narrow the list to one kind of work. Nothing is filtered by "
            "popularity or benchmark score, only by what fits.")
        # Everything first, and selected. Defaulting to a particular kind of
        # work hid most of the catalogue the moment the page opened, which is
        # the wrong first impression: someone who came to see what their
        # machine can hold is not asking about code yet.
        self.task_picker.addItem("Everything", None)
        for task, title in TASKS.items():
            self.task_picker.addItem(title, task)
        self.task_picker.currentIndexChanged.connect(self.task_changed)
        filters.addWidget(self.task_picker, 1)
        layout.addLayout(filters)

        self.results = QVBoxLayout()
        self.results.setSpacing(12)
        # Zeroed, because a layout on a widget gets Qt's 9px default. That
        # inset was the last thing keeping the cards off the page's left edge:
        # the device card sits directly in the page layout at 30px while the
        # model cards were pushed a further 9px in by this one, so their text
        # started at 51 and 60 -- three different edges, and the cards were 18px
        # narrower than the card above them for no reason anybody chose.
        self.results.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setLayout(self.results)
        layout.addWidget(self.body)

        # Paging sits directly under the list and above the actions, so it is
        # read as part of the list it moves rather than as another toolbar.
        # Both controls are built here and hidden rather than removed when the
        # catalogue fits on one page: a button that appears and disappears
        # between filter choices makes the row jump under the pointer.
        self.previous = action("Previous", lambda: self.go_to_page(self._page - 1),
                               name="ghost")
        self.previous.setAccessibleName("Previous page of models")
        self.page_label = label("", "muted")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next = action("Next", lambda: self.go_to_page(self._page + 1), name="ghost")
        self.next.setAccessibleName("Next page of models")
        pager = QHBoxLayout()
        pager.addStretch()
        pager.addWidget(self.previous)
        pager.addWidget(self.page_label, 1)
        pager.addWidget(self.next)
        layout.addLayout(pager)

        # Where a refresh reads from. Reading the setting without ever offering
        # a way to change it left the mirror documented and unreachable, and
        # "set cookbook_source in the JSON" is not something a person can do.
        self.mirror = QLineEdit()
        self.mirror.setPlaceholderText("https://huggingface.co")
        # Shown rather than left as a placeholder, so a mirror someone set
        # months ago is visible on this page rather than being a fact only the
        # code knows.
        self.mirror.setText(self.base or "")
        self.mirror.setAccessibleName("Model catalogue mirror host")
        self.mirror.setToolTip(
            "Leave empty to read from huggingface.co. A mirror is used for "
            "every model on every refresh, and must be an https:// origin.")
        self.mirror.editingFinished.connect(self.save_mirror)
        self.use_mirror = action("Use this host", self.save_mirror, name="ghost")
        self.use_mirror.setAccessibleName("Use this mirror host")
        # A widget rather than a bare layout, so the whole row can be hidden
        # together. A layout has no visibility of its own and hiding the two
        # widgets inside it separately would leave the label's gap behind.
        self.mirror_box = QWidget()
        self.mirror_row = QHBoxLayout(self.mirror_box)
        self.mirror_row.setContentsMargins(0, 0, 0, 0)
        self.mirror_row.addWidget(label("Read from", "muted"))
        self.mirror_row.addWidget(self.mirror, 1)
        self.mirror_row.addWidget(self.use_mirror)
        layout.addWidget(self.mirror_box)

        self.refresh = action(f"Refresh from {self.host_name()}", self.ask_to_refresh,
                           name="ghost")
        self.refresh.setToolTip(
            f"Re-read model details from {self.host_name()}. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")
        self.restore = action("Restore previous catalogue", self.do_restore, name="ghost")
        self.restore.setToolTip(
            "Put back the catalogue that was in use before the last refresh. "
            "Useful when a refresh brought in something that does not fit.")
        self.restore_mirror = action("Use the default host", self.reset_mirror,
                                     name="ghost")
        self.restore_mirror.setToolTip(
            "Go back to reading from huggingface.co. Only appears once a "
            "mirror has been set.")
        footer = QHBoxLayout()
        footer.addWidget(self.refresh)
        footer.addWidget(self.restore)
        footer.addWidget(self.restore_mirror)
        footer.addStretch()
        # A Close box on a page is a lie: there is nothing to close. It was
        # there because this was a dialog, and it was the clearest sign the
        # widget had stopped being what it claimed. Leaving the page is the
        # rail's job.
        layout.addLayout(footer)

        self.mirror_error = label("", "errorMessage", True)
        self.mirror_error.setWordWrap(True)
        self.mirror_error.setVisible(False)
        layout.addWidget(self.mirror_error)
        self.mirror_box.setVisible(bool(self.base))
        self.restore_mirror.setVisible(bool(self.base))

        # A trailing stretch, and the last thing in this layout.
        #
        # Without it the page looked like a different application depending on
        # whether anything was in the list. The scroll area resizes its content
        # to the viewport, so a short page had space to spare, and a vertical
        # box with no stretch hands that spare space to every row that will take
        # it rather than pooling it at the end. Measured with the list empty
        # and no other change: the eyebrow 14px -> 104px, the title 42 -> 105,
        # the device card 86 -> 104, the catalogue note 18 -> 104. Ninety pixels
        # of dead space above and below every element, which is why an empty
        # filter read as a different page rather than as the same one with
        # nothing on it. One stretch puts the slack in one place and both states
        # render identically.
        layout.addStretch()

        self.load()

    def showEvent(self, event):
        """Re-read the mirror each time the page is shown.

        A setting changed while this page was not on screen should not leave it
        describing a host it has stopped using -- the box, the button and the
        disclosure all name where the next request will go, and a stale name on
        those is the page making a claim it cannot keep.

        Skipped while the box has focus, so that arriving on the page does not
        overwrite what somebody is in the middle of typing.
        """
        super().showEvent(event)
        if self.mirror.hasFocus():
            return
        self.mirror.setText(self.base or "")
        self.mirror_box.setVisible(bool(self.base))
        self.restore_mirror.setVisible(bool(self.base))
        self.refresh.setText(f"Refresh from {self.host_name()}")
        self.refresh.setToolTip(
            f"Re-read model details from {self.host_name()}. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")

    @property
    def base(self):
        """Where a refresh reads from, read live rather than remembered.

        Cached at construction it went stale the moment the setting was changed
        anywhere else -- the page would go on labelling its own button with a
        host it had stopped using, which is the worst way for a control to
        lie, since the button is what says where the request is about to go.
        """
        return self.window.storage.settings.get("cookbook_source") or None

    def host_name(self):
        """Where a refresh reads from, as a person would name it."""
        return self.base or "huggingface.co"

    def host_origin(self):
        """The full origin a request will go to, scheme included.

        Separate from :meth:`host_name` because the button reads better saying
        "huggingface.co" and the disclosure must not. A dialog asking for
        permission to make twelve requests should show the reader the actual
        address, and showing it without its scheme would mean the default host
        was named one way and a mirror another, which is exactly the sort of
        thing that makes the address hard to check.
        """
        return check_base(self.base)

    def save_mirror(self):
        """Remember the mirror host, or say why the one typed is not usable.

        The check is the one the request itself uses, so a value accepted here
        cannot fail later at the point where the button says it is about to ask
        somewhere. An empty box is not an error: it is the way back to the
        default, which is also what the button beside it does.
        """
        typed = self.mirror.text().strip()
        try:
            host = check_base(typed or None)
        except ValueError as error:
            self.mirror_error.setText(str(error))
            self.mirror_error.setVisible(True)
            return
        self.mirror_error.setVisible(False)

        previous = dict(self.window.storage.settings)
        self.window.storage.settings["cookbook_source"] = None if not typed else host
        try:
            self.window.storage.save()
        except Exception as error:
            # Put the setting back rather than leaving the box showing a host
            # that was never saved, which would survive a restart and read as
            # though it had taken.
            self.window.storage.settings = previous
            self.mirror_error.setText(f"That could not be saved: {error}")
            self.mirror_error.setVisible(True)
            return

        self.refresh.setText(f"Refresh from {self.host_name()}")
        self.refresh.setToolTip(
            f"Re-read model details from {self.host_name()}. Sends your network "
            "address and the model names being shown. Nothing is requested "
            "until you press this.")
        self.mirror_box.setVisible(bool(self.base))
        self.restore_mirror.setVisible(bool(self.base))
        self.catalog_note.setText(
            f"Refreshes will be read from {self.host_name()}."
            if self.base else
            "Refreshes will be read from huggingface.co.")

    def reset_mirror(self):
        """Empty the box and go back to the public catalogue."""
        self.mirror.clear()
        self.save_mirror()

    def _card(self, name="card"):
        from .window import frame
        widget, column = frame(name)
        # Every card, whatever it is for. It used to be set on the model card
        # alone, so the device card, the withdrawn ones and the empty state all
        # fell back to Qt's 9px default and their text started 11px outboard of
        # the model cards beside them -- four different left edges on one page,
        # measured at 30, 39, 40 and 62. This is the padding the rest of the app
        # uses for a card, so one number covers all of them.
        padding = CARD_PADDING
        if name.startswith("modelCard"):
            # Two pixels back, because a model card carries a 3px left border
            # where the others carry 1px, and the extra two push its text out
            # of line with everything else on the page.
            padding = (CARD_PADDING[0] - 2,) + CARD_PADDING[1:]
        column.setContentsMargins(*padding)
        return widget, column

    def load(self):
        """The cached catalogue if there is one, otherwise the bundled one.

        Never touches the network. A cache that cannot be read is not an error
        worth stopping for: the bundled catalogue is always there, which is the
        reason it ships, and a line saying so is more use than a failure.
        """
        self.cached = load_cache(self.directory) if self.directory else None
        try:
            bundled = load_catalog()
        except CatalogError as error:
            if not self.cached:
                self.catalog = []
                self.catalog_note.setText(f"The bundled catalogue could not be read: {error}")
                self.render_models()
                return
            bundled = []
        self.catalog = self.cached if self.cached is not None else bundled
        if self.cached is None:
            self.catalog_note.setText(
                "Showing the catalogue that shipped with the app.")
        else:
            self.catalog_note.setText("Showing the catalogue saved by your last refresh.")
        self.refresh_controls()
        self.render_models()

    def refresh_controls(self):
        """Enable the actions that have something to act on."""
        self.restore.setEnabled(bool(self.directory and self.directory.joinpath(
            PREVIOUS_NAME).is_file()))

    def render_models(self):
        """Rebuild the list from the first page.

        Every reason the list changes -- a different task, a refresh, a
        rollback, a first load -- goes through here, and every one of them
        returns to page one. Carrying the page number across would land the
        reader on an empty page whenever a filter or a refresh changed how many
        models there are, and an empty page reads as "nothing fits this
        device", which is a claim about their hardware that they never made.
        """
        self._page = 0
        self.render_page()

    def go_to_page(self, number):
        """Show a different page of the same list."""
        self._page = max(0, int(number))
        self.render_page()

    def page_entries(self):
        """Everything the current filter matches, as one ordered list of cards.

        Recommended models first and withdrawn after them, in a single sequence
        rather than two sections, so the page limit can be applied to the whole
        thing. Withdrawn are not ranked with the rest: a withdrawn model cannot
        be recommended, and putting it among the ones that can would be an
        argument for downloading it. They are listed so somebody who already has
        one can find out why nothing recommends it.
        """
        task = self.task_picker.currentData()
        if not self.catalog:
            return []
        offered = recommend(self.catalog, self.hardware, task=task)
        withdrawn = [model for model in self.catalog
                     if model.deprecated and (not task or task in model.tasks)]
        return [("model", row) for row in offered] + [("withdrawn", model)
                                                      for model in withdrawn]

    def render_page(self):
        """Draw one page of cards, and the controls that move between them."""
        while self.results.count():
            item = self.results.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            # hidden() then deleteLater(), which is what window.clear_layout
            # does everywhere else in this app and is the only recipe here that
            # does not put a stray window on screen.
            #
            # setParent(None) was here instead, to detach cards before the event
            # loop got round to them. It does that, and it also promotes the
            # card to a top-level window -- measured: isWindow() goes from
            # False to True -- which is what a small white rectangle appearing
            # over the app is. Each re-render did it for every card on screen.
            widget.hide()
            widget.deleteLater()

        entries = self.page_entries()
        pages = max(1, -(-len(entries) // PAGE_SIZE))
        self._page = min(max(0, self._page), pages - 1)
        start = self._page * PAGE_SIZE
        window = entries[start:start + PAGE_SIZE]

        withdrawn_header_drawn = False
        self._cards = {}
        for kind, entry in window:
            if kind == "model":
                card = self._model_card(entry)
                self._cards[entry["model"].repo] = card
                self.results.addWidget(card)
                continue
            if not withdrawn_header_drawn:
                withdrawn_header_drawn = True
                if start:
                    # Only separated from the recommended models when it is not
                    # already the top of the page.
                    self.results.addSpacing(6)
                column = self._card()
                column.addWidget(label("Withdrawn by their publishers", "heading"))
                column.addWidget(label(
                    "Still listed so you can tell what happened to one you have. "
                    "They are not recommended for download.", "muted", True))
                self.results.addWidget(column)
            self.results.addWidget(self._withdrawn_card(entry))

        if window:
            self.results.addStretch()
        else:
            self._empty_card()

        self.update_pager(len(entries), pages)

    def update_pager(self, total, pages):
        """Say how much of the list is on screen, and which page this is."""
        paging = pages > 1
        for control in (self.previous, self.next, self.page_label):
            control.setVisible(paging)
        if not paging:
            self.page_label.setText("")
            return
        self.page_label.setText(
            f"Page {self._page + 1} of {pages} · {total} models")
        self.page_label.setAccessibleName(
            f"Page {self._page + 1} of {pages}, showing up to {PAGE_SIZE} of "
            f"{total} models")
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(self._page < pages - 1)

    def _withdrawn_card(self, model):
        """One model that its publisher has taken back."""
        card, column = self._card()
        column.addWidget(label(model.display, "heading", True))
        note = label(
            "Withdrawn by its publisher." + (
                f" {model.deprecated_reason}" if model.deprecated_reason else ""),
            "errorMessage", True)
        note.setWordWrap(True)
        column.addWidget(note)
        column.addWidget(label(
            f"{model.size_class} · {model.license or 'licence unstated'} · "
            f"read {model.last_verified or 'unknown'}", "muted", True))
        return card

    def _empty_card(self):
        """Draw the one card that says there is nothing, saying which nothing.

        ``recommend`` returns an empty list for four unrelated reasons and the
        page used to report all of them as "Nothing in the catalogue fits this
        device". Choosing *Image understanding* produced exactly that line
        while all four models in the catalogue fit comfortably -- none of them
        is simply tagged ``vision``. A gap in a four-model catalogue was being
        reported as a verdict on the reader's hardware, with advice about
        freeing memory that had nothing to do with the problem.

        So the reason is worked out where there are no widgets, and drawn as
        itself. A filter that hid the list offers its way back, because that is
        the one case where the reader can do something about it right now.
        """
        reason = empty_reason(self.catalog, self.hardware,
                              task=self.task_picker.currentData())
        card, column = self._card()
        column.addWidget(label(reason.heading, "heading", True))
        detail = label(reason.detail, "muted", True)
        detail.setWordWrap(True)
        column.addWidget(detail)

        if reason.can_widen:
            column.addSpacing(4)
            column.addWidget(action("Show every model", self.show_everything,
                                    name="ghost"))
        elif reason.kind in (NO_CATALOGUE, NOT_ENOUGH_PUBLISHED):
            column.addSpacing(4)
            column.addWidget(action(
                f"Refresh from {self.host_name()}", self.do_refresh,
                name="ghost"))
        self.results.addWidget(card)

    def task_changed(self):
        """A change to "Looking for", deferred to the next turn of the loop.

        Every way of changing this list -- the picker, the "Show every model"
        button, a refresh -- throws the whole page away and builds it again,
        and doing that from inside the interaction that asked for it is what
        puts windows on screen. Two paths reach it.

        The button is drawn on the very card saying the catalogue has nothing to
        show, so the rebuild tore down the widget still processing its own click:
        parented, laid out, and still the widget under the cursor at the moment
        the platform asks which window owns it. On Windows that question gets
        answered by putting a window on screen.

        The picker is being dismissed from its own dropdown when the signal
        arrives, so the page was rebuilt underneath a popup that had not closed
        yet, and the dropdown stayed up over a list it no longer described.

        Deferring to the next turn settles both, and collapses a burst of
        changes into one rebuild rather than one per press.
        """
        if self._task_pending:
            return
        self._task_pending = True
        QTimer.singleShot(0, self._task_changed_now)

    def _task_changed_now(self):
        """The deferred half of task_changed."""
        self._task_pending = False
        self.render_models()

    def show_everything(self):
        """Put the filter back to Everything, which is where models are.

        Nothing is rendered here. The picker's own signal does that, through
        task_changed, so there is exactly one rebuild per change and it happens
        after the interaction that caused it has finished.
        """
        index = self.task_picker.findData(None)
        if index < 0:
            return
        self.task_picker.setCurrentIndex(index)

    def _chosen_entry(self, row):
        """The build this card is showing: the reader's choice, or the best one.

        Always resolved rather than remembered. A catalogue that changed under
        the choice -- a refresh, a rollback, a filter over a different
        catalogue -- no longer has that build, and a remembered one would be a
        command naming a file that is not there.
        """
        builds = row.get("builds") or []
        wanted = self._chosen.get(row["model"].repo)
        for entry in builds:
            if wanted and entry["variant"].quantization == wanted:
                return entry
        return next((entry for entry in builds if entry["selected"]), None)

    def choose_build(self, repo, quantization):
        """Show a different build of this model, and re-draw the card for it.

        Deferred and scroll-preserving, for the same reason the filter change
        is (see task_changed): the row that was pressed is inside the card this
        throws away, so rebuilding from inside its own click is how a card ends
        up on screen as a window of its own.

        Keeping the reader where they were is the other half. The page is built
        from scratch, which drops the scroll bar to the top, and somebody
        choosing between eight quantisations has scrolled down a list to do it
        -- the choices are the last thing on the card, not the first. Landing
        back at the top after every one of them means choosing eight builds
        involves scrolling down eight times.
        """
        if self._chosen.get(repo) == quantization:
            return
        self._chosen[repo] = quantization
        where = self.scroller.verticalScrollBar().value()
        _report_stray_windows("before choosing a build")
        QTimer.singleShot(0, lambda: self._rebuild_at(where, repo))

    def _rebuild_at(self, where, repo):
        """The deferred half of choose_build: swap one card, keep the reader put.

        Built and inserted *before* the old card is destroyed, rather than the
        other way round. Rebuilding the page empties it first, so the list
        collapses to nothing and refills on every click -- and the card being
        destroyed is the one the pointer is resting on, which is how a widget
        that should be a row on a card briefly becomes a window of its own. A
        parentless widget is a window in Qt, complete with the app icon and the
        minimise, maximise and close buttons.

        Falls back to a full rebuild for anything it cannot place, so a card
        that is not where it was thought to be costs a redraw rather than a
        page with a hole in it.
        """
        self._swap_card(repo)
        bar = self.scroller.verticalScrollBar()
        if where:
            bar.setValue(where)
        _report_stray_windows("after choosing a build")

    def _swap_card(self, repo):
        """Put a freshly built card where the old one for this model was."""
        old = self._cards.get(repo)
        index = self.results.indexOf(old) if old is not None else -1
        entry = next((item for kind, item in self.page_entries()
                      if kind == "model" and item["model"].repo == repo), None)
        if old is None or index < 0 or entry is None:
            self.render_page()
            return
        fresh = self._model_card(entry)
        self.results.insertWidget(index, fresh)
        self._cards[repo] = fresh
        # Only now, with the replacement sitting in the layout where the old one
        # was.
        old.hide()
        old.deleteLater()

    def toggle_builds(self, repo, opened):
        """Remember which cards are open, so a re-render can leave them that way."""
        if opened:
            self._open.add(repo)
        else:
            self._open.discard(repo)

    def _other_builds(self, column, row):
        """Every build of this model, as a table you can choose from.

        It used to be a list you could only read, and a list you could only
        read was not much use: the page said a 7B model was "possible with
        compromises" at four-bit, offered the two-bit build in the row below,
        and then gave you a command for the four-bit one. Three failures at
        once -- the alternative was unselectable, unrunnable, and seven copies
        of the same fifty-nine characters differing only in the tag.

        Choosing a build now changes the card: the verdict and its colour, the
        reason, the figures and the command all follow, because the verdict was
        always computed per build by ``build_rows`` and only the one on show
        was ever drawn. The engine's choice is the default rather than being
        excluded, so what the card is using is visible in the list instead of
        being something you have to infer.
        """
        builds = list(row.get("builds") or [])
        if len(builds) < 2:
            return
        chosen = self._chosen_entry(row)

        on_card = sum(1 for entry in builds
                      if entry["verdict"]["result"] in (VERIFIED, LIKELY))
        spilling = sum(1 for entry in builds if entry["spills"])
        # Counted separately and worded honestly. "Possible with compromises"
        # is technically a fitting verdict, so calling these "fits" would have
        # claimed six of seven run on the graphics card when one does.
        #
        # Kept short on the button itself: a ghost button sizes to its text and
        # does not wrap, so a long summary here forced the whole card wider
        # than the page and put a horizontal scrollbar under it. The counts go
        # in the tooltip and inside the section, both of which wrap.
        summary = [f"{len(builds)} builds"]
        if on_card:
            summary.append(f"{on_card} run on the graphics card")
        if spilling:
            summary.append(f"{spilling} spill to the CPU")
        detail_text = " · ".join(summary[1:]) or "all of them fit system memory"
        toggle = action(summary[0], None, name="ghost")
        toggle.setToolTip(f"{summary[0]} — {detail_text}")
        toggle.setAccessibleName(
            f"Show the {len(builds)} builds of {row['model'].display}, with "
            f"their size and what this device makes of each. {detail_text}.")
        toggle.setCheckable(True)
        toggle.setChecked(row["model"].repo in self._open)
        column.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)

        detail = QWidget()
        listing = QVBoxLayout(detail)
        listing.setContentsMargins(0, 6, 0, 0)
        listing.setSpacing(4)
        note = label(detail_text, "muted", True)
        note.setWordWrap(True)
        listing.addWidget(note)

        shown = builds[:BUILDS_SHOWN]
        for entry in shown:
            listing.addWidget(self._build_row(row["model"], entry, chosen))
        hidden = len(builds) - len(shown)
        if hidden > 0:
            # "not shown", and not "this device cannot run": the table is
            # smallest first, so what is past the cap really is the larger
            # builds, but nothing here has established that this device cannot
            # run them. On a machine with no graphics card and memory to spare
            # every one of them may be perfectly runnable, and the page would
            # be telling a reader their hardware cannot do something it can.
            listing.addWidget(label(
                f"and {hidden} larger build{'s' if hidden != 1 else ''} not "
                "shown", "muted", True))

        detail.setVisible(toggle.isChecked())
        toggle.toggled.connect(detail.setVisible)
        toggle.toggled.connect(
            lambda opened, repo=row["model"].repo: self.toggle_builds(repo, opened))
        column.addWidget(detail)

    def _build_row(self, model, entry, chosen):
        """One build, as a row you can choose.

        A checkable ghost button rather than a label with a click handler, so
        the choice is a real control -- reachable by keyboard, and drawn as
        chosen by the ``#ghost:checked`` rule the app already has. Its property
        marks it as a build row for the tests, since the objectName has to stay
        ``ghost`` for the styling to apply.
        """
        variant, verdict = entry["variant"], entry["verdict"]
        total = (entry["figures"] or {}).get("total")
        is_chosen = bool(chosen and variant.quantization == chosen["variant"].quantization)

        row = action("", lambda: self.choose_build(model.repo,
                                                   variant.quantization),
                     name="ghost")
        row.setCheckable(True)
        row.setChecked(is_chosen)
        row.setProperty("buildRow", True)
        row.setProperty("buildVariant", variant.quantization)
        row.setCursor(Qt.CursorShape.PointingHandCursor)

        line = QHBoxLayout(row)
        line.setContentsMargins(10, 5, 10, 5)
        line.setSpacing(10)

        line.addWidget(label(variant.quantization or variant.name, "buildName"))

        size = f"{human_bytes(variant.size)} download"
        if total and total != variant.size:
            size += f" · {human_bytes(total)} in memory"
        line.addWidget(label(size, "muted", True), 1)

        if entry["selected"]:
            line.addWidget(label("recommended", "muted"))

        if entry["spills"]:
            mark, style = "spills to CPU", TONE[POSSIBLE]
        else:
            result = verdict["result"]
            # Short in the row, whole on hover. "possible with compromises" is
            # 24 characters of prose in a dense list, and it also forced the
            # card wider than the page.
            mark = {"verified compatible": "on the card",
                    "likely to run": "on the card",
                    "not recommended": "too big"}.get(result, "unknown")
            style = TONE.get(result, "muted")
        verdict_label = label(mark, style)
        gloss = EXPLANATIONS.get(verdict["result"])
        verdict_label.setToolTip(
            f"{verdict['result']} — {verdict['reason']}" + (
                f"\n\n{gloss}" if gloss else ""))
        # Carries the whole verdict too, so the short mark cannot be read
        # without the number that produced it. The row's own accessible name
        # says the same thing, because the row is now a control; this is what
        # a reader gets if they move onto the text itself.
        verdict_label.setAccessibleName(
            f"{verdict['result']}, {verdict['reason']}" + (
                f". {gloss}" if gloss else ""))
        line.addWidget(verdict_label)

        parts = [variant.quantization, size]
        if entry["selected"]:
            parts.append("the recommended build")
        parts.append(f"{verdict['result']}, {verdict['reason']}")
        if gloss:
            parts.append(gloss)
        row.setAccessibleName(". ".join(parts))
        row.setToolTip(verdict["reason"])
        return row

    def _model_card(self, row):
        """One model, in four groups: who it is, what it costs, how to run it,
        and where the figures came from.

        The groups are separated by more space than the lines inside them are,
        which is the whole change. Every child used to sit exactly 6px from the
        next, and six of them were the same grey at the same size, so a card
        read as one undifferentiated block: the verdict's own explanation was
        indistinguishable from the disclaimer three lines below it.
        """
        model = row["model"]
        # The build being shown, which is the reader's choice where they have
        # made one. Everything below -- the badge, its colour, the figures, the
        # command -- follows it, because the verdict was always worked out per
        # build and only one of them was ever drawn.
        entry = self._chosen_entry(row)
        variant = (entry or {}).get("variant", row["variant"])
        verdict = (entry or {}).get("verdict", row["verdict"])
        card, column = self._card(CARD[verdict["result"]])

        # Group one: what this is.
        column.addWidget(label(model.display, "heading", True))
        # The verdict sits under the name as its own line rather than floating
        # at the far right of the heading row. Beside a 19px heading it read as
        # a second title, and "possible with compromises" is twenty-four
        # characters of prose competing with the model's name for attention.
        # The card's own left border carries the signal now; this says which.
        badge = label(verdict["result"], TONE.get(verdict["result"], "muted"))
        gloss = EXPLANATIONS.get(verdict["result"])
        if gloss:
            # Written out in EXPLANATIONS and never rendered until now: the
            # verdict is an internal phrase, and a reader who cannot see the
            # colour has nothing to go on.
            badge.setToolTip(gloss)
            badge.setAccessibleName(f"{verdict['result']}: {gloss}")
        else:
            badge.setAccessibleName(f"Assessment: {verdict['result']}")
        column.addWidget(badge)

        # Group two: what it costs here.
        column.addSpacing(14)
        facts = (f"{model.size_class} · {human_bytes(variant.size)} download "
                 f"· {variant.quantization} · {model.license or 'licence unstated'}")
        # #meta rather than #muted: these are the figures the card exists to
        # give, and they were the same grey as the disclaimer.
        column.addWidget(label(facts, "meta", True))
        # And the reason is body text, because it is the sentence the reader
        # came for. It was muted, which is the style every quiet line on the
        # card also used.
        reason = label(verdict["reason"], None, True)
        reason.setWordWrap(True)
        column.addWidget(reason)

        longer = self._longer_context_warning(model)
        if longer:
            warning = label(longer, "muted", True)
            warning.setWordWrap(True)
            column.addWidget(warning)

        # Group three: how to run it.
        commands = ollama_commands(model, variant)
        if commands:
            column.addSpacing(16)
            column.addWidget(label("Run it", "muted"))
            # One block holding all of them, monospaced. In the proportional
            # face these were in, a repository path is an unbroken run of
            # characters that wraps wherever it happens to land, which is the
            # worst place for something the reader is about to copy and paste
            # into a terminal and get exactly right.
            block = label("\n".join(entry["command"] for entry in commands),
                          "command", True)
            block.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            block.setAccessibleName(
                f"{len(commands)} commands to run this model. Selectable, so "
                "it can be copied rather than typed.")
            column.addWidget(block)

        # Group four: where these numbers came from. Both lines in #footer, at a
        # size and colour meant to be read last rather than scanned, and set
        # close together so they read as one quiet footer instead of two more
        # things competing with the figures above.
        column.addSpacing(16)
        provenance = label(
            f"From {model.repo} on {model.source} · builds by "
            f"{model.artifact_repo} · read {model.last_verified or 'unknown'}",
            "footer", True)
        provenance.setWordWrap(True)
        column.addWidget(provenance)

        self._other_builds(column, row)

        disclaimer = label(
            "Sized from published figures, not measured on this device.",
            "footer", True)
        disclaimer.setWordWrap(True)
        column.addWidget(disclaimer)
        return card

    def _longer_context_warning(self, model):
        """Say what a longer conversation costs, when it would not fit.

        The verdict is for a 4k conversation. The model's own limit is often
        far higher, and using it is not free: the cache grows with every token,
        and at the full limit this model's is 1.75GiB. Stating that here is the
        difference between a reader choosing a context length and discovering
        one for themselves.
        """
        if not model.context or model.context <= 4096:
            return None
        full = kv_cache_bytes(model, model.context)
        if not full:
            return None
        current = kv_cache_bytes(model, 4096)
        extra = (full - (current or 0)) / 1024 ** 3
        if extra <= 0.25:
            return None
        return (f"At its full {model.context:,} token context this model needs "
                f"about {human_bytes(full)} of conversation memory, "
                f"{human_bytes(current or 0)} more than the figures above. "
                f"Raise the context length only if you need it.")

    def ask_to_refresh(self):
        """Say what pressing Refresh will do, then do it if that is welcome.

        The disclosure used to be a muted line above the list and a tooltip on
        the button -- a paragraph somebody has to go looking for, about twelve
        requests they are agreeing to. It is now asked for at the moment of the
        action, where the decision is actually being made.

        Remembered per host rather than once and forever. Pointing the Cookbook
        at a mirror means a different site sees the request, and agreeing to
        tell huggingface.co which models you run is not agreeing to tell
        somebody else.

        ``do_refresh`` is left ungated so it is the thing under test rather
        than a dialog: nothing else in the app calls it.
        """
        if self._busy or not self.catalog:
            return
        host = self.host_origin()
        if self.consented(host):
            self.do_refresh()
            return

        from .dialogs import RefreshDisclosure
        dialog = RefreshDisclosure(self.window, host, self.catalog,
                                   mirror=bool(self.base))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.should_remember():
            self.remember_consent(host)
        self.do_refresh()

    def consented(self, host):
        """Whether this host has been agreed to before."""
        agreed = self.window.storage.settings.get("cookbook_refresh_ok")
        return isinstance(agreed, dict) and bool(agreed.get(host))

    def remember_consent(self, host):
        """Record the agreement, and refuse to keep it if it cannot be saved."""
        settings = self.window.storage.settings
        previous = settings.get("cookbook_refresh_ok")
        agreed = dict(previous) if isinstance(previous, dict) else {}
        agreed[host] = True
        settings["cookbook_refresh_ok"] = agreed
        try:
            self.window.storage.save()
        except Exception as error:
            # Put it back rather than leaving a remembered agreement that did
            # not survive a restart: the reader would not be warned again this
            # session and would be warned again next time, which is worse than
            # not remembering at all.
            if previous is None:
                settings.pop("cookbook_refresh_ok", None)
            else:
                settings["cookbook_refresh_ok"] = previous
            self.catalog_note.setText(f"That could not be saved: {error}")

    def do_refresh(self):
        """The one control that leaves the machine, and only when pressed.

        Deliberately not wired to a timer, an open, or a hover. The page makes
        no request on its own, so the Resources page's promise that nothing is
        sent anywhere stays true of this page too.

        One press re-reads the whole catalogue, not one model from it. It used
        to refresh ``catalog[0]``, which meant the list could not update as a
        list: the other models kept figures as old as the install, and the one
        that moved was whichever happened to be first. The requests run one at
        a time rather than together, because a burst of four from one button
        press is not something the person pressing it agreed to.

        Each record goes through the same validation the bundled catalogue
        goes through, so a response cannot smuggle a field past the checks by
        arriving over the network instead of from disk. One model being
        refused or unreachable does not stop the rest, and the note says which
        ones those were rather than reporting the press as a single success or
        a single failure.

        The result is saved once at the end, so the catalogue on disk is
        never a half-finished mixture of old and new records, and the one it
        replaces is kept so it can be put back.
        """
        if self._busy:
            return
        targets = list(self.catalog)
        if not targets:
            self.catalog_note.setText(
                "There is nothing to refresh: no models have been loaded.")
            return

        before = list(self.catalog)
        updated = {}
        refused = {}
        self._busy = True
        self.refresh.setEnabled(False)
        self._refresh_step(0, len(targets), updated, refused, before)

    def _refresh_step(self, index, total, updated, refused, before):
        """Re-read one model, then either finish or ask for the next.

        Sequential rather than concurrent, so the host sees one request at a
        time from a button press, and so the progress on the button means
        something: "2 of 4" is a fact about work already done.
        """
        if index >= total:
            self._finish_refresh(total, updated, refused, before)
            return

        model = self.catalog[index] if index < len(self.catalog) else None
        repo = model.repo if model else "Qwen/Qwen2.5-0.5B-Instruct"
        artifacts = (model.artifact_repo if model
                     else "bartowski/Qwen2.5-0.5B-Instruct-GGUF")

        def landed(result):
            try:
                record = parse_model(carry_over(model, result), 0)
            except CatalogError as error:
                refused[repo] = (f"{self.host_name()} sent something unusable: "
                                 f"{error}")
            else:
                updated[record.repo] = record
            self._refresh_step(index + 1, total, updated, refused, before)

        def missed(reason):
            refused[repo] = reason
            self._refresh_step(index + 1, total, updated, refused, before)

        self.refresh.setText(f"Refreshing {index + 1} of {total} from "
                             f"{self.host_name()}.")
        self.window.command("cookbook_refresh", repo, artifacts, self.base,
                            success=landed, failure=missed)

    def _finish_refresh(self, total, updated, refused, before):
        """Merge what came back, say what did not, and save it once."""
        self._busy = False
        self.refresh.setEnabled(True)
        self.refresh.setText(f"Refresh from {self.host_name()}")

        if not updated:
            first = next(iter(refused.values()), "the request was refused")
            self.catalog_note.setText(
                f"{self.host_name()} could not be read: {first}")
            self.refresh_controls()
            return

        merged = [updated.get(model.repo, model) for model in before]
        # Anything that arrived that is not already in the catalogue is a
        # response naming a model this app did not ask about, which is not a
        # refresh result and is not merged in.
        note = describe_changes(before, merged)
        read = ", ".join(sorted(record.display for record in updated.values()))
        saved = ""
        if self.directory:
            newest = max((record.last_verified for record in updated.values()),
                         default="")
            try:
                kept = save_catalog(merged, self.directory, newest)
                saved = " The previous one is kept and can be restored." if kept else ""
            except (OSError, CatalogError) as error:
                # Shown, not raised: the models are already correct on
                # screen and losing them because they could not be written
                # down would be a worse outcome than a catalogue that has
                # to be read again next launch.
                saved = f" It could not be saved: {error}"

        self.catalog = merged
        self.catalog_note.setText(self._refresh_note(total, updated, refused,
                                                     read, note, saved))
        self.refresh_controls()
        self.render_models()

    def _refresh_note(self, total, updated, refused, read, note, saved):
        """One sentence saying what this press actually achieved.

        A partial refresh that reported plain success would be the wrong claim
        in the one case where it matters most -- when the host answered for
        some models and not others, and the reader would otherwise assume the
        whole list is current.
        """
        if not refused:
            return (f"Read {read} from {self.host_name()}. {note}{saved}")
        problems = "; ".join(f"{repo}: {reason}" for repo, reason
                             in sorted(refused.items()))
        return (f"Read {read} from {self.host_name()}, but {len(refused)} of "
                f"{total} could not be: {problems}. {note}{saved}")

    def do_restore(self):
        """Put back the catalogue from before the last refresh."""
        if not self.directory:
            return
        restored = rollback_catalog(self.directory)
        if restored is None:
            self.catalog_note.setText(
                "There is no earlier catalogue to go back to.")
            self.refresh_controls()
            return
        self.catalog = restored
        self.catalog_note.setText(
            f"Restored the catalogue from before your last refresh, with "
            f"{len(restored)} models.")
        self.refresh_controls()
        self.render_models()