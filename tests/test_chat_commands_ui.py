"""Exercise command dispatch, recipient isolation, and real composer keystrokes."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_app.storage import Storage
from desktop_app.widgets import Composer
from desktop_app.window import MainWindow
from network_a2a.orchestration import GENERAL_TARGET
from tests.test_desktop_runtime import MemoryVault


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    # These tests control the UI snapshot and callbacks; runtime integration
    # and ownership are exercised against live relays in the runtime tests.
    monkeypatch.setattr("desktop_app.bridge.NetworkThread.start", lambda self: None)
    instance = MainWindow(Storage(tmp_path, MemoryVault()))
    instance.workspace_id = "original"
    instance.workspace_list = [{"id": "original"}]
    instance.ready = True
    instance.agents = [{"id": "coder", "kind": "model", "model": "test-model", "online": True},
                       {"id": "alice", "kind": "peer", "online": True}]
    instance.workspace_meta = {"coordinator": "coder", "members": [{"id": "alice"}]}
    instance.select_agent(GENERAL_TARGET)
    yield instance
    instance.history_timer.stop()
    instance.message_scroll_timer.stop()
    instance.closing = True
    instance.close()
    instance.deleteLater()
    app.processEvents()


@pytest.fixture
def composer(app):
    widget = Composer()
    widget.set_mentions(["alice", "agent-name"])
    widget.show()
    widget.activateWindow()
    widget.setFocus()
    app.processEvents()
    yield widget
    widget.hide_completion()
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_help_works_without_models_or_network_and_stays_out_of_history(window):
    window.ready = False
    window.agents = []
    window.selected = None
    window.chats.clear()
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("/help")
    assert window.send_button.isEnabled()
    window.send_message()
    assert calls == []
    assert "/set @agent as Head" in window.command_output.toPlainText()
    assert not window.command_panel.isHidden()
    assert window.composer.toPlainText() == ""
    assert all(not chat["messages"] and not chat["history"] for chat in window.chats.values())


def test_commands_use_workspace_dispatch_and_leave_new_drafts_alone(window):
    calls = []
    window.command = lambda *args, **kwargs: calls.append((args, kwargs))
    window.composer.setPlainText("/set @coder as Head")
    window.send_message()
    assert calls[0][0] == ("chat_command", "/set @coder as Head", "original")
    assert not window.send_button.isEnabled()
    assert not window.chats[GENERAL_TARGET]["messages"]
    window.composer.setPlainText("A new draft")
    calls[0][1]["success"]({"text": "Head saved"})
    assert window.composer.toPlainText() == "A new draft"
    assert window.command_output.toPlainText() == "Head saved"
    assert window.send_button.isEnabled()
    window.composer.setPlainText("/status")
    window.send_message()
    calls[1][1]["success"]({"text": "Head: @coder"})
    assert window.composer.toPlainText() == ""


def test_invalid_and_failed_commands_preserve_drafts_without_model_requests(window):
    calls = []
    window.command = lambda *args, **kwargs: calls.append((args, kwargs))
    window.composer.setPlainText("/set @coder as Owner")
    window.send_message()
    assert calls == []
    assert window.composer.toPlainText() == "/set @coder as Owner"
    assert "Use /set" in window.command_output.toPlainText()
    window.composer.setPlainText("/set @coder as Writer")
    window.send_message()
    calls[0][1]["failure"]("Only the workspace owner can assign roles")
    assert "workspace owner" in window.command_output.toPlainText()
    assert window.composer.toPlainText() == "/set @coder as Writer"
    assert not window.chats[GENERAL_TARGET]["history"]


def test_old_command_result_does_not_clear_or_display_in_another_workspace(window):
    calls = []
    window.command = lambda *args, **kwargs: calls.append(kwargs)
    window.composer.setPlainText("/status")
    window.send_message()
    window.workspace_id = "different"
    window.composer.setPlainText("Draft in another workspace")
    calls[0]["success"]({"text": "Private status from original workspace"})
    assert window.command_panel.isHidden()
    assert window.composer.toPlainText() == "Draft in another workspace"


@pytest.mark.parametrize("identity", ["coder", "alice"])
def test_leading_mentions_target_agents_and_users_without_other_chat_context(window, identity):
    window.chats[GENERAL_TARGET]["history"] = [{"role": "user", "content": "Private general context"}]
    calls = []
    window.command = lambda *args, **kwargs: calls.append((args, kwargs))
    window.composer.setPlainText(f"@{identity} Please help")
    window.send_message()
    assert window.selected == identity
    assert calls[0][0] == ("send", identity, {"text": "Please help"})
    assert window.chats[GENERAL_TARGET]["history"][0]["content"] == "Private general context"
    assert window.chats[identity]["messages"] == [("user", "Please help")]
    # Command-shaped replies remain messages, never executable actions.
    calls[0][1]["success"]({"text": "/set @coder as Writer", "provider": "test"})
    assert len(calls) == 1
    assert window.chats[identity]["messages"][-1] == ("assistant", "/set @coder as Writer")


@pytest.mark.parametrize("text,expected", [("Ask @alice to help", "Ask @alice to help"),
                                            ("//help", "/help")])
def test_inline_mentions_and_escaped_slashes_remain_messages(window, text, expected):
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText(text)
    window.send_message()
    assert calls == [("send", GENERAL_TARGET, {"text": expected})]


def test_unknown_mentions_and_recipient_drafts_are_preserved(window):
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("@missing hello")
    window.send_message()
    assert "not in this workspace" in window.command_output.toPlainText()
    assert window.composer.toPlainText() == "@missing hello"
    assert window.selected == GENERAL_TARGET
    attachment = {"name": "draft.pdf", "content": []}
    window.chats["coder"] = {"messages": [], "history": [], "draft_attachments": [attachment]}
    window.composer.setPlainText("@coder hello")
    window.send_message()
    assert "draft attachments" in window.command_output.toPlainText()
    assert window.chats["coder"]["draft_attachments"] == [attachment]
    assert window.selected == GENERAL_TARGET
    assert calls == []


@pytest.mark.parametrize("online", [False, True])
def test_member_only_mentions_preserve_drafts_with_availability_feedback(window, online):
    window.workspace_meta["members"].append({"id": "bob", "online": online})
    attachment = {"name": "draft.pdf", "content": []}
    window.chats[GENERAL_TARGET]["draft_attachments"] = [attachment]
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("@BOB hello")
    assert "bob" in window.composer.mention_names
    window.send_message()
    assert "@bob is not currently available" in window.command_output.toPlainText()
    assert window.composer.toPlainText() == "@BOB hello"
    assert window.selected == GENERAL_TARGET
    assert window.chats[GENERAL_TARGET]["draft_attachments"] == [attachment]
    assert "bob" not in window.chats
    assert calls == []


def test_mentions_check_ambiguity_across_agents_and_members(window):
    window.workspace_meta["members"].append({"id": "Coder", "online": True})
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("@CODER hello")
    window.send_message()
    assert "ambiguous" in window.command_output.toPlainText()
    assert window.composer.toPlainText() == "@CODER hello"
    assert window.selected == GENERAL_TARGET
    assert calls == []


@pytest.mark.parametrize("online,can_start", [(True, False), (False, True), (False, False)])
def test_agent_mentions_keep_agent_availability_over_membership(window, online, can_start):
    window.agents[0].update(online=online, can_start=can_start)
    window.workspace_meta["members"].append({"id": "coder", "online": not online})
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("@coder hello")
    window.send_message()
    if online or can_start:
        assert calls == [("send", "coder", {"text": "hello"})]
        assert window.selected == "coder"
    else:
        assert "@coder is offline" in window.command_output.toPlainText()
        assert window.composer.toPlainText() == "@coder hello"
        assert window.selected == GENERAL_TARGET
        assert calls == []


def test_mention_can_open_a_user_chat_without_any_available_model(window):
    window.agents = [window.agents[1]]
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("@ALICE")
    assert window.send_button.isEnabled()
    window.send_message()
    assert window.selected == "alice"
    assert window.composer.toPlainText() == ""
    assert calls == []


@pytest.mark.parametrize("prefix,key,expected", [
    ("/he", Qt.Key.Key_Tab, "/help "),
    ("/he", Qt.Key.Key_Return, "/help "),
    ("@ag", Qt.Key.Key_Tab, "@agent-name "),
    ("/set @agent-name as H", Qt.Key.Key_Tab, "/set @agent-name as Head "),
])
def test_keyboard_completion_does_not_submit_a_message(composer, app, prefix, key, expected):
    submitted = []
    composer.submitted.connect(lambda: submitted.append(True))
    QTest.keyClicks(composer, prefix)
    app.processEvents()
    assert composer.completer.popup().isVisible()
    QTest.keyClick(composer, key)
    app.processEvents()
    assert composer.toPlainText() == expected
    assert submitted == []
    QTest.keyClick(composer, Qt.Key.Key_Return)
    assert submitted == [True]


@pytest.mark.parametrize("popup_focused", [False, True])
def test_shift_enter_and_escape_during_completion(composer, app, popup_focused):
    submitted = []
    composer.submitted.connect(lambda: submitted.append(True))
    QTest.keyClicks(composer, "/he")
    assert composer.completer.popup().isVisible()
    target = composer.completer.popup() if popup_focused else composer
    QTest.keyClick(target, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert composer.toPlainText() == "/he\n"
    assert submitted == []
    composer.clear()
    QTest.keyClicks(composer, "@al")
    QTest.keyClick(composer, Qt.Key.Key_Escape)
    app.processEvents()
    assert not composer.completer.popup().isVisible()
    assert composer.toPlainText() == "@al"


def test_inline_mention_completion_handles_unicode_and_workspace_changes(composer):
    composer.setPlainText("\U0001f3b5 Ask @ag")
    composer.moveCursor(composer.textCursor().MoveOperation.End)
    composer.insert_completion("@agent-name")
    assert composer.toPlainText() == "\U0001f3b5 Ask @agent-name "
    composer.set_mentions(["new-user", "../invalid", "new-user"])
    assert composer.mention_names == ("new-user",)
    composer.setPlainText("@ag")
    composer.moveCursor(composer.textCursor().MoveOperation.End)
    composer.insert_completion("@agent-name")
    assert composer.toPlainText() == "@ag"
