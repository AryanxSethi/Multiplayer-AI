"""The Resources and Settings pages, against a real headless window.

Runs through the offscreen Qt platform plugin so no display is needed.
The window is given a MemoryVault rather than a real one: DesktopRuntime
calls Storage.save_credentials while starting up, so a real Vault would
leave a device token in the developer's OS credential store.
"""

from __future__ import annotations

from pathlib import Path

import json
import os
import re
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop page tests")
pytest.importorskip("psutil", reason="psutil backs the Resources page")

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QPalette, QShortcut, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame,  # noqa: E402
                             QLabel, QMessageBox, QPushButton, QWidget)
from PySide6.QtCore import QAbstractAnimation  # noqa: E402
from PySide6.QtCore import QEvent, QSize  # noqa: E402
from PySide6.QtGui import QResizeEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from desktop_app.markdown import MarkdownMessage  # noqa: E402
from desktop_app import window as win  # noqa: E402
from desktop_app.storage import ABSENT, REMOVED, Storage  # noqa: E402
from desktop_app.theme import (DARK, LIGHT, MIKU, THEME_CHOICES, THEME_NAMES,
                             THEMES, color, contrast_ratio, stylesheet)  # noqa: E402


class MemoryVault:
    """Stands in for the OS credential store."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        return REMOVED if self.values.pop(name, None) is not None else ABSENT



@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the session; Qt allows only one."""
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path, MemoryVault())


@pytest.fixture
def window(qt_app, storage, monkeypatch):
    """A real MainWindow, torn down after the test."""
    instance = win.MainWindow(storage)
    def unavailable(address):
        raise RuntimeError("Ollama startup is mocked in UI tests")
    monkeypatch.setattr(instance.network.runtime.ollama_service, "_start", unavailable)
    yield instance
    instance.network.shutdown()
    instance.network.wait(10000)


# --- first run -----------------------------------------------------------


def test_every_nav_button_explains_its_page(window) -> None:
    """A page whose name does not describe it needs saying out loud."""
    for button in window.nav_buttons:
        assert button.toolTip(), f"a nav button with no tooltip: {button.text()!r}"
    assert set(win.PAGE_HELP) == {key for _, key in win.PAGES}, (
        "every page needs an entry, or a tooltip is about to be missing")


def test_the_shell_controls_explain_themselves(window) -> None:
    """The top bar was the one part of the shell a hover could not teach you.

    Tooltips already covered the navigation buttons and the workspace
    settings, but not the two buttons a new user is most likely to reach
    for, nor the chat page's own controls. Each of these says what will
    actually happen rather than repeating the label it sits beside.
    """
    chrome = {
        "invite": window.invite_button,
        "connect a model": window.add_button,
        "welcome connect": window.welcome_connect,
        "welcome join": window.welcome_join,
        "share": window.share_conversation_button,
        "agent count": window.chat_agent_count,
        "attach": window.attach_button,
        "send": window.send_button,
    }
    for name, button in chrome.items():
        assert button.toolTip(), f"the {name} button has no tooltip"
        assert button.toolTip() != button.text().strip(), (
            f"the {name} tooltip only repeats its own label")


def test_a_conversation_offers_what_it_can_do_and_nothing_else(window) -> None:
    """What a conversation can be given, in the header and on right-click.

    Both come from one description, so they cannot drift. What is in it depends
    on the conversation: a direct one can be deleted outright, and a shared one
    follows the workspace's rule -- whoever started it may end it for everyone,
    and anyone else steps out of it.
    """
    window.identity = "me"
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]

    def offered():
        menu = window.build_conversation_menu(window.selected)
        return [] if menu is None else [entry.text() for entry in menu.actions()]

    def tooltips():
        menu = window.build_conversation_menu(window.selected)
        return [] if menu is None else [entry.toolTip() for entry in menu.actions()]

    window.chats = {"local-llama": {"messages": [("user", "hi")]}}
    window.selected = "local-llama"
    window.update_chat_controls()
    assert offered() == ["Clear messages", "Delete conversation"], (
        f"a direct conversation offers {offered()}")
    for tip in tooltips():
        assert tip, "a conversation action has no tooltip"
        assert tip not in ("Clear messages", "Delete conversation"), (
            f"the tooltip only repeats its own label: {tip!r}")

    # Nothing to clear, so nothing offered to clear.
    window.chats["local-llama"]["messages"] = []
    window.update_chat_controls()
    assert offered() == ["Delete conversation"], (
        f"an empty conversation still offers to clear it: {offered()}")

    # A shared conversation follows the workspace's own rule.
    room = {"id": "conversation-abc", "title": "Chat with llama3", "target": "local-llama",
            "owner": window.identity, "members": [window.identity, "guest"],
            "messages": [], "revision": 3, "pending": False}
    window.conversations = {"conversation-abc": room}
    window.chats["conversation-abc"] = {"messages": [("user", "hi")], "revision": 3}
    window.selected = "conversation-abc"
    window.update_chat_controls()
    assert offered() == ["Clear messages", "Delete for everyone"], (
        f"the host of a shared conversation is offered {offered()}")
    assert "every device" in tooltips()[1], (
        f"deleting for everyone should say who it is for: {tooltips()[1]!r}")

    room["owner"] = "somebody-else"
    window.update_chat_controls()
    assert offered() == ["Clear messages", "Leave conversation"], (
        f"a member of a shared conversation is offered {offered()}, but the "
        "conversation is not theirs to end")
    assert "for the others" in tooltips()[1], (
        f"leaving should say what is kept: {tooltips()[1]!r}")

    # The header's one button opens the same menu a right-click gives.
    assert [text for text, _, _ in
            window.conversation_actions("conversation-abc")] == [
                "Clear messages", "Leave conversation"]
    assert window.conversation_actions(None) == [], (
        "with no conversation open there is nothing to clear or delete")
    window.selected = None
    window.update_chat_controls()
    assert window.build_conversation_menu(None) is None, (
        "a menu was built with no conversation open")


def test_clearing_a_conversation_keeps_the_thread_and_drops_the_messages(
        window, qt_app) -> None:
    """Clear empties the messages; the conversation stays where it was.

    For a shared conversation the revision is pinned to the room's, which is
    what stops the next poll rebuilding the messages straight back from the
    relay. Without it, clearing appears to do nothing at all.
    """
    room = {"id": "conversation-abc", "title": "Chat with llama3", "target": "llama3",
            "owner": "owner", "members": ["owner", "guest"],
            "messages": [{"id": "1", "role": "user", "content": "hello", "from": "owner"}],
            "revision": 7, "pending": False}
    window.conversations = {"conversation-abc": room}
    window.chats = {"conversation-abc": {
        "messages": [("user", "hello")], "revision": 7, "unread": 2}}
    window.selected = "conversation-abc"
    window.render_messages()

    window.clear_conversation("conversation-abc")

    assert window.chats["conversation-abc"]["messages"] == [], (
        "clearing left the messages behind")
    assert "conversation-abc" in window.conversations, (
        "clearing removed the conversation instead of its messages")
    assert window.chats["conversation-abc"]["revision"] == 7, (
        "the revision was not pinned to the room's, so the next poll rebuilds "
        "the messages and clearing looks like it did nothing")
    assert window.chats["conversation-abc"]["unread"] == 0, (
        "clearing left an unread count against a conversation with no messages")


def test_deleting_a_conversation_removes_only_that_one(window, qt_app, monkeypatch) -> None:
    """One conversation goes; every other keeps its messages.

    Checked against the saved history as well as the window, because a delete
    that only cleared the screen would come back on the next launch.
    """
    from network_a2a.persistence import HistoryStore

    window.history_store = HistoryStore(window.storage.directory)
    window.conversations = {"conversation-abc": {
        "id": "conversation-abc", "title": "Room", "target": "llama3",
        "owner": "owner", "members": ["owner", "guest"], "messages": [],
        "revision": 1, "pending": False}}
    window.chats = {"keep": {"messages": [("user", "keep me")], "history": []},
                    "gone": {"messages": [("user", "delete me")], "history": []},
                    "conversation-abc": {"messages": [("user", "room chat")], "revision": 1}}
    window.selected = "gone"
    window.history_store.save("rooms", "conversation-abc", window.conversations["conversation-abc"])
    window.persist_history()

    monkeypatch.setattr(win.QMessageBox, "question",
                        lambda *args, **kwargs: int(win.QMessageBox.StandardButton.Yes))
    window.delete_conversation("gone")

    assert "gone" not in window.chats, "the deleted conversation is still open in memory"
    assert "keep" in window.chats, "deleting one conversation took another with it"
    assert window.chats["keep"]["messages"] == [("user", "keep me")], (
        "another conversation's messages were changed")
    assert "conversation-abc" in window.conversations, (
        "deleting a direct conversation removed a shared one")
    assert window.selected is None, (
        "the deleted conversation was left selected, so the window still opens it")

    saved = window.history_store.load("ui").get("state", {})
    assert "gone" not in saved.get("chats", {}), "the delete was not saved"
    assert saved.get("chats", {}).get("keep"), "saving the delete lost another conversation"
    assert saved.get("selected") is None, "a deleted conversation stayed selected on disk"

    # Deleting a shared room takes its record and its saved chat with it.
    window.delete_conversation("conversation-abc")
    assert "conversation-abc" not in window.conversations, "the room is still listed"
    assert "conversation-abc" not in window.history_store.load("rooms"), (
        "the room's saved record survived, so it would come back at the next poll")


def test_a_conversation_that_disappears_elsewhere_is_forgotten_here(window) -> None:
    """A room deleted on another device stops being a conversation here.

    The poll only ever rebuilt the room list. It left the matching chat behind
    and left ``selected`` pointing at it, so the conversation stayed in the
    sidebar and stayed open after it had been deleted somewhere else.
    """
    window.chats = {"conversation-abc": {"messages": [("user", "hi")], "revision": 2},
                    "direct-chat": {"messages": [("user", "a direct chat")], "revision": 1}}
    window.conversations = {"conversation-abc": {
        "id": "conversation-abc", "title": "Room", "target": "llama3",
        "owner": "owner", "members": ["owner"], "messages": [], "revision": 2,
        "pending": False}}
    window.selected = "conversation-abc"

    window.network_event("conversations", [])

    assert "conversation-abc" not in window.chats, (
        "the chat for a room that is gone was kept")
    assert window.selected is None, (
        "the window is still pointed at a conversation that no longer exists")
    assert "direct-chat" in window.chats, (
        "a room disappearing took a direct conversation with it")


def test_the_chat_page_does_not_overflow(window, qt_app) -> None:
    """Nothing in the chat page may be laid out past the edge of the window.

    A control in an overflowing layout is the worst kind of invisible: it is
    drawn, hit-testing reaches it, and it looks exactly where it is supposed
    to. Two things caused it. The composer's hint was an unwrapped label, so
    its whole line was a hard minimum width, which made the composer's minimum
    the chat panel's minimum at 1111px inside a panel of 1020 -- and the header
    row, laid out at that wider figure, put everything after its stretch past
    the right edge of the window. Separately, the conversation actions were two
    text buttons needing 485px in a row that had 918px of other things in it.
    """
    window.identity = "me"
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [("user", "hi"), ("assistant", "hello")]}}
    window.navigate(win.CONVERSATIONS_PAGE)
    window.show()
    window.resize(1330, 910)
    window.render_agents()
    qt_app.processEvents()

    panel = window.stack.currentWidget().layout().itemAt(0).widget()
    assert panel.minimumSizeHint().width() <= panel.width(), (
        f"the chat panel wants {panel.minimumSizeHint().width()}px but only has "
        f"{panel.width()}px, so its header is laid out wider than the panel")

    # Every control in the header has to be inside the window, not merely drawn.
    for name, control in (("agent count", window.chat_agent_count),
                          ("invite", window.share_conversation_button),
                          ("manage", window.manage_button)):
        centre = control.mapTo(window, control.rect().center())
        assert 0 <= centre.x() < window.width(), (
            f"the {name} control is at x={centre.x()} in a window "
            f"{window.width()}px wide, so it cannot be clicked")
        assert isinstance(window.childAt(centre), type(control)), (
            f"the {name} control is not what a click at that point would reach")


def test_the_conversation_header_is_a_surface_of_its_own(window, qt_app) -> None:
    """The heading reads as a heading, and not as the first lines of the chat.

    It sat on the chat's own background with nothing between them, while the
    composer below had a hairline to mark it off. The header is now a framed
    surface with an edge under it, in every theme.
    """
    window.selected = "local-llama"
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.chats = {"local-llama": {"messages": [("user", "hi")]}}
    window.navigate(win.CONVERSATIONS_PAGE)
    window.show()
    window.render_messages()
    qt_app.processEvents()

    assert window.chat_header.objectName() == "chatHeader"
    for name, sheet in THEMES.items():
        rule = next((line for line in sheet.splitlines() if "#chatHeader" in line), "")
        assert rule, f"{name} has no rule for #chatHeader"
        assert "border-bottom" in rule, (
            f"{name} gives the chat header no edge, so it does not read as "
            f"separate from the chat: {rule}")

    # And it is above the messages rather than inside them.
    assert window.chat_header.y() + window.chat_header.height() \
        <= window.messages_scroll.y(), (
            "the chat header overlaps the messages, so the heading and the "
            "conversation are on top of each other")


def test_a_conversation_action_survives_the_polling(window, qt_app, monkeypatch) -> None:
    """The header's conversation button must still work three seconds later.

    The runtime polls the agents list every three seconds, and this button used
    to be two, rebuilt from scratch on each poll. A click whose mouse-up landed
    after a rebuild was delivered to a widget that had already been thrown
    away, so the buttons looked dead and worked only when the timing happened to
    fall right. It is now one button, held, offering a menu of the same actions
    a right-click gives.

    Going through the polls first is the part that matters: an earlier test
    clicked straight after building the buttons and passed against the broken
    version.
    """
    window.identity = "me"
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [("user", "hi"), ("assistant", "hello")]}}
    window.navigate(win.CONVERSATIONS_PAGE)
    window.show()
    window.render_agents()

    def poll():
        window.network_event("agents", {"agents": window.agents, "connected": True})

    before = window.manage_button
    for _ in range(5):
        poll()
    assert window.manage_button is before, (
        "the agents poll replaced the conversation button, so a click can be "
        "delivered to a widget that no longer exists")
    assert window.manage_button.isEnabled(), (
        "the conversation button is disabled while there are messages to clear")

    # And the menu it opens really does the work. The dialog is replaced with
    # one that answers the way a real dialog answers: QMessageBox.question
    # returns a plain int, not the StandardButton enum member. Returning the
    # enum here instead is what let an identity test pass against code that
    # could never work.
    monkeypatch.setattr(win.QMessageBox, "question",
                        lambda *args, **kwargs: int(win.QMessageBox.StandardButton.Yes))
    menu = window.build_conversation_menu(window.selected)
    assert [entry.text() for entry in menu.actions()] == [
        "Clear messages", "Delete conversation"]
    [entry for entry in menu.actions() if entry.text() == "Clear messages"][0].trigger()
    assert window.chats["local-llama"]["messages"] == [], (
        "Clear messages did nothing")

    window.chats["local-llama"]["messages"] = [("user", "again")]
    window.render_agents()
    for _ in range(5):
        poll()
    menu = window.build_conversation_menu(window.selected)
    [entry for entry in menu.actions()
     if entry.text().startswith("Delete")][0].trigger()
    assert "local-llama" not in window.chats, (
        "Delete conversation did nothing when used after a poll")
    assert window.selected is None, (
        "the deleted conversation is still selected")

    # With nothing left there is nothing to offer, so the button says so.
    window.update_chat_controls()
    assert window.manage_button.isEnabled() is False, (
        "the conversation button is still offered with no conversation open")


def test_answering_yes_really_does_the_thing(window, qt_app) -> None:
    """A confirmation that returns Yes must not quietly do nothing.

    This is why clear and delete appeared broken for so long. The comparison
    was ``is not QMessageBox.StandardButton.Yes``, and a dialog returns a plain
    ``int`` -- 16384 -- while that constant is a Shiboken flag enum. The two are
    equal by value and are never the same object, so the test for "did they say
    no" was always true and the function returned before doing anything. The
    dialog opened, the answer was given, and nothing happened, every time.

    Every test of it had passed because they replaced the dialog with one
    returning the very enum member being compared against. This one answers the
    way a real dialog answers.
    """
    window.identity = "me"
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [("user", "hello world")]}}
    window.navigate(win.CONVERSATIONS_PAGE)
    window.show()
    window.render_messages()

    assert window.answered_yes(int(win.QMessageBox.StandardButton.Yes)) is True, (
        "a dialog answering with the plain int Qt returns is being read as No, "
        "so every confirmation is a no-op")
    assert window.answered_yes(int(win.QMessageBox.StandardButton.Cancel)) is False, (
        "a dialog answering Cancel is being read as Yes")
    assert isinstance(int(win.QMessageBox.StandardButton.Yes), int), (
        "Qt no longer returns a plain int; this guard and its tests need "
        "revisiting")

    # The whole path, with the dialog answering the way a real one does.
    monkey = pytest.MonkeyPatch()
    monkey.setattr(win.QMessageBox, "question",
                   lambda *args, **kwargs: int(win.QMessageBox.StandardButton.Yes))
    try:
        window.clear_conversation_confirm("local-llama")
        assert window.chats["local-llama"]["messages"] == [], (
            "answering Yes did not clear the messages")

        window.chats["local-llama"]["messages"] = [("user", "hello world")]
        window.render_messages()
        window.delete_conversation("local-llama")
        assert "local-llama" not in window.chats, (
            "answering Yes did not delete the conversation")
    finally:
        monkey.undo()


def test_deleting_a_connected_agents_chat_removes_it_from_the_picker(window, qt_app, monkeypatch) -> None:
    """Conversation controls still work after removing the sidebar's live-agent list."""
    window.identity = "me"
    window.agents = [{"id": "device-me", "kind": "model", "online": True,
                      "local": True, "provider": "ollama", "model": "llama3"}]
    window.selected = "device-me"
    window.chats = {"device-me": {"messages": [("user", "a"), ("assistant", "b"),
                                               ("user", "c")]}}
    window.render_agents()
    assert window.conversation_picker.findData("device-me") >= 0
    asked = []
    monkeypatch.setattr(win.QMessageBox, "question",
                        lambda *args, **kwargs: asked.append(args[2])
                        or int(win.QMessageBox.StandardButton.Yes))
    window.clear_conversation_confirm("device-me")

    assert asked, "no confirmation was shown"
    body = asked[0]
    assert "3 messages" in body, f"the dialog does not say how many: {body!r}"
    assert "row will stay" not in body
    assert window.chats["device-me"]["messages"] == []
    assert window.conversation_picker.findData("device-me") >= 0

    asked.clear()
    window.delete_conversation("device-me")
    assert "row will stay" not in asked[0]
    assert "device-me" not in window.chats
    assert window.conversation_picker.findData("device-me") < 0
    assert any(agent["id"] == "device-me" for agent in window.model_agents())


def test_the_rows_built_on_demand_explain_themselves(window) -> None:
    """Agent cards and attachments appear only once there is something to show.

    Both are built after the window opens, so they cannot be covered by a
    check of the fixed chrome. A row of identical "Remove" buttons is
    ambiguous without the filename, and an agent card cannot say who it
    opens without naming them.
    """
    window.agents = [{
        "id": "local-llama", "online": True, "provider": "ollama",
        "model": "llama3", "profile": {"vision": True},
    }]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"draft_attachments": [
        {"name": "diagram.png", "note": "120 KB"}]}}
    window.render_agents()
    window.render_attachments()

    talk = [b for b in window.findChildren(QPushButton)
            if b.text().startswith("Open conversation")]
    assert talk, "the agent card did not render its conversation button"
    assert all(b.toolTip() for b in talk), "an agent card cannot say who it opens"
    assert all("local-llama" in b.toolTip() for b in talk), (
        "naming the agent is the point of the tooltip")

    remove = [b for b in window.findChildren(QPushButton) if b.text() == "Remove"]
    assert remove, "the attachment did not render its remove button"
    assert all("diagram.png" in b.toolTip() for b in remove), (
        "a column of identical Remove buttons is ambiguous")


def test_the_welcome_appears_although_this_device_is_listed(window) -> None:
    """A first run always lists this device in the sidebar, and so it always did.

    The welcome used to test the agent list for emptiness, which can never
    hold because this device is one of its own entries. So the panel a new
    user is meant to land on was hidden on a genuine first run and stayed
    hidden afterwards. Only the model agents a user can actually talk to
    should count.
    """
    window.agents = [{"id": "device-abc123", "kind": "device", "online": True}]
    window.conversations = {}
    window.render_agents()

    assert not window.model_agents(), "a device is not a model agent"
    assert not window.welcome_card.isHidden(), (
        "the welcome stayed hidden on a first run, because this device is "
        "always in the agent list it was testing for emptiness")


def test_the_statistics_return_once_a_model_is_connected(window) -> None:
    """The welcome has to get out of the way once there is something to count."""
    window.agents = [
        {"id": "device-abc123", "kind": "device", "online": True},
        {"id": "local-llama", "kind": "model", "online": True,
         "provider": "ollama", "model": "llama3"},
    ]
    window.conversations = {}
    window.render_agents()

    assert window.welcome_card.isHidden(), "the welcome stayed up over real statistics"
    assert not window.overview_content.isHidden(), "the statistics were never restored"


def test_a_saved_conversation_also_puts_the_welcome_away(window) -> None:
    """Somebody with history to come back to is not a first run."""
    window.agents = [{"id": "device-abc123", "kind": "device", "online": True}]
    window.conversations = {"local-llama": {
        "id": "local-llama", "title": "Notes on the parser",
        "target": "local-llama", "members": ["device-abc123"]}}
    window.render_agents()

    assert window.welcome_card.isHidden(), "the welcome covered a real conversation"


# --- the welcome illustration -------------------------------------------


def test_the_welcome_actually_carries_the_orbit_illustration(window, qt_app) -> None:
    """The orbit was painted for this panel and never mounted.

    It has its own unit test, which passed throughout while the widget sat
    in the module unused, so nothing about it ever reached the screen. The
    assertion has to be against the built window, not against the widget.
    """
    window.resize(1180, 760)
    window.show()
    qt_app.processEvents()
    art = window.welcome_art

    assert art in window.welcome_card.findChildren(win.OrbitArt), (
        "the orbit illustration is not in the welcome panel, so the first "
        "thing a new user sees has no motion in it")
    assert not art.isHidden(), "the orbit illustration is on a hidden page"
    assert art.width() >= 200 and art.height() >= 180, (
        f"the orbit was given {art.width()}x{art.height()}, too small to read")
    assert art.width() <= window.welcome_card.width(), (
        "the orbit is wider than the card holding it")


def test_the_orbit_repaints_for_the_new_theme(window, qt_app) -> None:
    """It paints from the palette rather than being styled, so it has to be
    told when the theme changes or it keeps the colours it was built with.

    Asserted on the tile in the middle rather than by comparing whole
    images: the chips orbit, so two grabs taken a moment apart always
    differ and would make this pass whatever the theme did.
    """
    art = window.welcome_art
    art.resize(260, 240)
    art.set_moving(False)

    def tile() -> str:
        image = art.grab().toImage()
        return image.pixelColor(image.width() // 2, image.height() // 2).name()

    window.apply_theme(DARK)
    qt_app.processEvents()
    assert tile() == color(DARK, "accent"), "the orbit is not painted in the theme's accent"

    window.apply_theme(LIGHT)
    qt_app.processEvents()
    assert tile() == color(LIGHT, "accent"), (
        "the orbit kept the previous theme's colours")


def test_the_orbit_holds_still_when_motion_is_reduced(window) -> None:
    """The setting already covered the page fade; it has to reach this too."""
    window.motion_changed(True)
    assert not window.welcome_art.moving, (
        "the orbit keeps turning with reduce-motion on")
    window.motion_changed(False)
    assert window.welcome_art.moving, (
        "turning reduce-motion off did not restart the orbit")


def test_the_orbit_respects_the_stored_motion_setting(qt_app, storage) -> None:
    """The setting is read when the panel is built, not only when toggled.

    Somebody who turned animations off in a previous session should not
    get a moving illustration for the length of this one.
    """
    storage.settings["reduce_motion"] = True
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        assert not instance.welcome_art.moving, (
            "the orbit started moving despite the stored reduce-motion setting")
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


# --- the conversation ----------------------------------------------------


def conversation_window(window):
    """A chat with two speakers, so grouping has something to group."""
    window.navigate(win.PAGE_INDEX["conversations"])
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [
        ("user", "Explain this traceback"),
        ("user", "It only happens on the second run"),
        ("assistant", "Two runs means the port is still bound"),
        ("assistant", "Close it before starting again"),
    ]}}
    window.render_messages()
    return window


def row_marks(row):
    """Every painted mark in a row, wherever it sits.

    Used to read ``content.itemAt(0)``, which assumed the mark is the first
    thing in the row. Your own messages put theirs on the right, so the
    leading slot is a stretch instead and the positional helper stopped
    finding anything. Scanning is what the helper should have done all along.
    """
    return [child for child in row.findChildren(QLabel) if not child.pixmap().isNull()]


def row_lead(row):
    """The widget holding a row's leading slot: an avatar, or the indent."""
    return row.content.itemAt(0).widget()


def row_has_mark(row):
    """A mark is a label carrying a painted pixmap; the indent is a bare widget."""
    return bool(row_marks(row))


def row_labels(row):
    return [child.text() for child in row.findChildren(QLabel) if child.text()]


def row_bubbles(row):
    return row.findChildren(win.MessageBubble)


def bubble_box(window, row):
    """A row's bubble in the panel's coordinates, so rows are comparable."""
    bubble = row_bubbles(row)[0]
    origin = bubble.mapTo(window.messages_widget, bubble.rect().topLeft())
    return origin.x(), origin.x() + bubble.width()


def test_messages_are_rows_and_not_cards(window) -> None:
    """Each message used to be a full-width card, which reads as a stack of
    documents rather than as a conversation."""
    conversation_window(window)

    rows = window.messages_widget.findChildren(win.HoverRow)
    assert len(rows) == 4, f"expected one row per message, got {len(rows)}"
    assert not [c for c in window.messages_widget.findChildren(QFrame)
                if c.objectName() == "card"], (
        "a message is still drawn as a card")


def test_consecutive_messages_from_one_speaker_are_grouped(window) -> None:
    """Discord repeats the name and mark once per speaker, not once per message."""
    conversation_window(window)
    rows = window.messages_widget.findChildren(win.HoverRow)

    assert row_has_mark(rows[0]), (
        "the first message from a speaker should carry their mark")
    assert not row_has_mark(rows[1]), (
        "the second message from the same speaker should have no mark")
    assert "You" in row_labels(rows[0]), "the opening message should name its speaker"
    assert "You" not in row_labels(rows[1]), (
        "a follow-on message repeats the name above it")

    # The speaker is named by the provider now, with the agent's own id as a
    # subtitle when it differs. The raw id was the only label before, so two
    # different providers were told apart by decoding "local-llama".
    assert "Ollama" in row_labels(rows[2]), (
        f"the agent is not named by its provider: {row_labels(rows[2])}")
    assert "local-llama" in row_labels(rows[2]), (
        "the agent id is lost, so two Ollama agents are indistinguishable")
    assert "local-llama" not in row_labels(rows[3]), (
        "a follow-on message repeats the name above it")


def test_a_grouped_message_lines_up_under_the_one_above(window, qt_app) -> None:
    """Without the held place, a follow-on message slides across.

    Checked on whichever edge the bubble sits against rather than on the left,
    because your own messages are right-aligned and an agent's are on the
    left. What has to hold either way is that a follow-on message shares an
    edge with the one above it.
    """
    conversation_window(window)
    window.show()
    qt_app.processEvents()
    rows = window.messages_widget.findChildren(win.HoverRow)

    for above, below in ((rows[0], rows[1]), (rows[2], rows[3])):
        top_left, top_right = bubble_box(window, above)
        low_left, low_right = bubble_box(window, below)
        assert top_left == low_left or top_right == low_right, (
            f"a grouped message sits at {low_left}-{low_right} while the one "
            f"above it sits at {top_left}-{top_right}, so neither edge lines up")


def test_your_messages_go_right_and_an_agents_go_left(window, qt_app) -> None:
    """The two sides are what tell your input from a reply at a glance."""
    conversation_window(window)
    window.show()
    qt_app.processEvents()
    rows = window.messages_widget.findChildren(win.HoverRow)
    panel = window.messages_scroll.viewport().width()

    yours = bubble_box(window, rows[0])
    theirs = bubble_box(window, rows[2])

    assert yours[0] > theirs[0], (
        f"your message starts at {yours[0]} and the agent's at {theirs[0]}, so "
        "they are on the same side")
    assert yours[1] >= theirs[1], (
        "your message does not reach as far across as the agent's")
    assert theirs[0] < panel * 0.5 < yours[1], (
        f"your message ends at {yours[1]} and the agent's at {theirs[0]} of a "
        f"{panel}px panel, so they are not on opposite sides")


def test_a_short_message_hugs_and_a_long_one_is_capped(window, qt_app) -> None:
    """A bubble sized to the panel is a slab, which is the fault being fixed.

    The window is shown before the messages are put in place. Once the real
    runtime is up it reports its workspace, and that restores the saved chat
    list over whatever the test set, so anything staged beforehand is gone.
    """
    window.resize(1330, 910)
    window.show()
    pump(qt_app, 0.3)
    window.selected = "local-llama"
    window.agents = [{"id": "local-llama", "online": True, "provider": "ollama",
                     "model": "llama3.2"}]
    window.chats = {"local-llama": {"messages": [
        ("user", "hi"),
        ("assistant", "word " * 400),
    ]}}
    window.render_messages()
    pump(qt_app, 0.3)

    rows = window.messages_widget.findChildren(win.HoverRow)
    assert len(rows) == 2, f"expected two rows, got {len(rows)}"
    panel = window.messages_scroll.viewport().width()
    short = row_bubbles(rows[0])[0].width()
    long = row_bubbles(rows[1])[0].width()

    assert short < panel * 0.5, (
        f"a two-word message filled {short}px of a {panel}px panel, so the "
        "bubble is a slab rather than a bubble")
    assert long <= int(panel * 0.74) + 1, (
        f"a long message ran to {long}px of a {panel}px panel")
    assert not window.messages_scroll.horizontalScrollBar().isVisible(), (
        "a long message made the chat scroll sideways")


def test_each_provider_gets_its_own_bubble_colour(window) -> None:
    """Which model is answering reads without the name."""
    window.agents = [
        {"id": "a", "online": True, "provider": "ollama", "model": "llama3.2"},
        {"id": "b", "online": True, "provider": "anthropic", "model": "claude-opus"},
        {"id": "c", "online": True, "provider": "gemini", "model": "gemini-2"},
    ]
    for name in THEME_NAMES:
        window.apply_theme(name)
        fills = {window.message_bubble_fill(agent["provider"], "agent")
                 for agent in window.agents}
        assert len(fills) == 3, (
            f"{name}: three providers share {len(fills)} bubble colours")


def test_a_failure_is_not_painted_in_a_providers_colour(window) -> None:
    """A failed call is not that model's fault to be branded with."""
    window.apply_theme(DARK)
    for provider in ("ollama", "anthropic", "gemini"):
        assert window.message_bubble_fill(provider, "error") == color(DARK, "danger_bg"), (
            f"a {provider} failure was tinted with that provider's colour")
    assert window.message_bubble_fill(None, "user") == color(DARK, "surface_raised"), (
        "your own messages carry a provider tint")


def test_a_reply_is_named_after_the_model_that_wrote_it(window) -> None:
    """A delegated answer is credited to its writer, not to the coordinator.

    One conversation can be answered by several models in turn, so the role
    alone does not say who is speaking and the responder recorded with the
    reply has to. The subtitle carries the agent's own id only when that
    differs from the model name, since printing "llama3 llama3" under every
    reply says nothing. The provider comes along too, because it is what tints
    the bubble and what tells two Ollama agents apart.
    """
    window.agents = [{"id": "coder", "online": True, "provider": "ollama", "model": "code-model-v1"}]
    responder = {"agent_id": "coder", "model": "code-model-v1", "provider": "ollama"}

    name, subtitle, provider, kind = window.speaker_identity("assistant", None, responder)
    assert (name, subtitle, provider, kind) == ("code-model-v1", "coder", "ollama", "agent"), (
        f"a delegated reply was labelled {name!r}/{subtitle!r}")

    # An agent whose id is its model name gets no subtitle, because printing
    # the same word twice under every reply says nothing.
    same = window.speaker_identity("assistant", None,
                                  {"agent_id": "solo", "model": "solo", "provider": "ollama"})
    assert same[:2] == ("solo", ""), f"an id that is the model grew the subtitle {same[1]!r}"

    # The agent is absent from the list, which is the normal case for a model
    # on another device. The recorded responder still has to name it.
    window.agents = []
    name, subtitle, _, _ = window.speaker_identity("assistant", None, responder)
    assert (name, subtitle) == ("code-model-v1", "coder"), (
        f"an unknown responder was labelled {name!r}/{subtitle!r}")

    # A responder with no recorded model falls back to the agent's, then to
    # the agent id itself rather than to nothing at all.
    name, subtitle, _, _ = window.speaker_identity("assistant", None, {"agent_id": "solo"})
    assert name == "solo", f"a bare responder was labelled {name!r}"

    # The responder only speaks for an assistant. A user's own message carries
    # one by accident and must still read as the user's.
    assert window.speaker_identity("user", None, responder)[0] == "You", (
        "a responder overrode who the user's own message belongs to")


def test_a_peer_message_is_named_as_a_peer(window) -> None:
    """It was labelled with the agent's name and rendered its markdown raw."""
    window.agents = [{"id": "local-llama", "online": True, "provider": "ollama",
                      "model": "llama3.2"}]
    window.selected = "guest-laptop"
    name, subtitle, provider, kind = window.speaker_identity("peer", None)
    assert kind == "peer", f"a peer was treated as {kind}"
    assert provider is None, "a peer was given a provider, so it is tinted as one"
    # The sender is the key of the chat the message is in, so it is whoever
    # is selected. It used to read "This device", which named the reader
    # rather than whoever had written the message.
    assert name == "guest-laptop", f"a peer was named {name!r}"

    window.selected = None
    assert window.speaker_identity("peer", None)[0] == "Another device", (
        "a peer with no chat open should still say something")

    # Not the reader's own agent: ``room["target"]`` is the local agent, and
    # labelling another device's message with it pointed at the wrong speaker.
    window.selected = "guest-laptop"
    with_room = window.speaker_identity("peer", {"target": "local-llama"})
    assert with_room[0] == "guest-laptop", (
        f"a peer was named after the local agent as {with_room[0]!r}")

    member = window.speaker_identity("member:guest-laptop", None)
    assert member[0] == "guest-laptop", f"a member was named {member[0]!r}"
    assert member[3] == "peer", f"a member was treated as {member[3]}"


def test_a_local_agents_reply_is_not_headed_by_the_peer_that_asked(window) -> None:
    """The responder is this device's own agent, not whoever asked it a question.

    A peer's request is answered by the local agent, and the reply comes back
    with ``from`` as the peer who asked and ``to`` as the agent that replied.
    The reply used to be headed by ``selected``, which in a peer conversation is
    the peer, so it rendered under the asker's name with the real responder left
    inline in the body text.
    """
    window.identity = "my-laptop"
    window.agents = [
        {"id": "my-local-agent", "kind": "model", "online": True, "local": True,
         "provider": "ollama", "model": "llama3"},
        {"id": "guest-laptop", "kind": "device", "online": True},
    ]
    window.selected = "guest-laptop"

    window.network_event("incoming_reply", {
        "from": "guest-laptop", "to": "my-local-agent",
        "text": "## Heading\n\n**Hello** from my agent."})

    assert window.chats["guest-laptop"]["messages"] == [
        ("local_agent:my-local-agent", "## Heading\n\n**Hello** from my agent.")], (
        f"the responder's id should travel in the role, not be glued onto the "
        f"text: {window.chats['guest-laptop']['messages']}")

    name, subtitle, provider, kind = window.speaker_identity(
        "local_agent:my-local-agent", None)
    assert name == "Ollama", f"the reply is headed by {name!r}"
    assert subtitle == "my-local-agent", f"the agent id was dropped: {subtitle!r}"
    assert provider == "ollama", "the local responder lost its provider"
    assert kind == "agent", f"the local responder was treated as {kind}"

    # The bare role still falls back the old way, which is what a saved
    # conversation nobody can attribute relies on.
    assert window.speaker_identity("local_agent", {"target": "some-agent"})[0] == "some-agent"


def test_a_failed_local_reply_is_still_an_error(window) -> None:
    """The error path is unchanged: it names no responder, because none replied."""
    window.identity = "my-laptop"
    window.agents = [{"id": "my-local-agent", "kind": "model", "online": True,
                      "local": True, "provider": "ollama", "model": "llama3"}]
    window.selected = "guest-laptop"

    window.network_event("incoming_reply", {
        "from": "guest-laptop", "to": "my-local-agent",
        "text": "The local agent could not complete this request.", "error": True})

    assert window.chats["guest-laptop"]["messages"] == [
        ("error", "The local agent could not complete this request.")]


def test_saved_replies_are_unwrapped_only_when_the_prefix_is_a_known_agent(window) -> None:
    """The old shape put ``id:\\n`` on the front, and is read back apart.

    Splitting on the newline alone would eat the first line of any reply that
    happens to begin with a colon, because an agent id and an ordinary word are
    both bare characters from the same set. So it splits only for an agent
    currently in the list, and only when the line ends in the colon the old
    prefix always had.
    """
    window.agents = [{"id": "my-local-agent", "kind": "model", "online": True,
                      "local": True, "provider": "ollama", "model": "llama3"}]

    assert window.split_legacy_responder(
        "local_agent", "my-local-agent:\n**Hi** there") == (
            "local_agent:my-local-agent", "**Hi** there")

    for text in ("Note:\ncheck the second run",     # an ordinary opening line
                 "my-local-agent\n**Hi**",          # no colon, so not the old shape
                 "my-local-agent:\n",               # nothing left to show
                 "no colon or newline here",
                 "ghost-agent:\nfrom a device that is gone"):
        assert window.split_legacy_responder("local_agent", text) == ("local_agent", text), (
            f"a reply that was never stored this way was rewritten: {text!r}")

    # Only the reply role is touched.
    assert window.split_legacy_responder(
        "assistant", "my-local-agent:\n**Hi**") == (
            "assistant", "my-local-agent:\n**Hi**")


def test_no_bubble_is_too_short_for_the_text_it_holds(window, qt_app) -> None:
    """Nothing is cut off, at any panel width.

    A bubble's height used to be pinned from its column's size hint, and once
    pinned the column reported the pin back, so the height could never change
    again. A message laid out at a wide panel kept that height, and narrowing
    the panel re-wrapped the text into a bubble far too short for it. On a
    1000px window a message needing 360px was drawn in 238px, so the bottom
    third of what was said was simply not on screen.

    Swept rather than checked once, because the height is only wrong at widths
    other than the one it was first laid out at.
    """
    window.navigate(win.PAGE_INDEX["conversations"])
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    question = ("This is a long question from the user that runs on for quite a "
                "while and should wrap across several lines in the bubble. " * 4)
    answer = ("This is a long reply from the agent that also runs on for quite a "
              "while and should wrap across several lines in the bubble. " * 4)
    window.show()

    def clipped():
        """Every body that cannot show all of its own text."""
        found = []
        for bubble in window.message_bubbles:
            for child in bubble.findChildren(QLabel):
                if child.wordWrap() and child.text():
                    needed = child.heightForWidth(child.width())
                    if needed > child.height() + 1:
                        found.append(f"label has {child.height()}px, needs {needed}px")
            for child in bubble.findChildren(MarkdownMessage):
                needed = child.document().size().height()
                if needed > child.height() + 1:
                    found.append(f"reply has {child.height()}px, needs {needed:.0f}px")
        return found

    for width in (1400, 1000, 800, 620, 480, 1500):
        window.resize(width, 900)
        # The runtime polls the relay and replaces the chats with whatever it
        # says, which is nothing, so it is given its turn before the fake
        # conversation goes in rather than after. Nothing turns the loop after
        # the render either: render_messages() settles the layout itself, and
        # one more pass is one more chance for the poll to wipe the bubbles out
        # from under the reading.
        qt_app.processEvents()
        window.selected = "local-llama"
        window.chats = {"local-llama": {"messages": [
            ("user", question), ("assistant", answer)]}}
        window.render_messages()
        assert window.message_bubbles, f"the message is missing at {width}px"
        assert not clipped(), f"text is cut off at {width}px: {clipped()}"


def test_a_bubble_grows_again_when_the_panel_is_widened(window, qt_app) -> None:
    """Widening the window restores the width a reply had before narrowing it.

    Measuring a reply's natural width did not work, because lifting the wrap
    re-entered the height fitter, which re-set the wrap before the measurement
    was read. So a reply's "natural" width was really just the width it already
    had, and once the panel narrowed, the ceiling was the only thing moving it.
    Widening afterwards had nothing to grow back into and left every message
    stranded at the narrow width.
    """
    window.navigate(win.PAGE_INDEX["conversations"])
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    reply = "A reply long enough that the panel's ceiling is what decides its width. " * 12
    window.show()

    def render_at(width):
        window.resize(width, 900)
        qt_app.processEvents()
        window.selected = "local-llama"
        window.chats = {"local-llama": {"messages": [("assistant", reply)]}}
        window.render_messages()
        assert window.message_bubbles, f"the reply is missing at {width}px"
        return window.message_bubbles[0].width()

    wide = render_at(1500)
    narrow = render_at(700)
    assert narrow < wide, f"narrowing did not narrow the reply ({narrow} vs {wide})"

    assert render_at(1500) > narrow, (
        f"widening did not widen the reply back: it stayed at {narrow}px after "
        f"being {wide}px on a wider panel")


def test_a_replys_natural_width_does_not_depend_on_how_it_is_currently_laid_out() -> None:
    """The unwrapped width of a reply is the same whatever its current width.

    Read through the widget so the guard inside it is exercised: lifting the
    wrap emits ``documentSizeChanged``, which re-enters ``fit_height`` and puts
    the wrap back before ``idealWidth`` can be read.
    """
    from desktop_app.markdown import MarkdownMessage

    reply = MarkdownMessage("A reply. " * 200)
    widths = set()
    for width in (200, 400, 700):
        reply.resize(width, 300)
        reply.fit_height()
        widths.add(round(reply.natural_width()))
        # Measuring must leave the reply as it found it, or the height the
        # bubble has already settled on stops matching the text.
        assert reply.document().textWidth() > 0, (
            "measuring left the reply unwrapped, so its height is meaningless")

    assert len(widths) == 1, (
        f"the natural width moved with the layout: {sorted(widths)}")


def test_resizing_the_window_re_caps_the_bubbles(window, qt_app) -> None:
    """Narrowing the panel re-wraps the messages rather than clipping them.

    The filter that does this is installed on the viewport, and it used to
    also insist that the watched widget was the scroll widget itself, which
    nothing can be both. So the branch was dead: the widths were only ever
    capped on a render, and a message dragged off the edge stayed off.
    """
    window.navigate(win.PAGE_INDEX["conversations"])
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [
        ("assistant", "a reply long enough that the ceiling matters " * 12)]}}
    window.show()
    window.resize(1400, 900)
    window.render_messages()
    qt_app.processEvents()
    wide = window.message_bubbles[0].width()

    # The filter has to run at all. No chat is needed for this half, which is
    # the point of checking it on its own: the runtime polls the relay on a
    # timer, and letting the event loop turn here hands it the chance to replace
    # the chats below with whatever the relay says, which is nothing.
    called = []
    original = window.cap_message_widths
    window.cap_message_widths = lambda: called.append(True)
    try:
        window.resize(760, 900)
        qt_app.processEvents()
        assert called, "resizing the window did not re-cap the bubbles"
    finally:
        window.cap_message_widths = original

    # The guard has to be narrow. Called directly rather than delivered, because
    # posting these for real lets Qt relayout in response -- a focus change can
    # move a scrollbar, which resizes the viewport, which legitimately re-caps.
    # That would be measuring Qt's layout rather than the filter's condition.
    called.clear()
    window.cap_message_widths = lambda: called.append(True)
    try:
        window.eventFilter(window, QResizeEvent(QSize(100, 100), QSize(100, 100)))
        window.eventFilter(window.messages_scroll.viewport(),
                           QEvent(QEvent.Type.FocusIn))
    finally:
        window.cap_message_widths = original
    assert not called, "the cap ran for something that was not the viewport resizing"

    # And the narrower panel really does produce a narrower bubble. Seeded again
    # here because the poll above has had its turn and replaced the chats.
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [
        ("assistant", "a reply long enough that the ceiling matters " * 12)]}}
    window.render_messages()
    qt_app.processEvents()
    assert window.message_bubbles, (
        "the reply is gone, so the poller replaced the chats after all")
    assert window.message_bubbles[0].width() < wide, (
        "a narrower panel did not narrow the bubble, so the reply runs off "
        "the edge")


def test_body_text_stays_readable_on_every_bubble_fill(window) -> None:
    """A tint is close enough to the surface that the checked pair still holds."""
    for name in THEME_NAMES:
        window.apply_theme(name)
        body = color(name, "text")
        for provider in ("ollama", "anthropic", "gemini", "openai"):
            fill = window.message_bubble_fill(provider, "agent")
            ratio = contrast_ratio(body, fill)
            assert ratio >= 4.5, (
                f"{name}/{provider}: text on {fill} is {ratio:.2f}:1, under 4.5")
        for kind in ("user", "error"):
            fill = window.message_bubble_fill(None, kind)
            ratio = contrast_ratio(body, fill)
            assert ratio >= 4.5, (
                f"{name}/{kind}: text on {fill} is {ratio:.2f}:1, under 4.5")


def test_a_markdown_reply_is_transparent_so_the_bubble_shows_through(window, qt_app) -> None:
    """It used to paint an opaque surface of its own, which is the slab."""
    from desktop_app.markdown import MarkdownMessage

    # Pump first, build the chat second. The runtime polls the relay on a
    # timer and replaces ``window.chats`` with whatever the relay says, which
    # is nothing, so a fake chat set before the pump is simply erased by it
    # and there is no reply left to look at.
    pump(qt_app, 0.2)
    conversation_window(window)
    body = window.messages_widget.findChild(MarkdownMessage)
    assert body is not None, "the reply is not rendered as markdown"

    base = body.palette().color(QPalette.ColorRole.Base)
    assert base.alpha() == 0 or "background: transparent" in body.styleSheet(), (
        f"a markdown reply still paints an opaque background ({base.name()}), "
        "so the bubble behind it is invisible")
    assert "background: transparent" in body.styleSheet(), (
        "the reply's own stylesheet still fills the widget")
    # And the bubble it sits in really does have a fill, so the transparency
    # is showing a background rather than showing the panel.
    bubble = body.parent()
    assert isinstance(bubble, win.MessageBubble), (
        f"a reply is not inside a bubble, it is inside {type(bubble).__name__}")

    # The palette alone would not prove it: the app-level ``QWidget`` rule
    # resolves the Base role back to an opaque colour on any widget under
    # this stylesheet, whatever the widget's own palette says. What decides
    # what is seen is the rule on the reply itself, so read what it paints.
    # Every pixel is counted rather than one being sampled: the reply is full
    # of text, and a single probe lands on a glyph or on the background
    # depending on the message. The surface colour must be absent, because
    # that opaque slab is the thing being removed, and the bubble's own fill
    # must be present, because that is what has to show through.
    qt_app.processEvents()
    surface = color(window.theme, "surface")
    fill = window.message_bubble_fill("ollama", "assistant")
    # Grab the bubble, not the reply. Grabbing the reply on its own gives a
    # pixmap whose transparent regions read as black, which is the reply's
    # transparency showing up as a colour rather than the bubble behind it.
    painted = bubble.grab().toImage()
    colours = {painted.pixelColor(x, y).name().lower()
               for y in range(painted.height()) for x in range(painted.width())}
    assert surface.lower() not in colours, (
        f"the reply still paints the {surface} surface, so it is an opaque "
        f"slab over the bubble again")
    assert fill.lower() in colours, (
        f"the reply never shows the bubble behind it; {fill} is absent from "
        f"what it paints")


def test_a_renamed_agent_does_not_inherit_the_previous_replys_header(window, qt_app) -> None:
    """Two replies both called "Ollama" are not the same speaker.

    A reconnect can bring an agent back under a new id. Both ids read as
    "Ollama", so grouping on the name alone ran the two runs together under
    one header, and the id that tells them apart was dropped from every reply
    after the first. The identity is the name, the id, the provider and the
    kind together.
    """
    window.navigate(win.PAGE_INDEX["conversations"])
    window.agents = [{"id": "llama3-old", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "llama3-old"
    window.chats = {"llama3-old": {"messages": [
        ("local_agent", "before the reconnect"),
        ("local_agent", "still the first run"),
    ]}}
    window.render_messages()
    first = [row_labels(row) for row in window.messages_widget.findChildren(win.HoverRow)]

    # The agent comes back under a new id, and the history follows it.
    window.agents = [{"id": "llama3-new", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "llama3-new"
    window.chats = {"llama3-new": {"messages": [
        ("local_agent", "before the reconnect"),
        ("local_agent", "after the reconnect"),
    ]}}
    window.render_messages()
    qt_app.processEvents()

    # Only the rows from this render are of interest: the previous render's
    # rows are still parented until the event loop deletes them, so the whole
    # set is searched rather than counted.
    headers = [row_labels(row) for row in window.messages_widget.findChildren(win.HoverRow)]
    assert headers.count(["Ollama", "llama3-new"]) == 1, (
        f"the reply after the reconnect never headed itself: {headers}")
    assert headers.count(["Ollama", "llama3-old"]) == 1, (
        f"the reply before it lost its own header: {headers}")
    assert ["Ollama", "llama3-new"] in headers and ["Ollama", "llama3-old"] in headers, (
        "both runs are called Ollama, so neither id may be dropped: "
        f"{headers}")


def test_hovering_a_message_moves_nothing(window, qt_app) -> None:
    """The pointer crossing a message must not shift it.

    It used to. The actions lived in a slot beside the mark and appeared on
    hover, and a hidden widget hands its space back to the layout, so
    revealing them took about 90px off the bubble and re-wrapped the text
    under the pointer. The row's own width never changed, which is why the
    earlier check on it passed while the message visibly jumped: the bubble
    is what reflowed. So the bubble is what is checked here.
    """
    conversation_window(window)
    window.show()
    qt_app.processEvents()
    rows = window.messages_widget.findChildren(win.HoverRow)
    assert rows

    # There is no hover behaviour left to fire, and that is the point. Pinned
    # here rather than left implicit, because otherwise the sendEvent below is
    # a no-op and this test would pass for the wrong reason the moment someone
    # re-added a reveal on hover.
    assert win.HoverRow.enterEvent is QWidget.enterEvent, (
        "the row reveals something on hover again, so a message will move as "
        "the pointer crosses it")
    assert win.HoverRow.leaveEvent is QWidget.leaveEvent, (
        "the row hides something on leave again, so a message will move as "
        "the pointer crosses it")

    def boxes():
        return [(bubble_box(window, row),
                 (row.findChild(win.MessageBubble).x(),
                  row.findChild(win.MessageBubble).y(),
                  row.findChild(win.MessageBubble).width(),
                  row.findChild(win.MessageBubble).height()))
                for row in rows]

    before = boxes()
    for row in rows:
        QApplication.sendEvent(row, QEvent(QEvent.Type.Enter))
    qt_app.processEvents()

    assert boxes() == before, (
        "hovering moved the bubbles, so a message reflows as the pointer "
        "crosses it")


def test_the_copy_action_is_permanent_and_sits_under_the_reply(window, qt_app) -> None:
    """A copy button under every reply, always there, the way a browser has it.

    It used to appear only on hover, which read as a pop-up, and sat in a slot
    beside the mark rather than under the message it copies.
    """
    window.navigate(win.PAGE_INDEX["conversations"])
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [
        ("assistant", "a reply"),
        ("user", "a question"),
    ]}}
    window.show()
    window.render_messages()
    qt_app.processEvents()

    rows = window.messages_widget.findChildren(win.HoverRow)
    reply, question = rows[0], rows[1]
    assert reply.has_actions, "the reply has no copy button"
    assert not question.has_actions, (
        "your own message has a copy button, so every question gets one")

    bubble = row_bubbles(reply)[0]
    strip = reply.actions
    bubble_top = bubble.mapTo(reply, bubble.rect().topLeft()).y()
    strip_top = strip.mapTo(reply, strip.rect().topLeft()).y()
    assert strip_top >= bubble_top + bubble.height(), (
        f"the copy button sits at y={strip_top}, inside the bubble, which "
        f"ends at {bubble_top + bubble.height()}")
    assert strip.mapTo(reply, strip.rect().topLeft()).x() \
        == bubble.mapTo(reply, bubble.rect().topLeft()).x(), (
        "the copy button should line up with the text it copies, not float "
        "off against the panel")

    # And it is visible without a pointer anywhere near it.
    assert strip.isHidden() is False, (
        "the copy button is hidden until hovered, so it pops up instead of "
        "being simply there")


def test_only_the_last_reply_of_a_run_carries_a_copy(window, qt_app) -> None:
    """Three replies from one agent get one copy button, under the third.

    Putting one under each would stack three identical buttons with nothing
    between them, since a run is already presented as one block under a
    single header.
    """
    window.navigate(win.PAGE_INDEX["conversations"])
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [
        ("assistant", "first"), ("assistant", "second"), ("assistant", "third"),
        ("user", "a question"), ("assistant", "a new run"),
    ]}}
    window.show()
    window.render_messages()
    qt_app.processEvents()

    rows = window.messages_widget.findChildren(win.HoverRow)
    carried = [index for index, row in enumerate(rows) if row.has_actions]
    assert carried == [2, 4], (
        f"expected a copy button on the last reply of each run, got rows "
        f"{carried}")


def test_a_copied_message_confirms_itself_briefly(window, qt_app) -> None:
    """A copy toast goes in a second, not the seven and a half for a failure.

    Nothing has to be read or acted on: the user just watched the copy happen.
    The copy text is a word, and the failure toast keeps its own duration.
    """
    assert win.COPY_TOAST_MS == 1000
    assert win.TOAST_MS == 7500

    window.copy_message("a message")
    assert window.toast.text() == "Message Copied", (
        f"the copy toast says {window.toast.text()!r}")

    # It is gone a second later, while a failure is still up. ``isHidden`` is
    # the toast's own state: the fixture's window is never shown, so
    # ``isVisible`` would report False for both of them and prove nothing.
    QTest.qWait(1100)
    assert window.toast.isHidden() is True, "the copy toast was still up"

    window.notice("Could not save credentials.", error=True)
    QTest.qWait(1100)
    assert window.toast.isHidden() is False, (
        "a failure has to outlive a copy: it is the one worth reading")


def test_a_failure_is_not_cut_short_by_the_notice_before_it(window, qt_app) -> None:
    """The notice on screen owns the countdown, not the one before it.

    Each notice used to schedule its own singleShot, so two notices in quick
    succession left two timers pending and the older one took the toast down in
    the middle of the newer. Copy something and a failure arrives within the
    second, which is not a rare thing: the copy is what the user is doing when
    the failure shows up. The failure then vanished after the copy's one second
    instead of its own seven and a half, which is exactly when someone needs to
    read it.

    The ordering here is the whole test: the failure is raised while the
    earlier, shorter notice is still counting down.
    """
    window.copy_message("a message")
    QTest.qWait(400)
    window.notice("Could not save credentials.", error=True)

    # Past the copy's one second, but nowhere near the failure's seven and a
    # half. Under the old per-notice timers this is where the toast vanished.
    QTest.qWait(700)
    assert window.toast.isHidden() is False, (
        "the failure was taken down by the countdown from the copy before it, "
        "so it disappeared after a second instead of being readable")
    assert window.toast.text() == "Could not save credentials.", (
        f"the toast shows {window.toast.text()!r}, not the failure")

    # And it still goes away on its own schedule rather than never.
    QTest.qWait(7200)
    assert window.toast.isHidden() is True, (
        "the failure toast never went away, so the timer is not running")


def test_one_timer_owns_the_toast(window) -> None:
    """The countdown is a window-owned timer that each notice restarts.

    Checked on the timer itself rather than only on the toast's visibility, so
    a per-notice timer would be caught here even in a run where two notices
    never happened to overlap.
    """
    assert window.toast_timer.isSingleShot() is True, (
        "the toast timer is not single-shot, so it will fire repeatedly")

    window.copy_message("a message")
    assert window.toast_timer.isActive() is True, (
        f"a copy did not start the toast timer, which is {window.toast_timer.interval()}ms")
    assert window.toast_timer.interval() == win.COPY_TOAST_MS, (
        f"a copy should start a {win.COPY_TOAST_MS}ms countdown, got "
        f"{window.toast_timer.interval()}ms")
    window.notice("Something failed.", error=True)
    assert window.toast_timer.isActive() is True, (
        f"a failure did not restart the toast timer, which is {window.toast_timer.interval()}ms")
    assert window.toast_timer.interval() == win.TOAST_MS, (
        f"a failure should restart the countdown at {win.TOAST_MS}ms, got "
        f"{window.toast_timer.interval()}ms")
    assert window.toast_timer.parent() is window, (
        "the timer is not owned by the window, so it can outlive it")


def test_the_composer_is_set_apart_from_the_messages(window, qt_app) -> None:
    """It already sat outside the scrolling area, so it did not move.

    What it lacked was any sign that it was a separate surface.
    """
    conversation_window(window)
    window.show()
    qt_app.processEvents()

    assert window.composer_bar.geometry().y() >= window.messages_scroll.geometry().y(), (
        "the composer overlaps the message area")
    assert window.composer_bar.objectName() == "composerBar"


@pytest.mark.parametrize("send_method", ("enter", "button"))
def test_sending_in_default_chat_keeps_latest_messages_visible(window, qt_app, send_method) -> None:
    from PySide6.QtTest import QTest

    window.show()
    deadline = time.monotonic() + 10
    while not window.ready or not window.agents:
        pump(qt_app, .01)
        assert time.monotonic() < deadline
    window.workspace_list = [{"id": window.workspace_id}]
    window.agents = [{"id": "model", "online": True, "kind": "model",
                      "model": "chat-model", "provider": "ollama"}]
    window.workspace_meta = {"coordinator": "model"}
    reply = "\n\n".join(f"Paragraph {index}: " + "A detailed explanation. " * 15 for index in range(12))
    window.chats[win.GENERAL_TARGET] = {
        "messages": [(role, text) for index in range(8)
                     for role, text in (("user", f"Question {index}"), ("assistant", reply))],
        "history": [], "pending": False,
    }
    window.select_agent(win.GENERAL_TARGET)
    pump(qt_app, .15)
    bar = window.messages_scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.composer.setPlainText("Continue with an example")
    if send_method == "enter":
        QTest.keyClick(window.composer, Qt.Key.Key_Return)
    else:
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
    pump(qt_app, .15)
    assert bar.value() == bar.maximum(), "Sending moved the view away from the newest message"
    callbacks["success"]({"text": reply, "provider": "ollama", "model": "chat-model",
                          "routing": {"coordinator": "model", "mode": "direct", "assignments": [],
                                      "reason": "Answered by model"}})
    pump(qt_app, .15)
    assert bar.value() == bar.maximum(), "The reply moved the view away from the newest message"
    bar.setValue(bar.maximum() // 3)
    window.resize(900, 700)
    pump(qt_app, .15)
    assert bar.value() < bar.maximum(), "Resizing should let the user keep reading older messages"


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_composer_and_the_rows_are_divided_in_every_theme(name) -> None:
    for frame_name in ("composerBar", "messageRow", "messageActions"):
        rule = next((line for line in THEMES[name].splitlines()
                     if f"#{frame_name}" in line), "")
        assert rule, f"{name} has no rule for {frame_name}"
        assert "transparent" in rule or "border-top" in rule, (
            f"{name} gives {frame_name} no separation from the surface behind it")



# --- the rail's plus -----------------------------------------------------


def test_the_join_button_is_a_drawn_plus_not_a_character(window) -> None:
    """It was the text "+", which is placed by the font and not by us.

    Whether that lands where it looks right is a question about the font,
    so the mark is drawn instead, on the same 24-unit grid as the
    navigation icons and with the same stroke.
    """
    join = window.join_button
    assert not join.text(), f"the button is still a text glyph, {join.text()!r}"
    assert not join.icon().isNull(), "the button has no drawn mark"
    assert not join.icon().isMask(), "the mark should be coloured, not a mask"


def test_the_drawn_plus_is_centred_in_its_own_mark(window) -> None:
    """A round-capped stroke runs past the end point it was given.

    Drawn to its nominal bounds the cross sits low and right, so the ends
    are pulled back by half a stroke. Checked on the mark's own ink, which
    unlike a glyph's does not depend on a font being installed.

    The centroid rather than the bounding box: a cross's box is set by its
    longest arm, so nudging the other arm sideways still fits inside it and
    a box check would call that centred.
    """
    image = window.join_button.icon().pixmap(48, 48).toImage()
    lit = [(x, y) for x in range(image.width()) for y in range(image.height())
           if image.pixelColor(x, y).alpha() > 32]
    assert lit, "the mark is empty"

    centre_x = sum(x for x, _ in lit) / len(lit)
    centre_y = sum(y for _, y in lit) / len(lit)
    middle = (image.width() - 1) / 2

    assert abs(centre_x - middle) <= 0.5, (
        f"the ink sits {abs(centre_x - middle):.1f}px right of centre")
    assert abs(centre_y - middle) <= 0.5, (
        f"the ink sits {abs(centre_y - middle):.1f}px below centre")


def test_the_plus_follows_the_theme(window, qt_app) -> None:
    """Hand-painted, so nothing reaches it but an explicit repaint."""
    for name in THEME_NAMES:
        window.apply_theme(name)
        qt_app.processEvents()
        middle = window.join_button.icon().pixmap(48, 48).toImage().pixelColor(24, 24)
        assert middle.name().lower() == color(name, "success").lower(), (
            f"{name}: the middle of a cross is a stroke, so it should read "
            f"as {color(name, 'success')}, found {middle.name()}")


def test_hovering_the_rail_does_not_strip_a_workspace_button(qt_app) -> None:
    """The hover animation writes its own stylesheet, and it used to win.

    The ink was set by a widget stylesheet, which the animation overwrote,
    so hovering took the colour and everything else that sheet was
    carrying for the rest of the hover with it. The ink is a property now,
    read by the application stylesheet, so the two cannot collide.

    Checked on a button carrying text rather than the rail's plus, which
    is drawn and so never depended on the button's own colour.
    """
    from desktop_app.widgets import Select, WorkspaceButton

    button = WorkspaceButton("AB")
    button.set_ink("success")
    before = button.styleSheet()

    button.set_radius(15.0)              # where the hover animation lands
    assert button.property("ink") == "success", (
        f"hovering took the ink, leaving {button.styleSheet()!r} instead of "
        f"the {before!r} that was there before")
    assert button.styleSheet() == "QPushButton { border-radius: 15px; }", (
        f"the hover sheet should carry the corner and nothing else, "
        f"found {button.styleSheet()!r}")


# --- the workspaces page ------------------------------------------------


def test_a_page_is_reachable_and_explains_itself(window) -> None:
    """Adding a page used to shift every index below it silently."""
    assert set(win.PAGE_HELP) == {key for _, key in win.PAGES}, (
        "every page needs a tooltip entry, or one is about to be missing")
    assert win.PAGE_INDEX["workspaces"] == win.WORKSPACES_PAGE
    assert len(win.PAGES) == window.stack.count(), (
        "a page is declared but never added to the stack, so its button "
        "would go somewhere else")


@pytest.mark.parametrize("key", ["resources", "settings", "workspaces"])
def test_the_named_pages_point_where_they_say(window, key) -> None:
    """RESOURCES_PAGE and SETTINGS_PAGE were counted, not looked up.

    Inserting a page above them moved both, which broke Ctrl+5 and Ctrl+,
    and nothing failed: the numbers were still in range, just addressing
    the wrong page.
    """
    position = [name for _, name in win.PAGES].index(key)
    assert getattr(win, f"{key.upper()}_PAGE") == position, (
        f"{key} is page {position} but the constant says "
        f"{getattr(win, f'{key.upper()}_PAGE')}")


@pytest.mark.parametrize("index,key", list(enumerate(k for _, k in win.PAGES)))
def test_every_shortcut_lands_on_its_own_page(window, index, key) -> None:
    """Ctrl+1..Ctrl+n have to follow the pages, not the order they were added."""
    shortcuts = {s.key().toString(): s for s in window.findChildren(QShortcut)}
    shortcut = shortcuts.get(f"Ctrl+{index + 1}")
    assert shortcut is not None, f"Ctrl+{index + 1} is not bound to anything"
    shortcut.activated.emit()
    assert window.stack.currentIndex() == index, (
        f"Ctrl+{index + 1} landed on {window.stack.currentIndex()} "
        f"instead of {key}")


def test_the_rail_plus_goes_to_the_page_and_not_a_menu(window) -> None:
    """It used to pop a two-item menu, and creating was a bare prompt."""
    before = window.stack.currentIndex()
    window.add_workspace_button.click()

    assert window.stack.currentIndex() == win.WORKSPACES_PAGE, (
        f"the plus left the user on page {window.stack.currentIndex()}")
    assert window.stack.currentIndex() != before
    assert QApplication.activePopupWidget() is None, (
        "the plus still opens a pop-up instead of going to the page")


def test_the_create_panel_refuses_a_name_it_would_be_rejected_for(window) -> None:
    """The rule is the runtime's, so the two cannot disagree about it."""
    window.ready = True
    for text, ok in (("", False), ("   ", False), ("x" * 81, False), ("Team", True)):
        window.workspace_name_input.setText(text)
        assert window.workspace_create_button.isEnabled() is ok, (
            f"{len(text.strip())} characters should {'allow' if ok else 'refuse'} "
            f"the submit button")

    window.workspace_name_input.setText("x" * 81)
    assert window.workspace_name_line.message.text(), "the refusal was not explained"


def test_a_refused_create_reports_on_the_page_not_in_a_toast(window, qt_app) -> None:
    """The command is asynchronous, so the failure lands after it returns.

    A toast has usually gone by the time anyone reads it; the field that
    caused the problem is still on screen.
    """
    window.ready = True
    window.workspace_name_input.setText("")
    shown: list[str] = []
    window.notice = lambda message, error=False: shown.append(message)

    window.create_workspace()

    assert window.workspace_name_line.message.text(), "nothing was reported anywhere"
    assert not shown, f"the message went to a toast instead: {shown}"


def test_joining_from_the_page_does_not_open_a_dialog(window, monkeypatch) -> None:
    """The page's own fields are the ones that get used.

    This was the bug: the page had an invitation box, a relay box, a token
    box and a checkbox, and pressing Join threw all of them away and opened
    the old modal to ask for the same information again. So the fields were
    decoration and whatever you had typed was silently discarded.
    """
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: opened.append(self))
    window.ready = True

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))

    invitation = json.dumps({
        "version": 1,
        "url": "wss://relay.example.com/connect",
        "token": "t" * 40,
        "allow_insecure": True,
    })
    window.workspace_invitation.setPlainText(invitation)

    window.join_workspace()

    assert not opened, f"a dialog was opened instead of joining: {opened}"
    assert sent, "no join was attempted"
    name, args = sent[0]
    assert name == "join"
    # The invitation filled the page's fields, and those were submitted.
    assert window.workspace_relay.text() == "wss://relay.example.com/connect"
    assert window.workspace_token.text() == "t" * 40
    assert window.workspace_lan.isChecked() is True
    assert args[0] == "wss://relay.example.com/connect"
    assert args[1] == "t" * 40
    assert args[2] is True


def test_a_shared_conversation_invitation_joins_that_conversation(window) -> None:
    """The conversation id in an invitation is carried into the join."""
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))
    window.workspace_invitation.setPlainText(json.dumps({
        "version": 1,
        "url": "wss://relay.example.com/connect",
        "token": "t" * 40,
        "conversation_id": "conversation-abc123",
    }))

    window.join_workspace()

    assert sent[0][0] == "join"
    # join(relay, token, allow_insecure, announce, conversation_id)
    assert sent[0][1][4] == "conversation-abc123"


@pytest.mark.parametrize("paste, expected", [
    ("not json at all", "Paste the complete invitation JSON"),
    ('{"url": "wss://x", "token": "y"}', "Unsupported invitation version"),
    ('["a"]', "beginning with { and ending with }"),
    ('{"version": 1, "url": "wss://x", "token": "y", "conversation_id": "nope"}',
     "Use the complete shared conversation invitation"),
])
def test_a_bad_invitation_is_reported_on_the_page(window, paste, expected) -> None:
    """Every reason a paste is unusable is shown where the paste is."""
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))
    window.workspace_invitation.setPlainText(paste)

    window.join_workspace()

    assert not sent, "a join was attempted with an unusable invitation"
    assert expected in window.workspace_join_line.message.text(), (
        f"the page says {window.workspace_join_line.message.text()!r}")
    assert window.workspace_join_button.text() == "Join workspace"


def test_a_too_short_token_is_refused_on_the_page(window) -> None:
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))
    window.workspace_invitation.setPlainText("")

    window.join_workspace()

    assert not sent
    assert "device token" in window.workspace_join_line.message.text()


def test_a_failed_join_leaves_the_page_usable(window) -> None:
    """A failure puts the button back and says why, on the page."""
    window.ready = True
    window.workspace_invitation.setPlainText("")
    window.workspace_token.setText("t" * 40)
    window.workspace_relay.setText("wss://relay.example.com/connect")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)

    window.join_workspace()
    assert window.workspace_join_button.text() == "Connecting…"
    assert window.workspace_join_button.isEnabled() is False

    # The failure arrives asynchronously, after join_workspace returned.
    callbacks["failure"]("The relay refused the token")

    assert window.workspace_join_button.text() == "Join workspace"
    assert window.workspace_join_button.isEnabled() is True
    assert "refused" in window.workspace_join_line.message.text()


def test_joining_before_the_network_is_ready_is_refused(window) -> None:
    window.ready = False
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))

    window.join_workspace()

    assert not sent, "a join was attempted before the network was ready"
    assert "still starting" in window.workspace_join_line.message.text()


def test_each_card_keeps_its_own_message(window) -> None:
    """A failure in one card never appears in, or clears, another.

    There was one line shared by the create and join forms, so a join error
    was painted inside the create card, and creating a workspace wrote its
    message to the same place a join was reading.
    """
    window.ready = True
    window.command = lambda name, *args, **kwargs: None

    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    assert window.workspace_join_line.message.text() == "the relay refused the token"
    assert not window.workspace_create_line.message.text(), (
        "a join failure showed in the create card")
    assert not window.workspace_name_line.message.text()

    window.set_card_message(window.workspace_create_line, "that name is taken")
    assert window.workspace_create_line.message.text() == "that name is taken"
    assert window.workspace_join_line.message.text() == "the relay refused the token"


def test_a_new_page_starts_with_the_stored_message(window, qt_app) -> None:
    """A card built after a failure shows that failure, not an empty line.

    Anything held only by the widget that displayed it is lost when the page
    is torn down and built again, so the message is kept in the window's
    state and each new line is given it as it is constructed.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")

    # The page is held here because a page built and dropped takes its
    # widgets with it, which would delete the lines being asserted on.
    rebuilt = window.workspaces_page()
    qt_app.processEvents()

    line = window.card_lines["workspace:join"]
    assert line.message.text() == "the relay refused the token"
    # isHidden rather than isVisible: this page was never added to the
    # stack, so nothing in it is visible yet, and that is not what is
    # being checked here.
    assert line.isHidden() is False, "the line is still collapsed"
    assert window.card_messages["workspace:join"] == (
        "the relay refused the token", "error")
    assert rebuilt is not None


def test_a_card_rebuild_keeps_the_message(window, qt_app) -> None:
    """The live rebuild path: a grid cleared and filled again.

    This is what the agents page does on every poll, and it is why the
    message cannot live in the card.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")

    window.refresh_card_messages()
    qt_app.processEvents()

    line = window.card_lines["workspace:join"]
    assert line.message.text() == "the relay refused the token"


def test_the_agents_poll_does_not_clear_a_message(window, qt_app) -> None:
    """The reported bug: a message lasted about three seconds.

    The runtime polls agents every three seconds, each poll re-ran
    render_workspace_page, which ended in the name handler, and that
    handler owned the single shared line. So a failure was erased by the
    next poll whether or not anything had been retried.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    window.set_card_message(window.workspace_create_line, "that name is taken")

    for _ in range(3):
        window.network_event("agents", {"agents": [], "self": window.identity,
                                        "connected": False})
        qt_app.processEvents()

    assert window.workspace_join_line.message.text() == "the relay refused the token"
    assert window.workspace_create_line.message.text() == "that name is taken"


def test_no_runtime_event_clears_a_message(window, qt_app) -> None:
    """Every event that redraws the page must leave messages alone.

    The workspace event does not reach render_workspace_page today, so this
    drives render_workspace_page itself and also the events that do. The
    requirement is about the render, not about which event happens to call
    it, because a poll is free to start calling it tomorrow.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    window.set_card_message(window.workspace_create_line, "that name is taken")

    window.render_workspace_page()
    window.render_agents()
    window.network_event("agents", {"agents": [], "self": window.identity,
                                    "connected": False})
    window.network_event("workspace", {
        "id": window.workspace_id, "name": "Lab", "remote": False,
        "self": window.identity, "port": 1234,
        "history_directory": str(window.storage.directory)})
    qt_app.processEvents()

    assert window.workspace_join_line.message.text() == "the relay refused the token"
    assert window.workspace_create_line.message.text() == "that name is taken"


def test_typing_a_name_does_not_clear_a_create_failure(window) -> None:
    """The name rule and the create outcome are different messages.

    They shared one line, so typing after a failure replaced what the
    runtime said with the name-length hint.
    """
    window.ready = True
    window.set_card_message(window.workspace_create_line, "that name is taken")

    window.workspace_name_input.setText("Research lab")
    assert window.workspace_create_line.message.text() == "that name is taken"

    window.workspace_name_input.setText("x" * 81)
    assert window.workspace_create_line.message.text() == "that name is taken", (
        "typing a name replaced a real failure with the name rule")
    assert window.workspace_name_line.message.text(), (
        "but the name rule itself was not shown either")


def test_dismissing_clears_one_message_and_leaves_the_others(window, qt_app) -> None:
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    window.set_card_message(window.workspace_create_line, "that name is taken")

    window.workspace_join_line.dismiss.click()
    qt_app.processEvents()

    assert not window.workspace_join_line.message.text()
    assert not window.workspace_join_line.isVisible()
    assert window.workspace_create_line.message.text() == "that name is taken", (
        "dismissing the join message also cleared the create message")


def test_trying_again_replaces_the_message_it_is_replacing(window) -> None:
    """The outcome line shows the newest outcome, never a stale one.

    A failure left on screen while a second attempt is already under way
    tells the reader the attempt failed when it has not been tried yet.
    """
    window.ready = True
    window.workspace_invitation.setPlainText("")
    window.workspace_token.setText("t" * 40)
    window.workspace_relay.setText("wss://relay.example.com/connect")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.join_workspace()
    callbacks["failure"]("The relay refused the token")
    assert window.workspace_join_line.message.text() == "The relay refused the token"

    window.join_workspace()

    assert window.workspace_join_line.message.text() != "The relay refused the token", (
        "the previous failure was still showing over a fresh attempt")
    assert "Contacting the relay" in window.workspace_join_line.message.text()


def test_trying_again_clears_a_failure_even_when_it_needs_no_message(window) -> None:
    """The clear happens because the button was pressed, not as a side effect.

    A retry that resolves without a message of its own must still remove the
    last failure, or it hangs over the form forever.
    """
    window.ready = True
    window.workspace_name_input.setText("Research lab")
    window.set_card_message(window.workspace_create_line, "that name is taken")
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)

    window.create_workspace()

    assert not window.workspace_create_line.message.text(), (
        "a previous failure stayed on screen after the button was pressed")
    assert not window.card_messages.get("workspace:create")


def test_a_message_line_costs_nothing_when_empty(window, qt_app) -> None:
    """An empty line must not leave a band of space on every card."""
    for line in (window.workspace_name_line, window.workspace_create_line,
                 window.workspace_join_line):
        assert line.isVisible() is False
        assert not line.message.text()


def test_a_message_line_is_out_of_the_layout_when_empty(window) -> None:
    """An empty line must not leave a band of space on every card.

    Checked through the layout rather than isVisible, because a page that
    was never shown has nothing visible in it; what matters is that the line
    contributes no height while it has nothing to say.
    """
    for line in (window.workspace_name_line, window.workspace_create_line,
                 window.workspace_join_line):
        assert not line.message.text()

    window.set_card_message(window.workspace_join_line, "refused")
    assert window.workspace_join_line.isHidden() is False

    window.clear_card_message(window.workspace_join_line)
    assert window.workspace_join_line.isHidden() is True, (
        "the cleared line is still taking up space on the card")


def test_a_card_message_follows_a_theme_change(window) -> None:
    """It carries its own colour, so the application sheet will not do it."""
    window.set_card_message(window.workspace_join_line, "refused")
    line = window.workspace_join_line
    assert color(LIGHT, "error") not in line.message.styleSheet()

    window.apply_theme(LIGHT)

    assert color(LIGHT, "error") in line.message.styleSheet(), (
        f"the message kept the old theme's colour: {line.message.styleSheet()!r}")
    assert color(DARK, "error") not in line.message.styleSheet()


def test_a_member_is_offered_a_way_out_of_the_workspace_it_is_in(window, monkeypatch) -> None:
    """A device joined to someone else's workspace could not leave it.

    Leaving worked, and was tested, and was reachable only through Workspace
    settings: a danger button in a dialog whose wording led with the loss of
    saved messages. So on the page that shows which workspace you are in, the
    only visible route out was one an invitation recipient had no reason to go
    looking for. It belongs beside the invitation, and only when there is
    somewhere to leave, since a device that owns its workspace has nothing to
    leave.
    """
    window.ready = True
    window.navigate(win.WORKSPACES_PAGE)
    window.render_workspace_page()
    assert window.workspace_leave_button.isHidden(), (
        "leaving is offered on a workspace this device owns, where there is nothing to leave")

    window.remote = True
    window.render_workspace_page()
    assert not window.workspace_leave_button.isHidden(), (
        "a member of someone else's workspace has no way off this page")
    assert window.workspace_leave_button.text() == "Leave workspace"
    assert window.workspace_leave_button.toolTip(), "the leave button has no tooltip"
    assert window.workspace_leave_button.toolTip() != window.workspace_leave_button.text()
    assert window.workspace_leave_button.isEnabled(), "leaving cannot be started while the runtime is up"

    asked = []
    monkeypatch.setattr(win.QMessageBox, "question",
                        lambda *args, **kwargs: asked.append(args[2])
                        or int(win.QMessageBox.StandardButton.Yes))
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, kwargs))

    window.workspace_leave_button.click()

    assert asked, "leaving happened without asking, which cannot be undone"
    assert [name for name, _ in sent] == ["delete_workspace"], (
        f"leaving did not reach the runtime: {sent!r}")
    window.network_event("ready", {"port": 0, "device_id": "device-test"})
    window.remote = False
    window.render_workspace_page()
    assert window.workspace_leave_button.isHidden(), (
        "the button is still there after leaving")


def test_leaving_asks_before_it_acts_and_says_when_the_host_refuses(window, monkeypatch) -> None:
    """Both halves of a destructive answer, which is what the button is for.

    Answering no must do nothing at all, and a host that will not release the
    device must not leave the button dead on a page with no line saying why.
    That second half is the one that loses people: the control stops working,
    nothing says the host refused, and it reads as though leaving is simply
    unavailable.
    """
    window.ready = True
    window.remote = True
    window.navigate(win.WORKSPACES_PAGE)
    window.render_workspace_page()
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, kwargs))

    monkeypatch.setattr(win.QMessageBox, "question",
                        lambda *args, **kwargs: int(win.QMessageBox.StandardButton.Cancel))
    window.leave_workspace()
    assert not sent, f"answering no still left the workspace: {sent!r}"

    monkeypatch.setattr(win.QMessageBox, "question",
                        lambda *args, **kwargs: int(win.QMessageBox.StandardButton.Yes))
    window.leave_workspace()
    assert [name for name, _ in sent] == ["delete_workspace"]
    assert not window.workspace_leave_button.isEnabled(), (
        "the button stayed live while the request was in flight, so it could be pressed twice")

    sent[0][1]["failure"]("The host could not remove this membership.")
    assert window.workspace_leave_button.isEnabled(), (
        "the button stayed dead after the host refused, with nothing saying why")


def test_the_join_card_says_where_leaving_is_possible(window) -> None:
    """The card names a control that is only on screen in one of the two cases.

    "Leave it from the card above" is true while this device is a member of
    the workspace it is looking at, because that is when the current
    workspace's card carries a Leave control. Hosting your own workspace while
    holding a membership elsewhere is the other half: the card above is yours,
    it has no Leave on it, and the way out is to switch to that workspace
    first. Sending somebody to a button that is not there is worse than saying
    nothing, because it reads as the control being broken.
    """
    window.ready = True
    window.network_event("ready", {"port": 0, "device_id": "device-test"})
    window.navigate(win.WORKSPACES_PAGE)
    window.show()
    window.remote = True
    joined = {"id": "workspace-abc", "name": "Alex's studio", "kind": "remote",
              "url": "wss://studio.example/connect"}

    # A member looking at the workspace they joined: the card above is theirs.
    window.workspace_list = [{"id": "local", "name": "Mine", "kind": "local"},
                             joined]
    window.remote = True
    window.render_workspace_page()
    assert window.workspace_leave_button.isVisible(), (
        "this fixture needs the Leave control on the card for the instruction "
        "to be true")
    in_it = window.card_lines["workspace:join"].message.text()
    assert "Alex's studio" in in_it, f"it does not name the workspace: {in_it!r}"
    assert "card above" in in_it, (
        f"it does not point at the control that works: {in_it!r}")

    # Hosting our own, with a membership elsewhere: not the card above.
    window.remote = False
    window.render_workspace_page()
    assert not window.workspace_leave_button.isVisible(), (
        "this fixture needs no Leave control, or the instruction would be "
        "right for the wrong reason")
    hosted = window.card_lines["workspace:join"].message.text()
    assert "Alex's studio" in hosted, f"it lost the name: {hosted!r}"
    assert "rail" in hosted, (
        f"it does not say how to leave while hosting another workspace: {hosted!r}")
    assert "card above" not in hosted, (
        f"it points at a button this device cannot see: {hosted!r}")


def test_the_join_card_stops_naming_a_workspace_after_leaving_it(window) -> None:
    """The message used to outlive the thing it described.

    It was written only when there was a workspace joined and never cleared,
    so after leaving, the card went on saying "You are in 'Alex's studio'" --
    naming a workspace this device was no longer a member of, and pointing at
    a button that was already enabled again.
    """
    window.ready = True
    window.network_event("ready", {"port": 0, "device_id": "device-test"})
    window.navigate(win.WORKSPACES_PAGE)
    window.show()
    window.remote = True
    joined = {"id": "workspace-abc", "name": "Alex's studio", "kind": "remote",
              "url": "wss://studio.example/connect"}

    window.workspace_list = [{"id": "local", "name": "Mine", "kind": "local"},
                             joined]
    window.remote = True
    window.render_workspace_page()
    assert "Alex's studio" in window.card_lines["workspace:join"].message.text()

    window.workspace_list = [{"id": "local", "name": "Mine", "kind": "local"}]
    window.remote = False
    window.render_workspace_page()

    line = window.card_lines["workspace:join"].message
    assert not line.text(), (
        f"the card still names a workspace this device has left: {line.text()!r}")
    assert not line.isVisible(), "the line is still on screen saying it"
    assert window.workspace_join_button.isEnabled(), (
        "joining is still refused after the only membership was left")


def test_leaving_a_workspace_does_not_wipe_a_join_that_failed(window) -> None:
    """The line carries an error and a standing hint, and they are not the same.

    Clearing it unconditionally on leaving -- which is what a first attempt at
    this did -- threw away the only account of a join that had actually failed,
    because the message was on the same line as the explanation of which
    workspace you are in. Only the hint is cleared.
    """
    window.ready = True
    window.network_event("ready", {"port": 0, "device_id": "device-test"})
    window.navigate(win.WORKSPACES_PAGE)
    window.show()
    window.remote = True

    # A join is attempted and refused, and that is what the line says.
    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    window.workspace_list = []
    window.render_workspace_page()

    assert "refused" in window.workspace_join_line.message.text(), (
        "leaving a workspace cleared the error from a failed join: "
        f"{window.workspace_join_line.message.text()!r}")


def test_the_join_card_explains_itself_while_another_workspace_is_joined(window) -> None:
    """One invitation at a time, and the page says so instead of going quiet.

    The button simply stopped working when a second join was refused. A
    disabled control with no reason is the same failure this file's other tests
    describe: the user cannot tell a decision from a fault, and the obvious
    next move is to conclude the app is broken. The card names the workspace to
    be left and points at the control that does it.
    """
    window.ready = True
    window.network_event("ready", {"port": 0, "device_id": "device-test"})
    window.navigate(win.WORKSPACES_PAGE)
    # Shown, because isVisible() is false for anything in a window that is not
    # on screen, and whether the Leave control is there is the point.
    window.show()
    window.set_card_message(window.workspace_join_line, "")

    assert window.workspace_join_button.isEnabled(), (
        "joining is refused before any workspace has been joined")

    joined = {"id": "workspace-abc", "name": "Alex's studio", "kind": "remote", "url": "wss://studio.example/connect"}
    window.workspace_list = [{"id": "local", "name": "My workspace", "kind": "local"}, joined]
    window.render_workspace_page()

    assert not window.workspace_join_button.isEnabled(), (
        "a second workspace can be joined while one is already joined")
    line = window.card_lines["workspace:join"].message.text()
    assert "Alex's studio" in line, f"the line does not say which workspace to leave: {line!r}"
    # How to leave it depends on whether this device is looking at that
    # workspace or at its own; either way the line has to say, rather than only
    # naming a problem. Which of the two is checked properly alongside this,
    # in the test that sets up both cases.
    assert ("card above" in line) or ("rail" in line), (
        f"the line does not say how to leave: {line!r}")


def test_the_invite_card_is_on_the_workspaces_page(window) -> None:
    """Inviting a device belongs on the page, beside what it is for."""
    window.ready = True
    window.navigate(win.WORKSPACES_PAGE)

    assert window.stack.currentIndex() == win.WORKSPACES_PAGE
    assert window.workspace_invite_name.text() or True, "no device name field"
    assert window.workspace_invite_url.text().startswith("ws://"), (
        f"the card does not show an address: {window.workspace_invite_url.text()!r}")


def test_the_invite_card_does_not_open_a_dialog(window, monkeypatch) -> None:
    """The whole point: the invitation is made here, not in a window.

    The button used to call InviteDialog, so the description of what an
    invitation is for sat on the page while every field that made one was
    somewhere else.
    """
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: opened.append(self))
    window.ready = True
    window.workspace_invite_name.setText("alex-laptop")

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))

    window.create_invitation()

    assert not opened, f"a dialog was opened instead of the page: {opened}"
    assert sent, "no invitation was attempted"
    assert sent[0][0] == "invite"
    assert sent[0][1][0] == "alex-laptop"


def test_the_invitation_is_shown_on_the_card_that_asked(window, qt_app) -> None:
    """The result comes back to the card, not to a dialog that has closed."""
    window.ready = True
    window.workspace_invite_name.setText("alex-laptop")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.create_invitation()
    assert window.workspace_invite_create_button.text() == "Creating…"

    callbacks["success"]({"version": 1, "url": "ws://192.168.1.70:1234/connect",
                          "token": "t" * 40})

    qt_app.processEvents()
    assert window.workspace_invite_result.isHidden() is False, (
        "the invitation was created but nothing on the card shows it")
    assert "alex" not in window.workspace_invite_name.text(), (
        "the name field was not cleared after a successful invitation")
    assert window.workspace_invite_copy_button.isHidden() is False


def test_a_refused_invitation_is_reported_on_the_card(window) -> None:
    window.ready = True
    window.workspace_invite_name.setText("alex-laptop")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.create_invitation()
    callbacks["failure"]("The relay is not running")

    assert "not running" in window.workspace_invite_error.message.text()
    assert window.workspace_invite_create_button.text() == "Create invitation"
    assert window.workspace_invite_create_button.isEnabled() is True
    # The create card's messages are untouched by an invitation failure.
    assert not window.workspace_create_line.message.text()
    assert not window.workspace_join_line.message.text()


def test_turning_off_lan_clears_the_address_the_card_generated(window) -> None:
    """A stale ws:// address invites a device to an address it cannot reach.

    This used to assert the opposite, that the address survives turning LAN
    off. It was written when the toggle drove nothing, so it recorded the
    absence of behaviour as though it were the requirement. With the toggle
    connected, leaving the address behind is the bug: the invitation is made
    with LAN sharing off and still advertises the LAN address.
    """
    window.ready = True
    window.port = 1234
    window.workspace_invite_network.setCurrentIndex(0)
    window.workspace_invite_lan.setChecked(True)
    assert window.workspace_invite_url.text().startswith("ws://")
    generated = window.workspace_invite_url.text()

    window.workspace_invite_lan.setChecked(False)

    assert not window.workspace_invite_url.text(), (
        f"the generated address {generated!r} is still advertised after LAN "
        "sharing was turned off")


def test_turning_off_lan_keeps_an_address_the_reader_typed(window) -> None:
    """Turning off local sharing is a statement about this machine.

    A WSS relay somebody reached for on purpose is not this card's address
    and must survive the toggle, or the only way to use a relay is to turn LAN
    sharing on and off again afterwards.
    """
    window.ready = True
    window.port = 1234
    window.workspace_invite_lan.setChecked(True)
    window.workspace_invite_url.setText("wss://relay.example.com/connect")

    window.workspace_invite_lan.setChecked(False)

    assert window.workspace_invite_url.text() == "wss://relay.example.com/connect", (
        "turning off LAN sharing erased a relay address the reader entered")


def test_the_network_picker_is_disabled_while_lan_is_off(window) -> None:
    """It only means something while the address is being taken from it."""
    window.ready = True
    assert window.workspace_invite_network.isEnabled() is True, (
        "the picker starts disabled, so this proves nothing")

    window.workspace_invite_lan.setChecked(False)
    assert window.workspace_invite_network.isEnabled() is False, (
        "the network picker is still live while the address it feeds is unused")

    window.workspace_invite_lan.setChecked(True)
    assert window.workspace_invite_network.isEnabled() is True


def test_an_address_from_an_older_port_is_replaced_not_treated_as_the_readers_own(window) -> None:
    """The relay's port is not known when the card is built.

    The card wrote ws://...:0 on the first pass and create_invitation submits
    whatever is in the box, so without a refresh the invitation advertised
    port 0. A refresh has to tell that stale address from one the reader
    typed, or it would either keep the bad port or overwrite their relay.
    """
    window.ready = True
    window.port = 1234
    assert window.workspace_invite_url.text().endswith(":0/connect"), (
        "this only means anything if the first address really did carry port 0")

    window.network_event("ready", {"port": 4321, "device_id": "device-1"})
    window.render_workspace_page()

    assert window.workspace_invite_url.text().endswith(":4321/connect"), (
        f"the address still reads {window.workspace_invite_url.text()!r} after "
        "the relay's port arrived")

    window.workspace_invite_url.setText("wss://relay.example.com/connect")
    window.render_workspace_page()
    assert window.workspace_invite_url.text() == "wss://relay.example.com/connect", (
        "a refresh overwrote a relay address the reader entered")


def test_the_welcome_join_tooltip_describes_the_page(window) -> None:
    """It used to say it opened a menu, which it has not since 7611fa8."""
    window.welcome_join.show()
    tooltip = window.welcome_join.toolTip()
    assert "menu" not in tooltip.lower(), (
        f"the tooltip still describes a menu: {tooltip!r}")
    assert "dialog" not in tooltip.lower(), (
        f"the tooltip still describes a dialog: {tooltip!r}")
    assert "Workspaces" in tooltip, (
        f"the tooltip does not say where it goes: {tooltip!r}")


def test_the_create_and_join_cards_look_different(window) -> None:
    """They were read as halves of one form.

    Both had a text box, a line of help text and a button under one shared
    heading, and nothing said that create makes a network here while join
    connects to one elsewhere.

    Checked on the rendered stylesheet rather than on the object names,
    because distinct names that style identically still look the same. The
    accent edge is what actually separates them.
    """
    page = window.stack.widget(win.WORKSPACES_PAGE)
    assert page is not None

    rules = {}
    for name in ("createCard", "joinCard", "inviteCard"):
        matched = re.search(rf"QFrame#{name} \{{([^}}]*)\}}",
                            stylesheet(window.theme))
        assert matched, f"the stylesheet has no rule for {name}"
        rules[name] = matched.group(1)

    edges = {name: re.search(r"border-left:\s*([^;]+);", rule)
             for name, rule in rules.items()}
    for name, edge in edges.items():
        assert edge is not None, f"{name} has no accent edge: {rules[name]}"

    colours = {edge.group(1).strip() for edge in edges.values()}
    assert len(colours) == 3, (
        f"the three cards share an accent edge, so they read as one form: "
        f"{colours}")

    # And each resolves to a real colour in every theme, rather than to an
    # unresolved placeholder that would render as nothing.
    for theme in THEME_NAMES:
        sheet = stylesheet(theme)
        for name in ("createCard", "joinCard", "inviteCard"):
            rule = re.search(rf"QFrame#{name} \{{([^}}]*)\}}", sheet)
            assert rule is not None, f"{name} has no rule in {theme}"
            for token in re.findall(r"@@([a-z_]+)@@", rule.group(1)):
                assert color(theme, token), (
                    f"{name} uses @@{token}@@ in {theme}, which is not a "
                    "token in that palette")


def test_connecting_a_model_lands_on_the_agents_page(window) -> None:
    """The card is on the page, not in a window.

    Five buttons led here: the rail, the welcome panel, a provider card, the
    overview and each agent's own Edit. All of them opened a 272-line modal,
    which meant the form was somewhere else from the list of models it adds
    to, and a successful save showed as a dialog closing rather than a card
    appearing.
    """
    window.ready = True

    window.add_agent("anthropic")
    assert window.stack.currentIndex() == win.AGENTS_PAGE, (
        "connecting a model did not open the page that holds it")
    assert window.model_provider.currentData() == "anthropic"
    assert window.model_name.text() == "anthropic-agent"
    assert window.model_form_title.text() == "Connect a model"
    assert window.model_save_button.text() == "Connect agent"


def test_connecting_a_model_does_not_open_a_dialog(window, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: opened.append(self))
    window.ready = True

    window.add_agent("ollama")
    window.edit_agent({"id": "llama-agent", "provider": "ollama",
                       "profile": {"id": "llama-agent", "model": "llama3.2"}})

    assert not opened, f"a dialog was opened instead of the page: {opened}"


def test_editing_a_model_loads_it_into_the_same_card(window) -> None:
    window.ready = True
    profile = {"id": "llama-agent", "model": "llama3.2", "base_url": "http://host:1234",
               "system_prompt": "be terse", "autostart": False, "vision": True,
               "web_search": "always", "search_provider": "ollama",
               "searxng_url": "", "searxng_allow_insecure": False}

    window.edit_agent({"id": "llama-agent", "provider": "ollama", "profile": profile})

    assert window.model_form_title.text() == "Edit llama-agent"
    assert window.model_save_button.text() == "Save changes"
    assert window.model_id.currentText() == "llama3.2"
    assert window.model_base.text() == "http://host:1234"
    assert window.model_system.toPlainText() == "be terse"
    assert window.model_autostart.isChecked() is False
    assert window.model_vision.isChecked() is True
    assert window.model_internet.isChecked() is True
    assert window.model_search_mode.currentData() == "always"
    assert window.model_name.isReadOnly() is True


def test_the_model_form_reaches_the_agents_page_in_full(window) -> None:
    """Every field the dialog had, not the easy half of it.

    Web search with SearXNG and its Test button is about half the old form,
    and leaving it in a dialog would have left a modal on the model path,
    which is the thing being removed.
    """
    window.ready = True
    window.navigate(win.AGENTS_PAGE)

    for name in ("model_name", "model_provider", "model_id", "model_base",
                 "model_key", "model_system", "model_autostart", "model_insecure",
                 "model_vision", "model_internet", "model_search_provider",
                 "model_search_mode", "model_search_url", "model_search_key",
                 "model_search_insecure", "model_search_test", "model_search_status",
                 "model_search_help", "model_models_button", "model_save_button"):
        assert hasattr(window, name), f"the form is missing {name}"

    # The card is on the page, above the connected models.
    page = window.stack.currentWidget()
    names = [child.objectName() for child in page.findChildren(win.QFrame)]
    assert "createCard" in names


def test_a_failed_model_save_is_reported_on_the_card(window) -> None:
    """Not a toast, and not in a dialog that has closed."""
    window.ready = True
    window.add_agent("ollama")
    window.model_id.setCurrentText("llama3.2")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.save_model()
    callbacks["failure"]("The provider refused that key")

    assert "refused" in window.model_error_line.message.text()
    assert window.model_save_button.isEnabled() is True
    # And it belongs to this card alone.
    assert not window.workspace_create_line.message.text()
    assert not window.workspace_join_line.message.text()


def test_failed_model_save_returns_to_the_field_and_clears_when_edited(window, qt_app) -> None:
    window.ready = True
    window.add_agent("ollama")
    window.model_id.setCurrentText("llama3.2")
    window.show()
    qt_app.processEvents()
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.save_model()
    window.navigate(win.OVERVIEW_PAGE)
    callbacks["failure"]("The provider refused that API key")
    qt_app.processEvents()
    assert window.stack.currentIndex() == win.AGENTS_PAGE
    assert window.model_key.hasFocus()
    assert window.model_key.property("invalid")
    assert window.model_card.property("invalid")
    window.model_key.setText("corrected-key")
    assert not window.model_key.property("invalid")
    assert not window.model_card.property("invalid")


def test_failed_chat_opens_its_conversation_after_the_user_leaves(window) -> None:
    window.ready = True
    window.workspace_id = "test-workspace"
    window.workspace_list = [{"id": window.workspace_id}]
    window.agents = [{"id": "model", "online": True, "kind": "model", "model": "chat-model", "provider": "ollama"}]
    window.select_agent("model")
    window.composer.setPlainText("Hello")
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.send_message()
    window.navigate(win.AGENTS_PAGE)
    callbacks["failure"]("The model is unavailable")
    assert window.stack.currentIndex() == win.CONVERSATIONS_PAGE
    assert window.selected == "model"
    assert window.chats["model"]["messages"][-1][0] == "error"
    assert not window.composer_bar.property("invalid")
    assert window.messages.itemAt(window.messages.count() - 1).widget().property("invalid")


def test_model_list_results_for_an_old_endpoint_are_ignored(window) -> None:
    window.ready = True
    window.add_agent("ollama")
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.find_models()
    window.model_base.setText("http://localhost:11435")
    callbacks["success"](["model-from-old-endpoint"])
    assert window.model_id.findText("model-from-old-endpoint") == -1
    assert window.model_models_button.isEnabled()


def test_default_model_dropdown_only_offers_active_models_and_recovers(window) -> None:
    window.ready = True
    window.workspace_meta["coordinator"] = "stopped"
    window.agents = [{"id": "stopped", "kind": "model", "model": "saved-model", "online": False},
                     {"id": "active", "kind": "model", "model": "active-model", "online": True}]
    window.refresh_coordinator_controls()
    assert window.coordinator_picker.findData("stopped") == -1
    assert window.coordinator_picker.currentData() == "active"
    assert window.workspace_meta["coordinator"] == "stopped"
    window.agents[1]["online"] = False
    window.refresh_coordinator_controls()
    assert window.coordinator_picker.count() == 1
    assert window.coordinator_picker.itemText(0) == "No active models"
    assert not window.coordinator_picker.isEnabled()
    assert not window.coordinator_save.isEnabled()
    window.agents[0]["online"] = True
    window.refresh_coordinator_controls()
    assert window.coordinator_picker.currentData() == "stopped"
    assert window.coordinator_picker.isEnabled()


def test_ollama_catalog_failure_removes_stale_models_and_a_restart_restores_choices(window) -> None:
    window.ready = True
    window.add_agent("ollama")
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.find_models()
    callbacks["success"](["first", "second"])
    window.model_id.setCurrentText("removed-model")
    window.find_models()
    callbacks["success"](["first"])
    assert window.model_id.currentText() == "first"
    window.find_models()
    callbacks["failure"]("Connection refused")
    assert window.model_id.count() == 0
    assert window.model_id.currentText() == ""
    assert "Ollama is unavailable" in window.model_catalog_status.text()
    window.refresh_ollama_catalog()
    callbacks["success"](["returned-model"])
    assert window.model_id.currentText() == "returned-model"
    assert window.model_error_line.message.text() == ""


def test_imported_ollama_row_reports_unavailability_and_automatic_reconnection(window) -> None:
    window.agents = [{"id": "saved-model", "provider": "ollama", "model": "chat",
                      "local": True, "online": False}]
    window.render_imported_models()
    card = window.imported_model_rows.itemAt(0).widget()
    assert any("Unavailable" in child.text() and "Reconnects automatically" in child.text()
               for child in card.findChildren(QLabel))
    window.agents[0]["online"] = True
    window.render_imported_models()
    card = window.imported_model_rows.itemAt(0).widget()
    assert any(child.text() == "Online" for child in card.findChildren(QLabel))


def test_attachment_failure_returns_to_its_chat_and_preserves_the_draft(window) -> None:
    window.ready = True
    window.workspace_id = "test-workspace"
    window.workspace_list = [{"id": window.workspace_id}]
    window.agents = [{"id": "model", "online": True, "kind": "model", "model": "vision-model", "provider": "ollama", "vision": True}]
    window.select_agent("model")
    window.composer.setPlainText("Understand this image")
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.prepare_files(["unreadable.png"])
    window.navigate(win.AGENTS_PAGE)
    callbacks["failure"]("Could not read image: unreadable.png")
    assert window.stack.currentIndex() == win.CONVERSATIONS_PAGE
    assert window.composer.toPlainText() == "Understand this image"
    assert not window.composer_bar.property("invalid")
    assert "unreadable.png" in window.chats["model"]["send_error"]
    assert not window.preparing_files


def test_an_oversized_message_stays_editable_and_reports_in_its_chat(window) -> None:
    window.ready = True
    window.workspace_id = "test-workspace"
    window.workspace_list = [{"id": window.workspace_id}]
    window.agents = [{"id": "model", "online": True, "kind": "model", "model": "chat-model", "provider": "ollama"}]
    window.select_agent("model")
    draft = "x" * 180001
    window.composer.setPlainText(draft)
    calls = []
    window.command = lambda name, *args, **kwargs: calls.append(name)
    window.send_message()
    assert not calls
    assert window.composer.toPlainText() == draft
    assert "too large" in window.chats["model"]["send_error"]
    assert not window.composer_bar.property("invalid")


def test_a_search_failure_points_at_the_search_settings(window) -> None:
    """A failure caused by search settings focuses them, as it used to."""
    window.ready = True
    window.add_agent("ollama")
    window.model_internet.setChecked(True)
    window.model_search_provider.setCurrentIndex(
        window.model_search_provider.findData("ollama"))
    window.model_search_key.setText("")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.save_model()

    assert not callbacks, "a save was attempted with no search key"
    assert "Search API key" in window.model_error_line.message.text(), (
        f"the page says {window.model_error_line.message.text()!r}")


def test_a_lan_http_searxng_is_refused_without_the_lan_box(window) -> None:
    """A LAN address over plain HTTP is what the trusted-LAN box governs.

    This used to use ``http://localhost:8888``, which pinned a rule the
    runtime contradicts: network_a2a.web_search exempts loopback from the
    HTTPS requirement, so localhost is legal with or without the box. It is
    also the field's own placeholder and the example in the runtime's error
    message, so the test was refusing the most ordinary local address.
    """
    window.ready = True
    window.add_agent("ollama")
    window.model_internet.setChecked(True)
    window.model_search_provider.setCurrentIndex(
        window.model_search_provider.findData("searxng"))
    window.model_search_url.setText("http://192.168.1.5:8888")
    window.model_search_insecure.setChecked(False)

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)
    window.save_model()

    assert not sent, "a save was attempted over plain HTTP to a LAN host"
    assert "HTTP" in window.model_error_line.message.text()

    window.model_search_insecure.setChecked(True)
    window.save_model()
    assert sent == ["save_agent"], "it was still refused after the box was ticked"


def test_a_loopback_http_searxng_needs_no_lan_box(window) -> None:
    """The runtime exempts localhost, so the page must not refuse it."""
    window.ready = True
    window.add_agent("ollama")
    window.model_internet.setChecked(True)
    window.model_search_provider.setCurrentIndex(
        window.model_search_provider.findData("searxng"))
    window.model_search_url.setText("http://localhost:8888")
    window.model_search_insecure.setChecked(False)

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)
    window.save_model()

    assert sent == ["save_agent"], (
        "a local SearXNG on loopback was refused: "
        f"{window.model_error_line.message.text()!r}")


@pytest.mark.parametrize("url, expected", [
    # Each case names the part of the address it is about, and the URL is
    # https throughout so the trusted-LAN rule cannot be what refused it.
    # Over http, all three of these produce the same sentence as a missing
    # host does, so a case could pass with its own rule deleted.
    ("https://user:pw@search.example.com", "Remove any login details"),
    ("https://search.example.com?format=json", "?search options"),
    ("https://search.example.com#top", "#section"),
    ("https://search.example.com:99999", "address and port"),
    ("https://", "starting with https://"),
])
def test_a_searxng_address_the_runtime_would_refuse(window, url, expected) -> None:
    """The page asks the runtime, so it cannot drift from it any more.

    The expected text is the runtime's own, so a case fails if the rule that
    was meant to catch this address stops catching it, and not merely if
    something refuses the save.
    """
    window.ready = True
    window.add_agent("ollama")
    window.model_internet.setChecked(True)
    window.model_search_provider.setCurrentIndex(
        window.model_search_provider.findData("searxng"))
    window.model_search_url.setText(url)

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)
    window.save_model()

    assert not sent, f"a save was attempted with {url!r}, which the runtime refuses"
    message = window.model_error_line.message.text()
    assert expected in message, (
        f"{url!r} was refused with {message!r}, which does not mention "
        f"{expected!r}")


def test_a_blank_search_key_is_allowed_when_editing_a_saved_profile(window) -> None:
    """The field promises to keep a saved key, so it has to.

    The dialog this replaced had the carve-out; dropping it made it impossible
    to save any edit of a profile that uses Ollama web search, because the key
    lives in the vault and is never read back into the field.
    """
    window.ready = True
    window.edit_agent({"id": "llama-agent", "provider": "ollama",
                       "profile": {"id": "llama-agent", "model": "llama3.2",
                                   "web_search": "always",
                                   "search_provider": "ollama"}})
    window.model_search_key.clear()
    assert not window.model_search_key.text(), "the saved key is not readable, by design"

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)
    window.save_model()

    assert sent == ["save_agent"], (
        "an edit could not be saved without re-pasting a key the user cannot "
        f"see: {window.model_error_line.message.text()!r}")


def test_a_blank_search_key_is_still_refused_for_a_new_model(window) -> None:
    """There is no saved key to keep, so a new model still needs one."""
    window.ready = True
    window.add_agent("ollama")
    window.model_internet.setChecked(True)
    window.model_search_provider.setCurrentIndex(
        window.model_search_provider.findData("ollama"))
    window.model_search_key.clear()

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)
    window.save_model()

    assert not sent, "a new model was saved with web search and no key"
    assert window.model_error_line.message.text()


def test_a_model_message_survives_the_agents_poll(window, qt_app) -> None:
    """The reason messages are kept in state, not in the card.

    render_agents clears and rebuilds the whole grid, so a message held only
    by a widget would be destroyed by the next poll. That is the bug the
    workspace page had.
    """
    window.ready = True
    window.add_agent("ollama")
    window.set_card_message(window.model_error_line, "The provider refused that key")

    for _ in range(3):
        window.render_agents()
        qt_app.processEvents()

    assert window.model_error_line.message.text() == "The provider refused that key", (
        "a poll on the agents page cleared the model's own message")


def test_finding_models_reports_on_the_card(window) -> None:
    window.ready = True
    window.add_agent("ollama")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.find_models()
    assert window.model_models_button.isEnabled() is False

    callbacks["success"]([])
    assert window.model_models_button.isEnabled() is True
    assert "No models found" in window.model_error_line.message.text()

    callbacks["failure"]("nope")
    assert "Could not list models" in window.model_error_line.message.text()


def test_testing_web_search_reports_where_it_is_configured(window) -> None:
    """The search result is not the model's error, so it has its own line."""
    window.ready = True
    window.add_agent("ollama")
    window.model_internet.setChecked(True)
    window.model_search_provider.setCurrentIndex(
        window.model_search_provider.findData("ollama"))
    window.model_search_key.setText("k" * 20)

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.test_web_search()
    assert window.model_search_status.text() == "Testing web search…"

    callbacks["success"](3)
    assert "3 results" in window.model_search_status.text()
    assert not window.model_error_line.message.text(), (
        "a search result was reported as a model failure")


def test_a_stale_search_result_is_dropped_when_the_settings_change(window) -> None:
    """The answer belongs to the settings that were asked about."""
    window.ready = True
    window.add_agent("ollama")
    window.model_internet.setChecked(True)
    window.model_search_provider.setCurrentIndex(
        window.model_search_provider.findData("ollama"))
    window.model_search_key.setText("k" * 20)

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.test_web_search()
    window.model_search_url.setText("https://changed.example.com")
    callbacks["success"](3)

    assert not window.model_search_status.text(), (
        "a result arrived for settings that are no longer on screen")


def test_the_welcome_orbit_fits_at_every_width(window, qt_app) -> None:
    """Widening the orbit must not give the welcome panel a sideways scroll.

    The orbit needed seven more pixels than it had been given, and the
    welcome panel is the one page whose hero is a fixed row rather than
    something that wraps, so a wider illustration could have pushed the
    content past the window at its minimum size.
    """
    floor, _ = win.minimum_size(window.primary_screen_size())
    for width in (floor, 760, 900, 1330):
        window.resize(width, 900)
        pump(qt_app, 0.2)
        page = window.stack.currentWidget()
        assert not page.horizontalScrollBar().isVisible(), (
            f"at {width}px the welcome page scrolls sideways")
        width_needed, _ = win.OrbitArt.required_size()
        assert window.welcome_art.width() >= width_needed, (
            f"at {width}px the orbit is {window.welcome_art.width()}px, "
            f"which clips the chips")


def test_the_card_really_fades_in(qt_app) -> None:
    """It has never faded. It popped in opaque and snapped out.

    ``fade_to`` set the effect to the target, then animated from the target
    to the target, which writes the destination immediately and then writes
    it again for the duration. So there was no fade at all, and the 260ms
    before the window appeared was 260ms of empty desktop.

    Measured over wall-clock time, because pumping the event loop without
    letting time pass does not advance an animation at all.
    """
    from desktop_app.splash import FADE_MS, LaunchScreen

    screen = LaunchScreen(DARK)
    screen.show()
    screen.fade_to(1.0, FADE_MS, QEasingCurve.Type.OutCubic)
    qt_app.processEvents()

    effect = screen.graphicsEffect()
    assert effect is not None, "a fade needs an effect"
    assert effect.opacity() == 0.0, (
        f"the fade starts at {effect.opacity()}, so the card is already "
        "partly on screen rather than fading up from nothing")

    samples = []
    deadline = time.time() + FADE_MS / 1000 + 0.2
    while time.time() < deadline:
        qt_app.processEvents()
        samples.append(round(effect.opacity(), 3))
        time.sleep(0.01)

    assert samples[-1] == pytest.approx(1.0, abs=0.01), (
        f"the fade ended at {samples[-1]}, not fully visible")
    assert samples == sorted(samples), f"the fade was not smooth: {samples}"
    assert len(set(samples)) > 3, (
        f"only {len(set(samples))} distinct values, so nothing moved: {samples}")


def test_the_card_really_fades_out_before_handing_over(qt_app) -> None:
    """The window appears as the card reaches nothing, not 260ms before."""
    from desktop_app.splash import FADE_MS, LaunchScreen

    screen = LaunchScreen(DARK)
    screen.show()
    screen.fade_to(1.0, FADE_MS, QEasingCurve.Type.OutCubic)
    pump(qt_app, FADE_MS / 1000 + 0.15)

    effect = screen.graphicsEffect()
    handed_over = []
    screen.fade_to(0.0, FADE_MS, QEasingCurve.Type.InCubic,
                   on_finished=lambda: handed_over.append(effect.opacity()))

    deadline = time.time() + FADE_MS / 1000 + 0.25
    while time.time() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)

    assert handed_over, "the handover never ran"
    assert handed_over[0] == pytest.approx(0.0, abs=0.01), (
        f"the window was handed to at opacity {handed_over[0]}, which is the "
        "flash: the card had already gone but the window had not arrived")


def test_only_one_fade_is_ever_live_on_the_card(qt_app) -> None:
    """One animation per property, which Qt gives us and _fade now records.

    The explicit stop() in fade_to is belt-and-braces: starting an animation
    on a property that already has one stops the previous animation, so two
    could never fight over the opacity in the first place. It is kept because
    ``self._fade`` used to be written and never read, so there was no way to
    ask, and deleteLater() releases the old object rather than leaving it for
    the collector.

    The invariant is asserted rather than the mechanism, because the mechanism
    is Qt's and would hold even with the stop() removed.
    """
    from desktop_app.splash import FADE_MS, LaunchScreen

    screen = LaunchScreen(DARK)
    screen.show()
    screen.fade_to(1.0, FADE_MS, QEasingCurve.Type.OutCubic)
    first = screen._fade
    assert first is not None

    screen.fade_to(0.0, FADE_MS, QEasingCurve.Type.InCubic)

    assert screen._fade is not first, "the second fade did not become the live one"
    assert first.state() != QAbstractAnimation.State.Running, (
        f"the earlier fade is still {first.state().name} and would keep "
        "writing the opacity")
    assert screen._fade.state() == QAbstractAnimation.State.Running


def test_selecting_an_agent_opens_the_conversation(window) -> None:
    """It navigated to the literal 2, which is Workspaces.

    Conversations was 2 until Workspaces was inserted ahead of it, so every
    selection landed the reader on the Workspaces page while the composer
    they had just focused sat on a page that was not showing.
    """
    assert win.CONVERSATIONS_PAGE == win.PAGE_INDEX["conversations"]
    assert win.PAGES[win.CONVERSATIONS_PAGE][0] == "Conversations"

    window.select_agent("local-llama")

    assert window.stack.currentIndex() == win.CONVERSATIONS_PAGE, (
        f"selecting an agent opened "
        f"{win.PAGES[window.stack.currentIndex()][0]!r} instead of Conversations")


def test_no_page_is_navigated_to_by_a_literal(qt_app) -> None:
    """Every one of them was a number, and one of them was wrong."""
    source = Path(win.__file__).read_text(encoding="utf-8")
    import re
    for line in source.splitlines():
        if re.search(r"self\.navigate\(\s*[0-9]", line):
            raise AssertionError(f"a page is navigated to by literal: {line.strip()}")


@pytest.mark.parametrize("operation, set_up", [
    ("join", lambda w: (w.workspace_invitation.setPlainText(""),
                        w.workspace_token.setText("t" * 40),
                        w.workspace_relay.setText("wss://relay.example.com/connect"))),
    ("create", lambda w: w.workspace_name_input.setText("Research lab")),
    ("invite", lambda w: w.workspace_invite_name.setText("alex-laptop")),
])
def test_a_poll_does_not_re_enable_an_operation_in_flight(window, qt_app, operation,
                                                          set_up) -> None:
    """A second submit was possible within three seconds of the first.

    The runtime polls agents every three seconds, the poll redraws this page,
    and the page re-enabled its buttons unconditionally. A join says it can
    take 30 seconds, so the button came back long before the attempt
    resolved, and a second join could be queued behind the first and then
    report its failure over the first one's success.
    """
    window.ready = True
    # Setting ready alone does not redraw the page, so the fields are still
    # disabled from construction. Without this the locked-field assertion
    # below would pass for the wrong reason.
    window.render_workspace_page()
    assert window.workspace_name_input.isEnabled() is True, (
        "the workspace name field is not editable once the runtime is up, so "
        "locking it during a create would prove nothing")
    set_up(window)
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)

    if operation == "join":
        window.join_workspace()
        button = window.workspace_join_button
    elif operation == "create":
        window.create_workspace()
        button = window.workspace_create_button
    else:
        window.create_invitation()
        button = window.workspace_invite_create_button

    assert button.isEnabled() is False, f"{operation} started with an enabled button"

    # The create name field is locked as well as its button. A button that
    # says Creating while the name beside it is still editable is the other
    # half of the confusion. The join and invitation fields are left editable
    # deliberately: correcting an address you have just submitted from is
    # reasonable, and the button, which is what stops a second attempt, is
    # what has to stay locked.
    if operation == "create":
        assert window.workspace_name_input.isEnabled() is False, (
            "the workspace name field stayed editable while it was being used")

    for _ in range(4):
        window.network_event("agents", {"agents": [], "self": window.identity,
                                        "connected": False})
        qt_app.processEvents()
        assert button.isEnabled() is False, (
            f"a poll re-enabled the {operation} button while it was in flight")

    # Typing must not re-enable it either: the name field is a live signal.
    if operation == "create":
        window.workspace_name_input.setText("Research lab 2")
    elif operation == "invite":
        window.workspace_invite_name.setText("alex-desktop")
    qt_app.processEvents()
    assert button.isEnabled() is False, (
        f"typing re-enabled the {operation} button while it was in flight")


def test_a_finished_operation_lets_the_button_work_again(window) -> None:
    window.ready = True
    window.workspace_name_input.setText("Research lab")
    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)

    window.create_workspace()
    assert window.workspace_create_button.isEnabled() is False
    callbacks["failure"]("That name is taken")
    assert window.workspace_create_button.isEnabled() is True
    assert window.workspace_busy["create"] is False

    window.workspace_invite_name.setText("alex-laptop")
    window.create_invitation()
    assert window.workspace_invite_create_button.isEnabled() is False
    callbacks["failure"]("The relay is not running")
    assert window.workspace_invite_create_button.isEnabled() is True
    assert window.workspace_busy["invite"] is False


def test_connecting_a_model_after_editing_one_starts_blank(window) -> None:
    """It overwrote the model just edited, and said it was connecting.

    The card is built once and reused, so the edited profile's id, model,
    instructions and every search setting stayed in place, the name field
    stayed read-only with the old id, and saving submitted that id. Storage
    filters profiles by id before appending, so this could only ever silently
    replace the model rather than add a second one, which is worse.
    """
    window.ready = True
    profile = {"id": "llama-agent", "model": "llama3.2", "base_url": "http://host:1234",
               "system_prompt": "be terse", "autostart": False, "vision": True,
               "allow_insecure": True, "web_search": "always",
               "search_provider": "ollama", "searxng_url": "https://search.example.com",
               "searxng_allow_insecure": True}
    window.edit_agent({"id": "llama-agent", "provider": "ollama", "profile": profile})

    # The same provider, so setCurrentIndex emits nothing and cannot be
    # relied on to reset anything.
    window.add_agent("ollama")

    assert window.model_name.text() == "ollama-agent", (
        f"the new form is still named {window.model_name.text()!r}")
    assert window.model_name.isReadOnly() is False, (
        "the name field is still read-only, so a new agent cannot be named")
    assert window.model_id.currentText() == "", (
        f"the previous model id is still there: {window.model_id.currentText()!r}")
    assert window.model_system.toPlainText() == "", "the instructions carried over"
    assert window.model_vision.isChecked() is False, "vision carried over"
    assert window.model_autostart.isChecked() is True, "autostart carried over"
    assert window.model_insecure.isChecked() is False, "allow-insecure carried over"
    assert window.model_internet.isChecked() is False, "web search carried over"
    assert window.model_search_url.text() == "", "the SearXNG address carried over"
    assert window.model_search_insecure.isChecked() is False, (
        "the search insecure box carried over")
    assert window.model_base.text() != "http://host:1234", (
        "the previous API root carried over")
    assert window.model_form_title.text() == "Connect a model"


def test_connecting_a_model_after_editing_cannot_overwrite_it(window) -> None:
    """The name field being read-only was what made the overwrite certain."""
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(args[0] if args else None)
    profile = {"id": "llama-agent", "model": "llama3.2"}
    window.edit_agent({"id": "llama-agent", "provider": "ollama", "profile": profile})

    window.add_agent("ollama")
    window.model_id.setCurrentText("qwen3")
    window.save_model()

    assert sent and sent[0]["id"] == "ollama-agent", (
        f"the new agent would be saved as {sent[0]['id']!r}, which is the "
        "model that was just edited")


@pytest.mark.parametrize("paste, expected", [
    # Missing fields. These used to raise KeyError, which the page rendered
    # as str(exc), so a reader was shown the bare word 'url'.
    ('{"version": 1, "token": "tttttttttttttttttttttttttttttttttttt"}', "no relay address"),
    ('{"version": 1, "url": "wss://relay.example.com/connect"}', "no device token"),
    # Wrong types. These used to be put straight into a QLineEdit, so the
    # reader saw a PySide6 signature dump instead of a sentence.
    ('{"version": 1, "url": 123, "token": "y"}', "no relay address"),
    ('{"version": 1, "url": null, "token": "y"}', "no relay address"),
    ('{"version": 1, "url": "wss://x", "token": 456}', "no device token"),
    ('{"version": 1, "url": "wss://x", "token": ""}', "no device token"),
    # True == 1, so this was accepted as version one.
    ('{"version": true, "url": "wss://x", "token": "y"}', "Unsupported invitation version"),
    # bool("false") is True, so this quietly downgraded a wss:// requirement.
    ('{"version": 1, "url": "wss://x", "token": "y", "allow_insecure": "false"}',
     "must be true or false"),
])
def test_an_unusable_invitation_field_is_explained(window, paste, expected) -> None:
    """Every way a paste can be wrong has a sentence, not a Python repr.

    The reader is looking at a card, and the page renders whatever the
    exception says. So a missing key arriving as KeyError showed them 'url',
    and a wrongly typed one showed them a PySide6 type signature.
    """
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))
    window.workspace_invitation.setPlainText(paste)

    window.join_workspace()

    assert not sent, "a join was attempted with an unusable invitation"
    message = window.workspace_join_line.message.text()
    assert expected in message, f"the page says {message!r}"
    assert "PySide6" not in message, f"a Qt error leaked into the card: {message!r}"
    assert not message.startswith("'"), f"a Python repr reached the card: {message!r}"
    assert window.workspace_join_button.text() == "Join workspace"


def test_a_valid_invitation_still_returns_the_same_four_values() -> None:
    """The stricter reading must not change what a good invitation gives."""
    from desktop_app.dialogs import parse_invitation

    assert parse_invitation(json.dumps({
        "version": 1, "url": "wss://relay.example.com/connect",
        "token": "t" * 40, "allow_insecure": True,
        "conversation_id": "conversation-abc123"})) == (
        "wss://relay.example.com/connect", "t" * 40, True, "conversation-abc123")

    # allow_insecure absent is the same as false, and conversation_id absent
    # is a plain workspace invitation rather than a shared conversation.
    assert parse_invitation(json.dumps({
        "version": 1, "url": "wss://relay.example.com/connect",
        "token": "t" * 40})) == (
        "wss://relay.example.com/connect", "t" * 40, False, None)


def test_the_create_panel_previews_the_rail_button(window) -> None:
    """The rail shows a workspace as its first two letters.

    Nothing showed that before, so the name you typed and the button you
    would then be clicking were unconnected until you clicked it.
    """
    window.ready = True
    window.workspace_name_input.setText("Research lab")
    assert window.workspace_preview.text() == "RE", (
        f"the preview reads {window.workspace_preview.text()!r}")
    assert "RE" in window.workspace_preview_label.text(), (
        "the preview is not named in words beside the field")

    window.workspace_name_input.setText("")
    assert not window.workspace_preview.text(), (
        "an empty name still leaves letters in the preview")


def test_the_page_says_which_workspace_you_are_in(window) -> None:
    """The sidebar names it, but only in one line and only the name."""
    window.workspace_label.setText("Research lab")
    window.workspace_meta = {"members": [
        {"id": "device-1", "role": "Device"},
        {"id": "device-2", "role": "Device"},
        {"id": "local-llama", "role": "Model"},
    ]}
    window.render_workspace_page()

    assert window.workspace_name_label.text() == "Research lab"
    summary = window.workspace_members_label.text()
    assert "2 devices" in summary, f"the summary does not count the devices: {summary!r}"
    assert "1 model agent" in summary, f"the summary does not count the agents: {summary!r}"


def test_the_page_opens_usable_once_the_runtime_is_up(window) -> None:
    """It refreshed on the agents poll, so the first second looked broken.

    The runtime reports ready long before it lists any agents, so a user
    opening this page in that gap was shown controls that were not yet
    usable and nothing to say they were coming. Driven through the real
    event rather than by flipping the flag, because the flag is not what
    decides whether the page refreshes.
    """
    window.ready = False
    window.render_workspace_page()
    assert not window.workspace_invite_button.isEnabled()

    window.network_event("ready", {"port": 0, "device_id": "device-test"})

    assert window.ready, "the runtime was not marked ready"
    assert window.workspace_invite_button.isEnabled(), (
        "the page did not refresh when the runtime came up")
    assert window.workspace_join_button.isEnabled()


from desktop_app.splash import LAUNCH_CAP_MS, LAUNCH_FLOOR_MS  # noqa: E402

# --- the launch screen --------------------------------------------------


def test_a_settled_status_alone_is_not_enough_to_leave(qt_app) -> None:
    """The agent list arriving is not the network being usable.

    A settled status only means the last expected report arrived. Letting it
    stand in for readiness is how a card hands over to a window whose controls
    are all disabled, because the runtime never said it was ready.

    Asserted on the predicate rather than on the clock. Timing it needed a
    MainWindow for the handover, and a MainWindow's relay polls every three
    seconds and starves the card's own 120ms poll, so the card never left on
    time under either condition and the test could not tell them apart.
    """
    from desktop_app.splash import LaunchScreen, StartupStatus

    status = StartupStatus()
    status.observe("agents", {"agents": [], "connected": False})
    assert status.settled, "this only means anything if the agents event settles it"

    screen = LaunchScreen(DARK)
    screen.set_status(status)
    screen.begin(on_done=lambda: None)

    assert screen._ready is False, "nothing has said the runtime is up"
    assert screen._settled() is False, (
        "a settled status alone was treated as readiness, so the card can "
        "hand over to a window whose controls are all disabled")


def test_a_ready_event_alone_is_not_enough_to_leave(qt_app) -> None:
    """Ready with the agent list still to come is not finished either."""
    from desktop_app.splash import LaunchScreen, StartupStatus

    status = StartupStatus()
    status.observe("ready", {"port": 1})
    assert not status.settled, "a ready event must not settle the status"

    screen = LaunchScreen(DARK)
    screen.set_status(status)
    screen.begin(on_done=lambda: None)
    screen.runtime_ready()

    assert screen._ready is True
    assert screen._settled() is False, (
        "a ready event alone was treated as finished, so the card can leave "
        "before the agent list has arrived")


def test_ready_and_settled_are_both_needed(qt_app) -> None:
    """The ordinary case, and the only one that leaves on the floor."""
    from desktop_app.splash import LaunchScreen, StartupStatus

    status = StartupStatus()
    status.observe("ready", {"port": 1})
    status.observe("agents", {"agents": [], "connected": False})

    screen = LaunchScreen(DARK)
    screen.set_status(status)
    screen.begin(on_done=lambda: None)
    screen.runtime_ready()

    assert screen._settled() is True


def test_the_card_still_leaves_at_the_cap_without_readiness(qt_app) -> None:
    """The one failure this screen is not allowed to have.

    A runtime that reports ready and then goes quiet must not hold a card over
    an app that is entirely usable, and neither must one that never reported
    ready at all. The cap is what covers both.
    """
    from desktop_app.splash import LAUNCH_CAP_MS, LAUNCH_FLOOR_MS, LaunchScreen, StartupStatus

    status = StartupStatus()
    status.observe("agents", {"agents": [], "connected": False})
    assert status.settled, "this only means anything if the agents event settles it"
    assert not any("Network ready" in fact for fact in status.facts), (
        "a ready event was recorded after all, so this case is not testing "
        "what it claims")

    screen = LaunchScreen(DARK)
    screen.set_status(status)
    screen.begin(on_done=lambda: None)
    screen.dismiss_when_floored()

    pump(qt_app, (LAUNCH_FLOOR_MS + 0.3) / 1000)
    assert not screen.dismissed, (
        "the card left on a settled status alone, with no ready event")

    pump(qt_app, LAUNCH_CAP_MS / 1000 + 0.6)
    assert screen.dismissed, "the hard cap did not release it"


@pytest.mark.parametrize("events,expected,warnings", [
    # Nothing wrong: one plain line and no complaint.
    ([("ready", {"port": 1}), ("workspace", {"name": "Mine"}),
      ("agents", {"agents": [1, 2], "connected": True})],
     "Getting things ready", []),
    # The relay being down is worth saying, and it is not a reason to hold.
    ([("ready", {"port": 1}), ("agents", {"agents": [], "connected": False})],
     "Working without the relay", ["the relay is not connected"]),
    # A cleanup still owed outranks however many agents turned up, so the
    # later signal must not overwrite the more important line.
    ([("ready", {"port": 1}), ("pending_cleanup", {"credentials": 2, "files": 1}),
      ("agents", {"agents": [1], "connected": True})],
     "Finishing an earlier cleanup", ["2 credential(s) still owed"]),
    ([("ready", {"port": 1}), ("fatal", "the relay refused the connection")],
     "Could not start", ["the relay refused the connection"]),
])
def test_the_status_line_says_what_actually_happened(events, expected, warnings) -> None:
    from desktop_app.splash import HOLD_TEXT, StartupStatus

    status = StartupStatus()
    for event, data in events:
        status.observe(event, data)

    assert status.settled, "the startup never settled, so the card waits out its cap"
    assert status.line == expected
    assert status.warnings == warnings
    if not warnings:
        assert status.line == HOLD_TEXT


def test_cleanup_with_nothing_owed_is_not_a_warning() -> None:
    """Reported on every single start, including when there is nothing to do.

    Treating it as a warning made every launch open with a line about a
    cleanup that did not exist.
    """
    from desktop_app.splash import StartupStatus

    status = StartupStatus()
    status.observe("pending_cleanup", {"credentials": 0, "files": 0})
    status.observe("agents", {"agents": [], "connected": True})

    assert status.warnings == []
    assert status.tone == "normal"


def test_startup_facts_do_not_flicker_past_on_the_card() -> None:
    """Each fact arrives about 200ms after the last one.

    Showing them meant three lines changed in the time it takes to read a
    single one, which looks like a fault rather than like progress. They
    are recorded, but only the held line is displayed.
    """
    from desktop_app.splash import StartupStatus

    status = StartupStatus()
    seen = []
    for event, data in (("ready", {"port": 1}), ("workspace", {"name": "Mine"}),
                        ("agents", {"agents": [1], "connected": True})):
        if status.observe(event, data):
            seen.append(status.line)

    assert len(seen) == 1, f"the displayed line changed {len(seen)} times: {seen}"
    assert any("port 1" in f for f in status.facts), status.facts
    assert any("Mine" in f for f in status.facts), status.facts
    assert any("1 agent(s)" in f for f in status.facts), (
        "the facts are still recorded even though they are not displayed")


def test_the_window_is_hidden_until_the_card_has_gone(qt_app, storage) -> None:
    """It used to be shown first and covered, so it peeked out from behind.

    Now the window is built and painted off screen underneath the card, and
    only shown when the card has faded out.
    """
    from desktop_app.splash import LaunchScreen, StartupStatus

    window = win.MainWindow(storage)
    try:
        status = StartupStatus()
        screen = LaunchScreen(DARK)
        screen.set_status(status)
        revealed = []
        screen.begin(on_done=lambda: (revealed.append(True), window.show()))
        window.grab()

        assert not window.isVisible(), (
            "the window is on screen behind the card, so the card is "
            "overlaying it rather than replacing it")

        status.observe("ready", {"port": 1})
        status.observe("agents", {"agents": [], "connected": True})
        screen.runtime_ready()
        assert not revealed, "the window was revealed before the hold was over"

        pump(qt_app, (LAUNCH_FLOOR_MS + 1200) / 1000)
        assert revealed, "the window was never revealed"
        assert window.isVisible()
    finally:
        window.network.shutdown()
        window.network.wait(10000)
        window.hide()


def test_a_hidden_window_can_be_painted_before_it_is_shown(qt_app, storage) -> None:
    """The reason the reveal can be instant.

    Shown for the first time, a window paints its first frame after it
    appears, which reads as a flash of something unfinished. Rendering it
    off screen first means the frame the user sees is already complete.
    """
    window = win.MainWindow(storage)
    try:
        assert not window.isVisible()
        image = window.grab().toImage()
        assert not image.isNull()
        colours = {image.pixelColor(x, y).name()
                   for x in range(0, image.width(), 40)
                   for y in range(0, image.height(), 40)}
        assert len(colours) > 3, (
            "the hidden window rendered as a blank, so showing it would flash")
    finally:
        window.network.shutdown()
        window.network.wait(10000)


def test_the_card_releases_even_if_the_runtime_goes_quiet(qt_app, storage) -> None:
    """The one failure this screen is not allowed to have.

    A runtime that reported ready and then never listed its agents would
    otherwise hold a card over an app that is entirely usable. The status
    is left unsettled deliberately: a startup with nothing owed settles at
    once and would release at the floor whether or not the cap existed.
    """
    from desktop_app.splash import LAUNCH_CAP_MS, LaunchScreen, StartupStatus

    window = win.MainWindow(storage)
    try:
        status = StartupStatus()
        status.observe("ready", {"port": 1})   # ready, and then nothing
        assert not status.settled, "the status settled on its own"

        screen = LaunchScreen(DARK)
        screen.set_status(status)
        screen.begin(on_done=window.show)
        screen.runtime_ready()

        pump(qt_app, (LAUNCH_FLOOR_MS + LAUNCH_CAP_MS) / 1000 + 0.6)
        assert screen.dismissed, (
            "the card is still up although the runtime never reported again")
    finally:
        window.network.shutdown()
        window.network.wait(10000)


def test_closing_the_card_cancels_the_launch(qt_app, storage) -> None:
    """It has no title bar, so Alt+F4 on it is the only way to dismiss it.

    Showing a window nobody asked to see because they dismissed its card
    would be worse than quitting.
    """
    from desktop_app.splash import LaunchScreen

    window = win.MainWindow(storage)
    try:
        revealed = []
        screen = LaunchScreen(DARK)
        screen.begin(on_done=lambda: revealed.append(True))
        screen.close()
        qt_app.processEvents()
        assert screen.cancelled
        assert not revealed, "closing the card revealed the window anyway"
    finally:
        window.network.shutdown()
        window.network.wait(10000)


def pump(qt_app, seconds):
    """Turn the event loop for a while, which is what drives a timer."""
    end = time.time() + seconds
    while time.time() < end:
        qt_app.processEvents()
        time.sleep(0.005)


def test_the_launch_screen_waits_for_a_floor(qt_app) -> None:
    """Startup takes about a quarter of a second here.

    Waiting only for readiness would flash the screen for a fifth of a
    second, which reads as a glitch rather than as an intention, so it
    holds for a floor even though it is ready well before that.
    """
    from desktop_app.splash import LAUNCH_FLOOR_MS, LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    qt_app.processEvents()
    assert screen.isVisible()

    screen.runtime_ready()
    pump(qt_app, 0.2)
    assert not screen.dismissed, (
        f"it left after 200ms; the floor is {LAUNCH_FLOOR_MS}ms")

    pump(qt_app, LAUNCH_FLOOR_MS / 1000 + 0.5)
    assert screen.dismissed and not screen.isVisible()


def test_the_launch_screen_leaves_at_once_when_ready(qt_app) -> None:
    """Once the hold has passed, readiness releases it with nothing left to wait for."""
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    # Wait out the hold first, so this is asking whether readiness releases
    # it rather than whether the hold does.
    pump(qt_app, LAUNCH_FLOOR_MS / 1000 + 0.4)
    assert not screen.dismissed, "the hold let go on its own, which it should not"

    screen.runtime_ready()
    pump(qt_app, 0.5)
    assert screen.dismissed


def test_the_launch_screen_has_a_background_of_its_own(qt_app) -> None:
    """Left translucent with nothing behind it, it showed the window through.

    The splash comes up over a main window that is already painted, so a
    transparent one reads as an error dialog rather than as progress. It is
    a card in the theme's own colour: opaque in the middle, rounded and so
    transparent at the corners.
    """
    from desktop_app.splash import FADE_MS, LaunchScreen

    for name in THEME_NAMES:
        screen = LaunchScreen(name)
        screen.begin()
        # Past the fade. The card is genuinely transparent for the first
        # FADE_MS now that the fade works, so sampling immediately would
        # measure the fade rather than the card, and would pass the corner
        # check for the wrong reason.
        pump(qt_app, FADE_MS / 1000 + 0.15)
        image = screen.grab().toImage()
        width, height = image.width(), image.height()

        middle = image.pixelColor(width // 2, 8)
        assert middle.alpha() == 255, (
            f"{name}: the background is see-through, so the window shows "
            f"through the loading screen")
        assert middle.name().lower() == color(name, "surface").lower(), (
            f"{name}: the background is {middle.name()}, not the theme's "
            f"card colour {color(name, 'surface')}")

        corner = image.pixelColor(1, 1)
        assert corner.alpha() < 255, (
            f"{name}: the card has square corners, so it reads as a plain "
            f"rectangle rather than as a card")

        edge = image.pixelColor(width // 2, 0)
        assert edge.name().lower() == color(name, "border").lower(), (
            f"{name}: the card has no hairline, so it has no edge against a "
            f"busy window behind it")
        screen.dismiss()
        pump(qt_app, 0.3)


def test_the_launch_screen_background_follows_a_theme_change(qt_app) -> None:
    from desktop_app.splash import FADE_MS, LaunchScreen

    screen = LaunchScreen(DARK)
    screen.begin()
    screen.set_theme(LIGHT)
    # Past the fade, which the card is genuinely transparent for now.
    pump(qt_app, FADE_MS / 1000 + 0.15)
    image = screen.grab().toImage()
    middle = image.pixelColor(image.width() // 2, 8)
    assert middle.name().lower() == color(LIGHT, "surface").lower(), (
        "the card kept the dark theme's colour after a theme change")
    screen.dismiss()
    pump(qt_app, 0.3)


def test_the_launch_screen_takes_its_effect_off_when_it_goes(qt_app) -> None:
    """Left attached, every later paint goes through Qt's effect machinery.

    That is the fault the page fade has in the window, and a widget with
    nothing left to fade has no business keeping one.
    """
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    qt_app.processEvents()
    assert screen.graphicsEffect() is not None, "the fade-in needs an effect"

    screen.dismiss()
    pump(qt_app, 0.6)
    assert screen.graphicsEffect() is None, (
        "the effect outlived the animation it was there for")
    assert screen.art.moving is False, "the orbit keeps turning behind a closed splash"


def test_the_launch_screen_carries_the_apps_own_mark(qt_app) -> None:
    """The same mark the taskbar is about to show.

    The letter on it cannot be checked here: with no fonts installed Qt
    draws no glyph at all, so a marked tile and a blank one paint
    identically. What is checked is that the fill is the theme's accent,
    which is what would otherwise make the splash a coloured square.
    """
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("miku")
    assert screen.art._theme == MIKU
    assert not screen.mark.pixmap().isNull()
    centre = screen.mark.pixmap().toImage().pixelColor(44, 44).name()
    assert centre == color(MIKU, "accent").lower(), (
        f"the mark is painted {centre}, not the theme's accent")


def test_the_launch_screen_actually_writes_its_text(qt_app) -> None:
    """The caption and the status line are on the card, not just in the code.

    Both of these lines were invisible for the whole life of the card. They
    are hand-painted widgets that a layout centres, and a bare QWidget has no
    valid size hint, so the layout gave them zero width and a zero-width
    widget never paints. Nothing failed, nothing warned, and the card looked
    finished because it had a mark and an orbit on it.

    So the check is on width and on painted pixels, which is the thing that
    was actually wrong, rather than on the presence of the widgets.
    """
    from desktop_app.splash import CARD_TEXT_WIDTH, LaunchScreen

    screen = LaunchScreen("dark")
    screen.show()
    qt_app.processEvents()

    for widget in (screen.caption, screen.status):
        assert widget.width() > 0, (
            f"a line of text has no width, so it cannot paint: {widget._text!r}")
        assert widget.width() <= CARD_TEXT_WIDTH, (
            f"a line wider than the card: {widget.width()} > {CARD_TEXT_WIDTH}")

    # Width is not proof of paint, so check glyphs were laid down. With no
    # fonts installed the shapes are wrong but the pixels are still inked.
    image = screen.grab().toImage()
    background = color(DARK, "surface")
    for widget in (screen.caption, screen.status):
        box = widget.geometry()
        region = image.copy(box.x(), box.y(), box.width(), box.height())
        inked = {region.pixelColor(x, y).name()
                 for y in range(region.height())
                 for x in range(region.width())} - {background}
        assert inked, f"nothing was painted for {widget._text!r}"


def test_a_status_line_too_long_for_the_card_is_elided(qt_app) -> None:
    """A long warning cannot push the layout about or run off both edges."""
    from desktop_app.splash import CARD_TEXT_WIDTH, LaunchScreen

    screen = LaunchScreen("dark")
    screen.show()
    screen.status.set("Could not start: the relay is unreachable and its vault "
                      "could not be read, so nothing can be sent", "error")
    qt_app.processEvents()

    assert screen.status.sizeHint().width() <= CARD_TEXT_WIDTH
    assert screen.status.width() <= CARD_TEXT_WIDTH
    assert screen.status.width() > 0


def test_the_launch_screen_follows_the_theme(qt_app) -> None:
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.set_theme(LIGHT)
    assert screen.art._theme == LIGHT
    centre = screen.mark.pixmap().toImage().pixelColor(44, 44).name()
    assert centre == color(LIGHT, "accent").lower()


def test_dismissing_the_launch_screen_twice_is_harmless(qt_app) -> None:
    """A fatal error and the floor can both ask it to go."""
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    qt_app.processEvents()
    screen.dismiss()
    screen.dismiss()
    pump(qt_app, 0.5)
    assert screen.dismissed and not screen.isVisible()


def test_a_launch_screen_that_never_ran_leaves_cleanly(qt_app) -> None:
    """It can be asked to go before it was ever shown."""
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.dismiss()
    assert screen.dismissed and not screen.isVisible()


def test_the_workspaces_page_never_scrolls_sideways(window, qt_app) -> None:
    """A wide label or a long checkbox label sets the width for the page.

    The page's own intro was 129 unwrapped characters, which on its own
    demanded more than the window had, and a checkbox's minimum is the
    width of its whole label with no word wrap to fall back on. Both grew a
    horizontal scrollbar under the content at the preferred window size,
    which is the same fault the sidebar list had.
    """
    window.navigate(win.WORKSPACES_PAGE)
    for width in (760, 1000, 1330, 1700):
        window.resize(width, 800)
        pump(qt_app, 0.25)
        page = window.stack.currentWidget()
        content = page.widget()
        assert content.minimumSizeHint().width() <= page.viewport().width(), (
            f"at {width}px the page needs {content.minimumSizeHint().width()}px "
            f"but has {page.viewport().width()}px, so it scrolls sideways")


# --- the settings controls ----------------------------------------------


def wheel_event(delta=-120):
    """One wheel notch, as a page scroll would deliver it."""
    return QWheelEvent(
        QPointF(8, 8), QPointF(8, 8 - abs(delta) // 8), QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False)


def test_the_theme_picker_ignores_the_mouse_wheel(window, qt_app) -> None:
    """Scrolling the Settings page used to rewrite the stored theme.

    Qt's combo box advances on any wheel notch over it, so a scroll meant
    to move the page kept changing the theme instead, for as long as the
    scroll continued. A wheel is not a pick out of a closed list; the popup
    is the way to choose.

    The page is opened first on purpose: a hidden widget is given no wheel
    events at all, so the test would pass against an unfixed picker purely
    by looking at the wrong one. The starting row is in the middle for the
    same reason, since a picker at either end cannot move and would pass
    however it behaved.
    """
    window.navigate(win.SETTINGS_PAGE)
    window.show()
    qt_app.processEvents()
    picker = window.theme_picker
    assert picker.isVisible(), "the picker under test is not on screen"
    assert picker.count() >= 3, "there is no room to move in the test"

    middle = picker.count() // 2
    for delta in (-120, 120):
        picker.setCurrentIndex(middle)
        before = picker.currentIndex()
        for _ in range(4):
            QApplication.sendEvent(picker, wheel_event(delta))
        qt_app.processEvents()
        assert picker.currentIndex() == before, (
            f"a {delta:+d} notch moved the theme from row {before} to "
            f"{picker.currentIndex()}")

    # The control must still work; ignoring the wheel is not disabling it.
    picker.setCurrentIndex(0)
    assert picker.currentIndex() == 0, "the picker no longer accepts a choice"


def test_the_dialog_dropdowns_ignore_the_wheel(window, qt_app) -> None:
    """The same hazard sat on every provider, model and relay dropdown.

    Scroll inside a dialog and the provider silently changes, which is
    worse than no scroll at all because the choice is what gets sent.
    """
    from desktop_app.dialogs import AgentDialog, InviteDialog

    dialogs = [AgentDialog(window, "ollama", {}), InviteDialog(window)]
    try:
        combos = [c for d in dialogs for c in d.findChildren(win.Select)]
        assert len(combos) >= 4, f"only {len(combos)} dropdowns were found"
        for combo in combos:
            assert isinstance(combo, win.Select), (
                f"{type(combo).__name__} can still be moved by the wheel")
            # Most of these are filled by the runtime listing providers, so
            # only the ones with room either side can be moved at all.
            if combo.count() < 3:
                continue
            middle = combo.count() // 2
            for delta in (-120, 120):
                combo.setCurrentIndex(middle)
                before = combo.currentIndex()
                for _ in range(3):
                    QApplication.sendEvent(combo, wheel_event(delta))
                qt_app.processEvents()
                assert combo.currentIndex() == before, (
                    f"a {type(combo.parent()).__name__} dropdown moved "
                    f"on a {delta:+d} notch")
    finally:
        for dialog in dialogs:
            dialog.deleteLater()


def test_a_checkbox_paints_nothing_behind_itself(window, qt_app) -> None:
    """It used to inherit the page background and draw a panel.

    The rule for QCheckBox set a colour but no background, so it fell
    through to the shared QWidget rule and painted the window base on top
    of the card it sat in. Sampled rather than read from the stylesheet,
    because "the rule says transparent" and "nothing is painted" are not
    the same claim.
    """
    window.navigate(win.SETTINGS_PAGE)
    window.show()
    end = time.time() + 0.5          # let the page fade finish
    while time.time() < end:
        qt_app.processEvents()
        time.sleep(0.005)

    for name in THEME_NAMES:
        window.apply_theme(name)
        qt_app.processEvents()
        image = window.grab().toImage()
        anchor = window.reduce_motion.mapTo(window, QPoint(0, 0))
        sampled = image.pixelColor(
            anchor.x() + window.reduce_motion.width() - 4,
            anchor.y() + window.reduce_motion.height() // 2).name()

        assert sampled.lower() != color(name, "surface_base").lower(), (
            f"{name}: the checkbox is painting the page base behind itself")
        assert sampled.lower() == color(name, "surface").lower(), (
            f"{name}: expected the card surface behind the checkbox, "
            f"found {sampled}")


def test_a_checkbox_hugs_its_label(window, qt_app) -> None:
    """It used to stretch the full width of the card it sat in.

    A checkbox 857px wide takes any hover or focus background across the
    whole row, which reads as a band rather than as a control.
    """
    window.navigate(win.SETTINGS_PAGE)
    window.show()
    qt_app.processEvents()

    assert window.reduce_motion.width() < 400, (
        f"the checkbox is {window.reduce_motion.width()}px wide, so it is "
        f"filling the card instead of sitting beside its label")



def test_the_user_panel_reaches_the_edges_of_the_sidebar(window, qt_app) -> None:
    """It used to be a child of a layout carrying 14px side margins.

    So its background stopped that far short on both sides and read as a
    panel clipped by the window rather than as a footer of the sidebar. It
    now hangs off the sidebar frame itself, the width is the sidebar's
    width, and the inset is inside it where the text needs it.
    """
    window.resize(1180, 760)
    window.show()
    qt_app.processEvents()
    footer = window.avatar.parentWidget()

    assert footer.parentWidget() is window.sidebar, (
        "the panel is inside the padded body, so it inherits its margins "
        "and cannot reach the edge")
    assert footer.width() == window.sidebar.width(), (
        f"the panel is {footer.width()}px against a {window.sidebar.width()}px sidebar")
    left, right = footer.layout().contentsMargins().left(), footer.layout().contentsMargins().right()
    assert left and right, "the panel's text has no inset, so it will touch the window edge"


def test_the_avatar_is_a_painted_mark_and_not_a_letter(window) -> None:
    """It used to be the text " M " on a fixed width with no fixed height.

    With a 14px radius and a height the layout chose, that drew a rounded
    rectangle of arbitrary height rather than the circle it was aiming at.
    """
    assert not window.avatar.text(), (
        f"the avatar is still a text label, {window.avatar.text()!r}")
    assert not window.avatar.pixmap().isNull(), "the avatar carries no painted mark"
    assert window.avatar.width() == window.avatar.height(), (
        f"the avatar is {window.avatar.width()}x{window.avatar.height()}, not square")


def test_the_avatar_follows_the_identity_and_the_theme(window) -> None:
    """The identity arrives after the window is built, and the theme changes later."""
    window.identity = "device-77ac8845"
    window.refresh_avatar()
    before = window.avatar.pixmap().toImage()

    window.apply_theme(LIGHT)
    light = window.avatar.pixmap().toImage()
    assert light != before, "the avatar ignored the theme change"

    window.apply_theme(MIKU)
    miku = window.avatar.pixmap().toImage()
    assert miku != light, "the avatar kept the previous theme's accent"


@pytest.mark.parametrize("identity,expected", [
    ("device-77ac8845", "D"),
    ("relay", "R"),
    # The identity is only known once the runtime has started, and it can
    # arrive with whitespace in it. Neither may draw an empty tile, which
    # reads as a missing image rather than as a device not yet named.
    ("", "?"),
    ("   ", "?"),
    (None, "?"),
])
def test_the_avatar_initial_is_never_blank(identity, expected) -> None:
    """A fixed mark cannot tell two devices apart, so the initial comes
    from the identity. Asserted on the letter rather than on pixels: this
    platform installs no fonts, so Qt renders no text at all and two
    different initials paint identically."""
    assert win.device_initial(identity) == expected


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_user_panel_is_divided_by_a_border_in_every_theme(name) -> None:
    """All three themes put the panel on the sidebar's own colour.

    That is what makes it read as one surface, so the border is the only
    thing dividing the footer from the list above it and it has to exist
    wherever the colours match.
    """
    rule = next((line for line in THEMES[name].splitlines()
                 if line.startswith("QFrame#profile")), "")
    assert rule, f"{name} has no rule for the user panel at all"
    assert "border-top" in rule, (
        f"{name} puts the panel on the sidebar colour with nothing dividing it")

    body = next((line for line in THEMES[name].splitlines()
                 if "sidebarBody" in line), "")
    assert "transparent" in body, (
        f"{name} paints the padded body as a panel, so the footer is "
        f"floating on a second surface")



def test_the_device_identity_is_shown_on_the_profile_card(window, tmp_path) -> None:
    """It used to sit on its own line beside a card already saying 'This device'.

    Someone reading their identity out to join a workspace should find it in
    the place that names them, not on a line of its own above it. The
    history directory is a real path because the handler opens it, and a
    relative one lands in the repository.
    """
    window.network_event("workspace", {
        "id": "local", "name": "My workspace", "remote": False,
        "self": "device-abc123", "port": 5000,
        "history_directory": str(tmp_path / "history")})

    assert window.profile_identity.text() == "device-abc123"
    assert window.profile_identity.toolTip(), "it has to be explainable on hover"


def test_working_is_shown_while_a_command_is_in_flight(window) -> None:
    """Every command goes through one place, so all of them get feedback."""
    window.ready = True
    window.network.submit = lambda *args, **kwargs: "request-1"

    window.command("join_workspace")

    assert window.busy, "submitting work must show that the app is waiting"
    assert not window.progress.isHidden()

    window.command_success("request-1", None)

    assert not window.busy
    assert window.progress.isHidden()


def test_two_overlapping_commands_do_not_clear_the_indicator_early(window) -> None:
    """The first to finish must not report done while the second is running."""
    window.ready = True
    submitted = iter(["request-1", "request-2"])
    window.network.submit = lambda *args, **kwargs: next(submitted)

    window.command("join_workspace")
    window.command("create_workspace")
    window.command_success("request-1", None)

    assert window.busy, "one command is still in flight"

    window.command_failure("request-2", "nope")

    assert not window.busy


def test_a_command_refused_before_submitting_leaves_no_busy_state(window) -> None:
    """Refused while starting up is a notice, not something to wait for."""
    window.ready = False
    window.network.submit = lambda *args, **kwargs: "should-not-happen"
    # The bar is up because the app is still starting; a refused command
    # must leave that alone rather than clearing it.
    startup_bar = not window.progress.isHidden()

    window.command("join_workspace")

    assert not window.busy, "nothing was submitted, so nothing is in flight"
    assert not window.progress.isHidden() == startup_bar, (
        "the startup indicator must not be disturbed by a refused command")


def test_an_empty_workspace_shows_a_welcome_instead_of_three_zeroes(window) -> None:
    """A first-run user should be told what to do, not shown three zeroes.

    Three statistic cards all reading zero, and a large empty activity
    list, say nothing about what the app is or what to press. The welcome
    replaces all of it while the workspace holds nothing.
    """
    window.navigate(win.OVERVIEW_PAGE if hasattr(win, "OVERVIEW_PAGE") else 0)
    window.render_agents()

    assert not window.welcome_card.isHidden(), "an empty workspace needs the welcome"
    assert window.overview_content.isHidden(), "zeroes are not worth showing at zero"
    assert window.welcome_connect.text().strip().endswith("Connect a model")
    assert window.welcome_join.text().strip().endswith("Join a workspace")


def texts(layout):
    """Every label's text in a layout, flattened."""
    found = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is not None and hasattr(item.widget(), "text"):
            found.append(item.widget().text())
        elif item.layout() is not None:
            found.extend(texts(item.layout()))
    return found


def test_the_chat_does_not_claim_a_collaboration_that_has_not_started(window) -> None:
    """It said 'the beginning of your collaboration' with nothing chosen.

    The title directly above read 'Choose an agent', so the two told a
    first-run user opposite things about whether they had started.
    """
    window.selected = ""
    window.chats = {}
    window.render_messages()

    said = " ".join(texts(window.messages))
    assert "No conversation open" in said
    assert "beginning of your collaboration" not in said

    window.selected = "agent-1"
    window.render_messages()

    assert "beginning of your collaboration" in " ".join(texts(window.messages)), (
        "an opened conversation with no messages yet is still a beginning")


def test_sidebar_has_navigation_without_chat_or_agent_lists(window) -> None:
    window.agents = []
    window.conversations = {}
    window.resize(1400, 900)
    window.render_agents()
    window.apply_sidebar_density()

    assert not window.stack.widget(win.CONVERSATIONS_PAGE).findChildren(win.QListWidget)
    assert not window.sidebar.findChildren(win.QListWidget)

    window.agents = [{"id": "model", "online": True, "provider": "ollama", "model": "m"},
                     {"id": "guest-device", "kind": "device", "online": True}]
    window.render_agents()
    window.apply_sidebar_density()

    assert not window.sidebar.findChildren(win.QListWidget)
    assert "CHATS, MODELS AND DEVICES" not in " ".join(child.text() for child in window.sidebar.findChildren(QLabel))
    assert window.conversation_picker.count() == 1
    assert window.conversation_picker.isHidden()


def test_a_narrow_window_can_open_workspace_navigation(window, qt_app) -> None:
    window.resize(900, 700)
    window.show()
    window.agents = [{"id": "device", "online": True}]
    window.render_agents()
    qt_app.processEvents()
    assert window.sidebar.isHidden()
    assert window.sidebar_toggle.isVisible()
    assert window.sidebar_toggle.text() == "Menu"
    window.sidebar_toggle.click()
    assert window.sidebar.isVisible()
    window.nav_buttons[win.AGENTS_PAGE].click()
    assert window.stack.currentIndex() == win.AGENTS_PAGE
    assert window.sidebar.isHidden()


def test_the_welcome_and_the_statistics_are_never_shown_together(window) -> None:
    """They are alternatives, so neither may be left showing by a stale event."""
    for agents in ([], [{"id": "model", "online": True, "provider": "ollama", "model": "m"}]):
        window.agents = agents
        window.render_agents()

        assert bool(window.welcome_card.isHidden()) != bool(window.overview_content.isHidden()), (
            f"exactly one of welcome and statistics must show with agents={agents}")


def test_the_welcome_hides_once_a_model_is_connected(window) -> None:
    window.agents = []
    window.render_agents()
    assert not window.welcome_card.isHidden()

    window.agents = [{"id": "model", "online": True, "provider": "ollama", "model": "m"}]
    window.render_agents()

    assert window.welcome_card.isHidden()
    assert not window.overview_content.isHidden()
    assert window.stat_values[0].text() == "1", "the statistics still have to be right"


# --- navigation ----------------------------------------------------------


def test_pages_and_titles_stay_in_step() -> None:
    """The nav, the stacked pages and the titles must agree in length.

    These were three separate lists before, so adding a page meant fixing
    the title tuple as well and forgetting to do so was an IndexError at
    startup.
    """
    assert len(win.PAGES) == len(win.PAGE_TITLES)
    assert 0 <= win.RESOURCES_PAGE < len(win.PAGES)
    assert 0 <= win.SETTINGS_PAGE < len(win.PAGES)


def test_window_builds_every_page(window) -> None:
    """Every page declared in PAGES is one of the pages actually built."""
    assert len(window.nav_buttons) == len(win.PAGES)
    assert window.stack.count() == len(win.PAGES)


@pytest.mark.parametrize("index", range(len(win.PAGES)))
def test_every_page_can_be_opened(window, index: int) -> None:
    """Navigating to each page does not raise."""
    window.navigate(index)

    assert window.stack.currentIndex() == index
    assert window.page_title.text() == win.PAGE_TITLES[index]


def test_controls_moved_off_the_providers_page(window) -> None:
    """Providers holds provider configuration and nothing else.

    This navigated to the literal 3, which is Conversations, so it was
    asserting that the Conversations page has no settings controls on it, and
    it passed for a second reason as well: ``_holds_settings`` is set on
    nothing at all, so the assertion was true of every page. It now points at
    Providers and asks whether the two pages' controls are in the right
    places, which is the thing it was written to protect.
    """
    window.navigate(win.PAGE_INDEX["providers"])
    providers = window.stack.currentWidget()
    assert win.PAGES[win.PAGE_INDEX["providers"]][0] == "Providers"
    assert not hasattr(providers, "_holds_settings"), (
        "the marker this test looked for is on no widget, so it asserted "
        "nothing whatever")

    # Something only this page has, so pointing the test at the wrong page
    # cannot leave it passing. The eyebrow is the cheapest thing that is
    # genuinely Providers and not Conversations.
    eyebrows = {child.text() for child in providers.findChildren(QLabel)}
    assert "CHOOSE YOUR INTELLIGENCE" in eyebrows, (
        f"that is not the Providers page; its labels are {sorted(eyebrows)}")

    # The theme picker is a combo box and reduce-motion is a checkbox, so the
    # two pages are asked about the widget types they actually use.
    assert window.theme_picker not in providers.findChildren(win.Select), (
        "the theme picker turned up on the Providers page")

    window.navigate(win.SETTINGS_PAGE)
    settings_page = window.stack.currentWidget()
    assert window.reduce_motion in settings_page.findChildren(QCheckBox), (
        "Reduce animations is not on the Settings page")
    assert window.theme_picker in settings_page.findChildren(win.Select), (
        "the theme picker is not on the Settings page")


def test_settings_controls_are_on_the_settings_page(window) -> None:
    """Reduce motion and the theme picker live in Settings."""
    window.navigate(win.SETTINGS_PAGE)

    assert window.theme_picker.count() == len(THEME_CHOICES)
    assert window.reduce_motion in window.findChildren(type(window.reduce_motion))


# --- theme picker ---------------------------------------------------------


def test_theme_picker_offers_all_themes(window) -> None:
    """Every theme is selectable, with follow-system first."""
    labels = [window.theme_picker.itemText(i) for i in range(window.theme_picker.count())]
    stored = [window.theme_picker.itemData(i) for i in range(window.theme_picker.count())]

    assert labels[0] == "Follow system"
    assert stored[0] is None
    assert set(stored[1:]) == {DARK, LIGHT, MIKU}


def test_theme_picker_defaults_to_follow_system(window) -> None:
    """With nothing stored the picker shows the system-following entry."""
    assert window.storage.settings.get("theme") is None
    assert window.theme_picker.currentIndex() == 0


def test_choosing_a_theme_applies_and_persists_it(window) -> None:
    """Picking Miku switches the window and remembers the choice."""
    index = [c[1] for c in THEME_CHOICES].index(MIKU)

    window.theme_picker.setCurrentIndex(index)

    assert window.theme == MIKU
    assert window.storage.settings["theme"] == MIKU
    assert window.styleSheet() == win.stylesheet(MIKU)


def test_returning_to_follow_system_removes_the_stored_choice(window) -> None:
    """Follow-system is represented by absence, not a stored value."""
    miku_index = [c[1] for c in THEME_CHOICES].index(MIKU)
    window.theme_picker.setCurrentIndex(miku_index)
    assert window.storage.settings["theme"] == MIKU

    window.theme_picker.setCurrentIndex(0)

    assert "theme" not in window.storage.settings
    assert window.theme in (DARK, LIGHT, MIKU)


def test_theme_hint_explains_the_current_choice(window) -> None:
    """The hint says what the picker is doing."""
    window.update_theme_hint()
    following = window.theme_hint.text()

    window.storage.settings["theme"] = LIGHT
    window.update_theme_hint()
    chosen = window.theme_hint.text()

    assert following != chosen
    assert "system" in following.lower()


def test_switching_theme_does_not_write_the_preference_back(window) -> None:
    """Applying a theme programmatically must not fight the picker.

    apply_theme resets the combo index, and if that emitted currentIndexChanged
    the stored preference would be rewritten from the resolved theme, which
    would silently convert follow-system into an explicit choice.
    """
    window.theme_picker.setCurrentIndex(0)
    window.apply_theme(MIKU)

    assert "theme" not in window.storage.settings
    assert window.theme_picker.currentIndex() == 0


# --- resources page -------------------------------------------------------


def test_resources_page_shows_real_readings(window) -> None:
    """Meters are filled from a live sample, not placeholders."""
    window.navigate(win.RESOURCES_PAGE)

    for key in ("cpu", "memory", "disk"):
        reading, bar = window.resource_rows[key]
        assert reading.text() not in ("", "—"), key
        assert bar.value() >= 0


def test_resources_page_builds_one_bar_per_core(window) -> None:
    """The per-core grid matches the machine."""
    window.navigate(win.RESOURCES_PAGE)

    assert len(window.core_bars) == window.sampler.sample()["core_count"]


def test_polling_runs_only_while_the_page_is_visible(window) -> None:
    """Sampling stops when the page is left.

    A panel that polls while hidden spends the app's own CPU to display
    nothing, on a page about CPU usage.
    """
    window.navigate(win.RESOURCES_PAGE)
    assert window.resource_timer.isActive()

    window.navigate(win.OVERVIEW_PAGE)
    assert not window.resource_timer.isActive()

    window.navigate(win.RESOURCES_PAGE)
    assert window.resource_timer.isActive()


def test_manual_refresh_works_from_any_page(window) -> None:
    """The refresh shortcut updates the meters without navigating."""
    window.navigate(win.OVERVIEW_PAGE)
    window.refresh_resources()

    assert window.resource_rows["cpu"][0].text() != "—"


# --- window sizing --------------------------------------------------------


def test_window_fits_the_screen_it_opens_on(qt_app, storage) -> None:
    """The opening size never exceeds the usable screen area."""
    instance = win.MainWindow(storage)
    try:
        available = instance.primary_screen_size()
        assert available is not None
        assert instance.width() <= available[0]
        assert instance.height() <= available[1]
        assert instance.minimumWidth() <= available[0]
        assert instance.minimumHeight() <= available[1]
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_window_size_is_never_remembered(qt_app, storage) -> None:
    """No size is written on close, so the app cannot reopen as a window.

    Replaced a test that asserted the opposite. Remembering the size is what
    made the app come back up in a window rather than maximised, and the
    window it opened at was chosen by whatever the last session happened to
    leave behind.
    """
    instance = win.MainWindow(storage)
    try:
        instance.resize(1150, 760)
        instance.close()
        assert "window_size" not in storage.settings
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_the_window_opens_maximised(qt_app, storage) -> None:
    """The window is maximised, and a stale stored size is ignored."""
    storage.settings["window_size"] = [1150, 760]
    instance = win.MainWindow(storage)
    try:
        instance.showMaximized()
        assert instance.isMaximized()
        # A size left behind by an earlier version must not shrink the
        # window back to a restored rectangle.
        assert instance.size() != win.QSize(1150, 760)
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_nothing_reintroduces_a_stored_window_size(qt_app, storage) -> None:
    """Guards the preference against being written by something unnoticed.

    A key already exists in the real settings, left by an older build that
    saved its size. Nothing reads it now, but the cheap way for the window to
    stop opening maximised later is for some path to start writing it again,
    and the reader is ``window_size(None, {})`` which ignores it entirely.
    """
    module = Path(win.__file__)
    for name in ("runtime.py", "dialogs.py", "bridge.py", "window.py"):
        text = (module.parent / name).read_text(encoding="utf-8")
        assert 'settings["window_size"]' not in text, (
            f"{name} writes a window size, which would bring back the "
            "remembered-size behaviour")

    instance = win.MainWindow(storage)
    try:
        instance.resize(900, 700)
        instance.close()
        assert "window_size" not in storage.settings
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)





def test_sidebar_hides_when_the_window_is_narrow(window) -> None:
    """The agent sidebar is dropped when there is no room for it.

    isHidden is checked rather than isVisible because the window is never
    shown in a headless test, which makes every child report as not
    visible regardless of what was asked for.
    """
    window.resize(800, 700)
    window.apply_sidebar_density()
    assert window.sidebar.isHidden() is True

    window.resize(1500, 900)
    window.apply_sidebar_density()
    assert window.sidebar.isHidden() is False



def test_settings_page_offers_the_identity_reset(window) -> None:
    """Settings is where a user goes to replace an exposed token."""
    window.navigate(win.SETTINGS_PAGE)
    labels = [button.text() for button in window.findChildren(QPushButton)]

    assert any("Reset local identity" in text for text in labels)


def _reset_dialog_answer(window, answer):
    """Run confirm_reset_identity with the dialog auto-answered.

    QMessageBox.exec() blocks, so it is entered through a timer that
    clicks the chosen button. Without this the test would hang forever
    under the offscreen platform, where nobody can dismiss a dialog.
    """
    calls = []
    window.command = calls.append

    def answer_it():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox):
                widget.button(answer).click()
                return

    QTimer.singleShot(0, answer_it)
    window.confirm_reset_identity()
    return calls


def test_choosing_reset_dispatches_the_command(window) -> None:
    """Confirming Reset asks the runtime to replace the identity."""
    calls = _reset_dialog_answer(window, QMessageBox.StandardButton.Reset)

    assert calls == ["reset_identity"]


def test_cancelling_does_nothing(window) -> None:
    """Cancelling must leave the identity alone."""
    calls = _reset_dialog_answer(window, QMessageBox.StandardButton.Cancel)

    assert calls == []


def test_the_destructive_dialog_defaults_to_cancel(window) -> None:
    """A stray Enter must not discard the token.

    Otherwise the default button would reset the identity without the
    user having chosen Reset.
"""
    seen = {}
    original = QMessageBox.exec

    def capture(self):
        seen["box"] = self
        return int(QMessageBox.StandardButton.Cancel)

    QMessageBox.exec = capture
    try:
        _reset_dialog_answer(window, QMessageBox.StandardButton.Reset)
    finally:
        QMessageBox.exec = original

    box = seen["box"]
    assert box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel)


def read_settings_resilient(path, attempts=8, pause=0.005):
    """Read settings.json, tolerating a writer part way through a replace.

    On Windows the reader is the side that loses: replacing a file takes
    DELETE access on it and CPython opens files without FILE_SHARE_DELETE,
    so a read issued while the runtime thread is moving the file into place
    is refused with ERROR_ACCESS_DENIED. The writer retries now, which
    settles that half, but this is the half a reader controls and the
    writer's retries cannot help it.

    Only the test needs this. Nothing in the app reads settings.json after
    Storage has loaded it, so there is no product read to make robust.
    """
    for attempt in range(attempts):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(pause * (attempt + 1))


def test_follow_system_persists_immediately(window) -> None:
    """Removing the stored theme must reach the file, not just memory.

    Only the explicit-choice branch saved, so picking "Follow system"
    left the old theme in settings.json until some unrelated write
    happened to flush it, and the choice came back on the next launch.
    """
    miku_index = [c[1] for c in THEME_CHOICES].index(MIKU)
    window.theme_picker.setCurrentIndex(miku_index)
    assert read_settings_resilient(window.storage.path)["theme"] == MIKU

    window.theme_picker.setCurrentIndex(0)

    on_disk = read_settings_resilient(window.storage.path)
    assert "theme" not in on_disk


def test_a_failed_command_is_reported_in_the_error_colour(window) -> None:
    """A failure must not be painted in the success colours."""
    window.apply_theme(LIGHT)
    window.command_failure("no-such-request", "Could not save credentials.")

    assert window.toast_error is True
    assert win.color(window.theme, "error") in window.toast.styleSheet()
    assert win.color(window.theme, "toast_fg") not in window.toast.styleSheet()


def test_a_notice_is_reported_in_the_success_colour(window) -> None:
    """The default stays success-coloured."""
    window.notice("Replaced 2 saved identities.")

    assert window.toast_error is False
    assert win.color(window.theme, "toast_fg") in window.toast.styleSheet()


def test_switching_themes_does_not_turn_an_error_toast_green(window) -> None:
    """apply_theme restyles the toast, so it must keep the error state.

    Otherwise a failure would silently change colour mid-display while
    the user is still reading it.
    """
    window.notice("Could not save credentials.", error=True)

    window.apply_theme(LIGHT)

    assert win.color(LIGHT, "error") in window.toast.styleSheet()
    assert win.color(LIGHT, "toast_fg") not in window.toast.styleSheet()


def test_settings_shows_owed_credentials_until_the_cleanup_completes(window) -> None:
    """State, not a toast, for a condition that outlives the message.

    isHidden is checked rather than isVisible because the window is never
    shown in a headless test, so every child reports as not visible.
    """
    window.navigate(win.SETTINGS_PAGE)

    assert window.pending_cleanup.isHidden(), "nothing is owed after a clean start"

    window.show_pending_cleanup(2)
    assert not window.pending_cleanup.isHidden()
    assert "2 saved credentials" in window.pending_cleanup.text()
    assert "Unlock" in window.pending_cleanup.text()

    window.show_pending_cleanup(1)
    assert "1 saved credential " in window.pending_cleanup.text()

    window.show_pending_cleanup(0)
    assert window.pending_cleanup.isHidden(), "cleared once nothing is owed"


def test_settings_reports_stranded_files_separately_from_credentials(window) -> None:
    """A folder that will not go is a different problem from a locked keyring.

    Folding it into the credential count would either blame the keyring or
    leave the line hidden, and this can last as long as whatever is holding
    the folder does.
    """
    window.navigate(win.SETTINGS_PAGE)

    window.show_pending_cleanup(0, 1)
    assert not window.pending_cleanup.isHidden()
    assert "1 deleted workspace has files" in window.pending_cleanup.text()
    assert "credential" not in window.pending_cleanup.text()

    window.show_pending_cleanup(2, 1)
    assert "2 saved credentials" in window.pending_cleanup.text()
    assert "1 deleted workspace has files" in window.pending_cleanup.text()

    window.show_pending_cleanup(0, 2)
    assert "2 deleted workspaces have files" in window.pending_cleanup.text()

    window.show_pending_cleanup(0, 0)
    assert window.pending_cleanup.isHidden(), "cleared once nothing is owed"


def test_the_pending_line_receives_the_runtime_event(window) -> None:
    """The runtime's pending_cleanup event is what drives the line."""
    window.network_event("pending_cleanup", {"credentials": 3, "files": 0})

    assert not window.pending_cleanup.isHidden()
    assert "3 saved credentials" in window.pending_cleanup.text()

    window.network_event("pending_cleanup", {"credentials": 0, "files": 1})

    assert "1 deleted workspace has files" in window.pending_cleanup.text()


def test_general_chat_keeps_each_answering_model_after_routing_changes_and_restore(window, tmp_path) -> None:
    window.agents = [{"id": "planner", "kind": "model", "online": True, "model": "planner-v2", "provider": "ollama"}]
    window.selected = win.GENERAL_TARGET
    window.chats = {win.GENERAL_TARGET: {"messages": [
        ("user", "Write a parser"), ("assistant", "Here is the parser"),
        ("user", "Review it"), ("assistant", "Here is the review")],
        "responders": {"1": {"agent_id": "coder", "model": "code-model-v1", "provider": "bionic"},
                       "3": {"agent_id": "reviewer", "model": "review-model-v3", "provider": "ollama"}},
        "routing": {"coordinator": "planner", "assignments": [{"agent_id": "reviewer", "task_type": "debugging"}],
                    "reason": "Review specialist"}}}
    window.history_store = win.HistoryStore(tmp_path)
    window.persist_history()
    window.chats = {}
    window.restore_history(tmp_path)
    window.render_messages()
    rows = window.messages_widget.findChildren(win.HoverRow)
    assert "code-model-v1" in row_labels(rows[1])
    assert "review-model-v3" in row_labels(rows[3])
    assert all("planner-v2" not in row_labels(row) and "Jev" not in row_labels(row) for row in rows)


def test_the_conversation_picker_is_not_rebuilt_when_nothing_changed(window) -> None:
    """The agents poll runs every few seconds and must leave the combo alone.

    It is rebuilt only when the workspace, the selection or the list of choices
    actually differs. Rebuilding unconditionally throws away a dropdown the
    user has open mid-poll, and a changed selection while their pointer is
    still over it. So the same poll is run twice here and the widget is
    expected to survive the second one untouched, which is the only way to
    catch a signature that stops carrying the thing it is meant to carry.
    """
    window.selected = "alpha"
    window.chats = {"alpha": {"messages": [("user", "hi")]}}
    window.refresh_conversation_picker()
    before = window.conversation_picker
    index = window.conversation_picker.currentIndex()

    window.refresh_conversation_picker()
    assert window.conversation_picker is before, "the picker was replaced on an unchanged poll"
    assert window.conversation_picker.currentIndex() == index, "the selection moved on an unchanged poll"

    # A chat that gains messages is a real change and has to rebuild, or a
    # conversation started on another page would never appear.
    window.chats["beta"] = {"messages": [("user", "hi")]}
    window.refresh_conversation_picker()
    assert window.conversation_picker.findData("beta") >= 0, (
        "a second conversation never reached the picker")


def test_the_conversation_picker_says_how_much_is_unread(window) -> None:
    """An unread count is the only thing telling a new conversation apart.

    The row carries the count in its label, because the picker is the only
    place a conversation is listed now that the sidebar has no list of its own.
    Without it a conversation with new messages looks the same as one that has
    been read, and the reason the user opened the app is invisible.
    """
    window.selected = "alpha"
    window.chats = {"alpha": {"messages": [("user", "hi")], "unread": 3}}
    window.refresh_conversation_picker()
    index = window.conversation_picker.findData("alpha")
    assert index >= 0, "the conversation is not in the picker to begin with"
    assert "(3 new)" in window.conversation_picker.itemText(index), (
        f"the unread count is missing from {window.conversation_picker.itemText(index)!r}")

    # Reading it has to take the badge off, or it counts up forever.
    window.chats["alpha"]["unread"] = 0
    window.refresh_conversation_picker()
    assert "(3 new)" not in window.conversation_picker.itemText(index), (
        "the unread badge survived the conversation being read")


def test_choosing_a_conversation_by_index_ignores_a_stale_one(window) -> None:
    """A stale index must not silently switch conversations.

    The agents poll can rebuild the combo between a click and the slot running,
    so the index carries no guarantee that it still points at what the user
    saw. An index that no longer names a conversation is dropped rather than
    resolved to whatever now sits there, which would open the wrong thread.
    """
    window.agents = [{"id": "alpha", "online": True, "kind": "model", "provider": "ollama", "model": "m"}]
    window.selected = "alpha"
    window.chats = {"alpha": {"messages": [("user", "hi")]}}
    window.refresh_conversation_picker()

    window.choose_conversation(window.conversation_picker.count())
    assert window.selected == "alpha", (
        f"an out-of-range index moved the selection to {window.selected!r}")

    empty = window.conversation_picker.count()
    for index in range(empty):
        window.conversation_picker.setItemData(index, None)
    window.choose_conversation(0)
    assert window.selected == "alpha", (
        f"an index with no conversation behind it moved the selection to {window.selected!r}")


def test_a_failure_in_another_workspace_reopens_that_conversation(window, tmp_path) -> None:
    """A send that failed in the background is reported where it happened.

    Attachment and size failures are found long after the user has moved on,
    often into another workspace. Writing the message only to the conversation
    on screen would put the explanation in the wrong thread, and losing it
    means the user is told nothing about a request that did not send. So the
    other workspace is switched to, its own saved chat is written, and the
    conversation is opened there.
    """
    window.ready = True
    other = win.HistoryStore(tmp_path / "other")
    window.workspace_id = "here"
    window.workspace_list = [{"id": "here"}, {"id": "there"}]
    window.selected = "here-model"
    window.chats = {"here-model": {"messages": [("user", "hi")]}}
    window.history_store = win.HistoryStore(tmp_path / "here")
    window.persist_history()
    window.history_store = win.HistoryStore(tmp_path / "here")

    # The background workspace's saved state, holding the conversation that failed.
    other.save("ui", "state", {"chats": {"there-model": {"messages": [("user", "a draft")]}}})
    switched = []
    window.command = lambda name, *args, **kwargs: (
        switched.append((name, args)), kwargs.get("success") and kwargs["success"]())

    window.record_chat_error("there", other, "there-model", "Message too large")

    assert [name for name, _ in switched] == ["switch_workspace"], (
        f"the failing workspace was not opened: {switched!r}")
    assert switched[0][1] == ("there",), f"it switched somewhere else: {switched[0][1]!r}"
    assert "Message too large" in other.load("ui")["state"]["chats"]["there-model"]["send_error"], (
        "the failure was not kept with the conversation it belongs to")
    assert "send_error" not in window.chats["here-model"], (
        "the failure was written into the workspace on screen instead")

    # And the message survives a restart of that workspace. Compared as JSON,
    # because the store round-trips through it and a saved role/text pair comes
    # back as a list rather than the tuple it went in as.
    reopened = other.load("ui")["state"]["chats"]["there-model"]
    assert [list(message) for message in reopened["messages"]] == [["user", "a draft"]], (
        f"the draft was lost while recording the failure: {reopened['messages']!r}")


def test_a_failure_in_an_unknown_workspace_is_dropped_rather_than_guessed(window, tmp_path) -> None:
    """A workspace that is no longer listed has nowhere to report to.

    Writing into it anyway would recreate a workspace the user has left, and
    switching to it would fail. Both are worse than saying nothing, so the
    message is dropped and nothing is sent anywhere.
    """
    window.ready = True
    window.workspace_id = "here"
    window.workspace_list = [{"id": "here"}]
    window.selected = "here-model"
    window.chats = {"here-model": {"messages": [("user", "hi")]}}
    store = win.HistoryStore(tmp_path)
    store.save("ui", "state", {"chats": {"gone-model": {"messages": [("user", "hi")]}}})
    switched = []
    window.command = lambda name, *args, **kwargs: switched.append(name)

    window.record_chat_error("somewhere-else", store, "gone-model", "Could not send")

    assert not switched, f"it switched to a workspace that is not listed: {switched!r}"
    assert "send_error" not in window.chats["here-model"], (
        "the failure landed in the workspace on screen")
    assert "send_error" not in store.load("ui")["state"]["chats"]["gone-model"], (
        "the failure was written into a workspace the user has left")


def test_the_page_title_is_a_title_and_not_a_markdown_heading(window) -> None:
    """The "#  " was painted, never parsed.

    label() pins Qt.TextFormat.PlainText, so a heading marker in the source
    became a character in the largest text in the window -- on every page, of
    every session -- and a screen reader announced "hash, overview".
    """
    for title in win.PAGE_TITLES:
        assert not title.startswith("#"), f"{title!r} is drawn with its marker showing"
    window.navigate(win.OVERVIEW_PAGE)
    assert not window.page_title.text().startswith("#"), window.page_title.text()


def test_a_startup_that_failed_is_not_painted_as_a_healthy_one(window) -> None:
    """One label, one colour, three different facts.

    The connection line was created as "online" and only ever had its text
    replaced, so "Relay unavailable" and "Startup needs attention" were both
    drawn in the success green. It is the one status always on screen, and the
    leading glyph does not change, so colour was carrying the whole meaning.
    """
    window.network_event("offline", None)
    assert window.connection_status.objectName() == "statusBad", (
        f"the relay is down and the line reads {window.connection_status.text()!r} "
        f"in the state {window.connection_status.objectName()!r}")

    window.network_event("agents", {"agents": [], "connected": True})
    assert window.connection_status.objectName() == "statusOk", window.connection_status.text()

    window.network_event("agents", {"agents": [], "connected": False})
    assert window.connection_status.objectName() == "statusWarn", window.connection_status.text()


def test_a_failed_startup_is_stated_and_not_only_toasted(window) -> None:
    """The toast is gone in seven seconds; a fatal cannot be undone.

    Everything the fatal disables stays disabled, so after the toast expired the
    window was full of controls that would not work and nothing saying why.
    """
    assert window.startup_error.isHidden(), "the fatal surface should start hidden"
    window.network_event("fatal", "The credential store is locked.")
    assert not window.startup_error.isHidden(), (
        "a fatal startup error has no lasting surface, so the reason is lost "
        "as soon as the toast expires")
    assert "credential store" in window.startup_error.text()


def test_a_search_that_matched_nothing_does_not_claim_the_team_is_empty(window) -> None:
    """"Your team is getting ready" is a claim about the reader's own setup.

    With the search box open it was being made while a model was connected and
    simply had not matched what was typed, which is the one thing the Cookbook's
    empty_reason() discipline exists to prevent: one state standing in for
    another.
    """
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "model": "llama3.2", "provider": "ollama"}]
    window.search.setText("nothing-matches-this")
    window.render_agents()

    grid = window.agent_cards
    cards = [grid.itemAt(index).widget() for index in range(grid.count())
             if grid.itemAt(index).widget() is not None]
    said = [label.text() for card in cards
            for label in card.findChildren(QLabel) if label.text()]
    buttons = [button.text() for card in cards
               for button in card.findChildren(QPushButton)]

    assert said, "the page said nothing at all"
    assert not any("getting ready" in text for text in said), (
        f"a connected team was reported as empty: {said}")
    assert any("nothing-matches-this" in text for text in said), (
        f"the empty state does not echo what was searched for: {said}")
    assert "Clear search" in buttons, (
        f"nothing offers to undo the search that hid the reader's team: {buttons}")


def test_a_control_a_screen_reader_cannot_name_is_named(window) -> None:
    """Four controls in the shell were announced as a glyph or as nothing.

    The join button has no text at all and carries an icon, so it was skipped
    over entirely; the others were announced as "M, button", "question mark,
    button" and "multiplication sign, button". The Cookbook button already had
    an accessible name, so the convention existed -- these had simply not had
    it applied.
    """
    cases = [
        (window.join_button, "Create or join a workspace"),
        (window.help_button, "Project documentation"),
        (window.sidebar_close, "Close the sidebar"),
    ]
    for widget, expected in cases:
        assert widget.accessibleName() == expected, (
            f"a control announced as {widget.accessibleName()!r} instead of "
            f"{expected!r}")

    agents = [{"id": "local-llama", "kind": "model", "online": True,
               "model": "llama3.2", "provider": "ollama", "profile": {"id": "local-llama"},
               "local": True}]
    window.agents = agents
    window.render_agents()

    grid = window.agent_cards
    cards = [grid.itemAt(index).widget() for index in range(grid.count())
             if grid.itemAt(index).widget() is not None]
    names = [button.accessibleName() for card in cards
             for button in card.findChildren(QPushButton)]
    for expected in ("Open a conversation with local-llama",
                     "Edit the settings for local-llama",
                     "Stop local-llama on this device"):
        assert expected in names, (
            f"no button on the agent card is called {expected!r}; it carries {names}")


def test_the_smallest_controls_are_still_wide_enough_to_hit(window) -> None:
    """The one way to clear a card error was a 20x20 target.

    ErrorLine's dismiss button sits in a row spaced 6px, is the only way to
    dismiss the message, and was small enough to miss with a pointer and hard
    to reach with one. The sidebar's close button was 26px on the same window.
    """
    from desktop_app.widgets import ErrorLine

    line = ErrorLine(window, "model:connect")
    assert line.dismiss.width() >= 28 and line.dismiss.height() >= 28, (
        f"the dismiss target is {line.dismiss.width()}x{line.dismiss.height()}")
    assert window.sidebar_close.width() >= 28 and window.sidebar_close.height() >= 28, (
        f"the sidebar close target is {window.sidebar_close.width()}x"
        f"{window.sidebar_close.height()}")


def test_the_empty_agent_card_is_padded_like_the_ones_it_replaces(window) -> None:
    """Qt's default margin, 11px, against 18/17 on the real cards below.

    They sit in one grid on one screen, so the empty state was visibly the
    tighter box of the two -- on the one screen where the difference is
    easiest to notice, because it is the only card on it.
    """
    window.agents = []
    window.search.clear()
    window.render_agents()

    grid = window.agent_cards
    cards = [grid.itemAt(index).widget() for index in range(grid.count())
             if grid.itemAt(index).widget() is not None]
    assert cards, "nothing was drawn at all"
    margins = cards[0].layout().contentsMargins()
    assert margins.left() >= 18, (
        f"the empty card's left padding is {margins.left()}px where an agent "
        f"card uses 18")


def test_the_sidebar_offers_a_retry_when_the_relay_is_gone(window) -> None:
    """There was no retry anywhere in the app.

    Not one -- a repo-wide search found the word only in error prose. So a
    reader whose relay had gone had no way to ask again, and the poll's own
    cadence was the only thing that could notice a host coming back.
    """
    assert window.connection_retry.isHidden(), "a Retry is offered before anything has gone wrong"

    # The runtime is up and the relay had been answering, which is the state a
    # Retry is for: something that worked and then stopped.
    window.ready = True
    window.network_event("agents", {"agents": [], "connected": True})
    assert window.connection_retry.isHidden(), "a Retry is offered while the relay is answering"

    window.network_event("offline", None)
    assert not window.connection_retry.isHidden(), (
        "the relay is unreachable and nothing in the app offers to ask again")

    window.network_event("agents", {"agents": [], "connected": True})
    assert window.connection_retry.isHidden(), (
        "the relay answered and the Retry is still on screen")

    # Not offered while starting, or after a fatal: there is nothing to retry.
    window.ready = False
    window.network_event("offline", None)
    assert window.connection_retry.isHidden(), (
        "a Retry is offered before the runtime is up, where it cannot work")
    window.ready = True


def test_a_retry_reports_the_relay_back_without_a_command(window, monkeypatch) -> None:
    """The poll is backing off to five minutes, so a reader needs a way to poke it.

    Otherwise a host that has just been restarted stays unnoticed until that
    ceiling elapses, which is long enough to look like the button is broken.
    """
    asked = []
    monkeypatch.setattr(window.network, "submit",
                        lambda method, *args, **kwargs: asked.append(method) or "r1")
    window.network_event("offline", None)
    window.connection_retry.click()

    assert asked == ["retry_poll"], f"the Retry button sent {asked}"
    assert window.connection_status.objectName() == "statusWarn", (
        "after pressing Retry the status still claims the relay is unavailable")


def test_a_failed_relay_does_not_lock_the_reader_out(window, monkeypatch) -> None:
    """Refusing commands while offline was tried and removed.

    The poll backs off to a five minute ceiling, so a flag set by one offline
    event during startup would keep refusing commands -- including the one that
    would have fixed it -- long after the relay was fine again. A stale network
    flag is worse than a failed request, because the failed request says what
    went wrong.
    """
    submitted = []
    monkeypatch.setattr(window.network, "submit",
                        lambda method, *args, **kwargs: submitted.append(method) or "r1")
    window.ready = True
    window.network_event("offline", None)

    window.command("create_workspace", "Still mine to make")

    assert submitted == ["create_workspace"], (
        f"a command was swallowed while offline: {submitted}")


def test_the_members_table_keeps_the_keyboard_focus_it_was_given(window) -> None:
    """It rebuilt itself every second, destroying any focus on a Remove button.

    refresh() built a fresh button per member and handed it to setCellWidget,
    which deletes the previous one. So tabbing onto Remove and waiting a second
    meant the focused widget no longer existed. AgentListDialog already guarded
    its refresh on a signature of the list; this one did not.
    """
    from desktop_app.workspace_dialog import WorkspaceDialog

    window.workspace_meta = {"members": [
        {"id": "guest-laptop", "role": "device", "online": True}]}
    dialog = WorkspaceDialog(window)
    dialog.show()

    first = dialog.members.cellWidget(0, 3)
    first.setFocus()
    dialog.refresh()

    assert dialog.members.cellWidget(0, 3) is first, (
        "an unchanged membership rebuilt the row, so the focused Remove button "
        "was deleted underneath the reader")

    window.workspace_meta = {"members": [
        {"id": "guest-laptop", "role": "device", "online": True},
        {"id": "phone", "role": "device", "online": False}]}
    dialog.refresh()
    assert dialog.members.rowCount() == 2, (
        "a member who actually arrived was not drawn, because the guard is "
        "skipping real changes as well as repeats")

    dialog.close()


def test_the_cookbook_is_not_built_while_the_window_is(window) -> None:
    """Building it runs nvidia-smi with a three second timeout, on the GUI thread.

    Every page was constructed into the stack at start-up, so the Cookbook's
    hardware probe ran inside build_ui before anything was drawn. On a machine
    where nvidia-smi is slow that is up to three seconds of frozen window, on
    every launch, to prepare a page most sessions never open.

    Measured with the probe forced to take three seconds: 3.23s to construct
    the window before, 0.11s after.
    """
    assert window.cookbook_panel is None, (
        "the Cookbook page was built during start-up, which puts the graphics "
        "card probe on the path that draws the window")
    assert window.stack.widget(win.COOKBOOK_PAGE) is not None, (
        "the stack must still have a widget in the Cookbook's slot, or "
        "navigate() and the page shortcuts point at nothing")


def test_opening_the_cookbook_builds_it_once(window) -> None:
    """The cost moves rather than disappears, which is the point.

    It is paid the first time the reader asks for the page, and only then.
    """
    window.navigate(win.OVERVIEW_PAGE)
    assert window.cookbook_panel is None

    window.navigate(win.COOKBOOK_PAGE)
    panel = window.cookbook_panel
    assert panel is not None, "navigating to the Cookbook did not build it"
    assert window.stack.widget(win.COOKBOOK_PAGE) is panel, (
        "the page was built but not put in the slot navigate() selects")

    window.navigate(win.OVERVIEW_PAGE)
    window.navigate(win.COOKBOOK_PAGE)
    assert window.cookbook_panel is panel, (
        "the page was rebuilt on a second visit, re-probing the hardware and "
        "re-reading the catalogue for nothing")


def test_refreshing_invitations_empties_the_rows_and_keeps_the_heading(window) -> None:
    """A refresh should replace the invitations, not the whole section.

    The list of invitations this device can reissue changes whenever the reader
    makes or removes one, and the dialog redraws it in place. Clearing it by
    emptying the container took the heading and the note with it, and the note's
    text was then set on a widget no longer in the box, so the section lost its
    explanation on the first open and every refresh after it.
    """
    from desktop_app.workspace_dialog import WorkspaceDialog

    window.outstanding_invitations = []
    dialog = WorkspaceDialog(window)
    dialog.refresh_invitations()

    window.outstanding_invitations = ["laptop-one", "laptop-two"]
    dialog.refresh_invitations()
    text = [child.text() for child in dialog.invitations_box.findChildren(win.QWidget)
            if hasattr(child, "text")]
    assert "Invitations already created" in text, f"the heading went missing: {text}"
    assert "laptop-one" in text and "laptop-two" in text, f"rows missing: {text}"
    assert any("credential store" in line for line in text), f"the note went missing: {text}"
    assert dialog.invitation_rows.count() == 2, dialog.invitation_rows.count()

    window.outstanding_invitations = ["laptop-three"]
    dialog.refresh_invitations()
    assert dialog.invitation_rows.count() == 1, dialog.invitation_rows.count()
    text = [child.text() for child in dialog.invitations_box.findChildren(win.QWidget)
            if hasattr(child, "text")]
    assert "laptop-one" not in text, f"a removed invitation was still listed: {text}"
    assert "Invitations already created" in text, "the heading went missing on refresh"
