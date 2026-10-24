"""Exercise workspace commands, in-place model mentions, and composer keystrokes."""
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
from network_a2a.persistence import HistoryStore
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
    instance.history_store = HistoryStore(tmp_path)
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


def test_user_mentions_open_a_direct_chat_without_other_chat_context(window):
    identity = "alice"
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


@pytest.mark.parametrize("current", [GENERAL_TARGET, "reviewer"])
def test_model_mentions_keep_history_replies_and_followups_in_the_current_chat(window, current):
    window.agents.append({"id": "reviewer", "kind": "model", "model": "review-model", "online": True})
    window.select_agent(current)
    chat = window.chats[current]
    history = [{"role": "user", "content": "We are building a parser"},
               {"role": "assistant", "content": "Which language?"}]
    chat["history"] = list(history)
    chat["messages"] = [(message["role"], message["content"]) for message in history]
    chat["routing"] = {"mode": "direct", "reason": "Old routing"}
    calls = []
    window.command = lambda *args, **kwargs: calls.append((args, kwargs))
    for identity, question, answer in (("coder", "Use Python", "Parser written"),
                                       ("reviewer", "Review that parser", "Review complete")):
        window.composer.setPlainText(f"@{identity} {question}")
        window.send_message()
        assert window.selected == current
        assert calls[-1][0] == ("send", identity, {"messages": history + [{"role": "user", "content": question}]})
        assert "routing" not in chat
        calls[-1][1]["success"]({"text": answer})
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        assert chat["history"] == history
        assert chat["messages"][-2:] == [("user", question), ("assistant", answer)]
        assert chat["responders"][str(len(chat["messages"]) - 1)]["agent_id"] == identity
    assert "coder" not in window.chats
    assert set(window.chats) == {GENERAL_TARGET, current}
    window.composer.setPlainText("What should we test next?")
    window.send_message()
    assert calls[-1][0] == ("send", current, {"messages": history + [
        {"role": "user", "content": "What should we test next?"}]})
    calls[-1][1]["success"]({"text": "Test invalid inputs", "provider": "test"})
    saved = window.history_store.load("ui")["state"]
    assert saved["selected"] == current
    assert saved["chats"][current]["history"] == chat["history"]
    assert "coder" not in saved["chats"]


def test_bare_model_mention_keeps_the_current_conversation_and_draft(window):
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("@coder")
    window.send_message()
    assert window.selected == GENERAL_TARGET
    assert window.composer.toPlainText() == "@coder"
    assert "Add a message" in window.command_output.toPlainText()
    assert "coder" not in window.chats
    assert calls == []


@pytest.mark.parametrize("pending", ["pending", "local_pending"])
def test_model_mention_waits_for_the_current_conversation_reply(window, pending):
    chat = window.chats[GENERAL_TARGET]
    chat[pending] = True
    calls = []
    window.command = lambda *args, **kwargs: calls.append(args)
    window.composer.setPlainText("@coder Another request")
    window.send_message()
    assert "Wait for this conversation" in window.command_output.toPlainText()
    assert window.selected == GENERAL_TARGET
    assert window.composer.toPlainText() == "@coder Another request"
    assert calls == []


def test_model_mention_uses_current_attachments_and_leaves_the_models_separate_draft_alone(window):
    attachment = {"name": "notes.pdf", "content": [{"type": "document", "name": "notes.pdf", "text": "Parser notes"}]}
    separate = {"messages": [], "history": [], "pending": True, "draft_attachments": [{"name": "separate.pdf"}]}
    window.chats["coder"] = separate
    window.chats[GENERAL_TARGET]["draft_attachments"] = [attachment]
    calls = []
    window.command = lambda *args, **kwargs: calls.append((args, kwargs))
    window.composer.setPlainText("@coder Explain these notes")
    window.send_message()
    content = [{"type": "text", "text": "Explain these notes"}, *attachment["content"]]
    assert calls[0][0] == ("send", "coder", {"messages": [{"role": "user", "content": content}]})
    assert window.selected == GENERAL_TARGET
    assert separate == {"messages": [], "history": [], "pending": True, "draft_attachments": [{"name": "separate.pdf"}]}
    calls[0][1]["success"]({"text": "Notes explained", "provider": "test"})
    assert window.chats[GENERAL_TARGET]["history"][0]["content"] == content


def test_model_mention_stays_in_a_shared_conversation(window):
    room_id = "conversation-" + "a" * 32
    room = {"id": room_id, "target": GENERAL_TARGET, "title": "Team chat", "owner": window.identity, "members": ["alice"],
            "messages": [], "revision": 0, "pending": False}
    window.conversations[room_id] = room
    window.select_agent(room_id)
    calls = []
    window.command = lambda *args, **kwargs: calls.append((args, kwargs))
    window.composer.setPlainText("@coder Help the team")
    window.send_message()
    assert calls[0][0] == ("send_conversation", room_id, "Help the team", "coder")
    assert window.selected == room_id
    assert room["target"] == GENERAL_TARGET
    assert "coder" not in window.chats
    calls[0][1]["success"]({})
    assert not window.chats[room_id]["local_pending"]


def test_model_mention_reply_stays_in_its_original_chat_when_the_user_navigates(window):
    calls = []
    window.command = lambda *args, **kwargs: calls.append((args, kwargs))
    window.composer.setPlainText("@coder Help me")
    window.send_message()
    window.select_agent("alice")
    calls[0][1]["success"]({"text": "Here is the answer", "provider": "test"})
    assert window.selected == "alice"
    assert window.chats[GENERAL_TARGET]["messages"][-1] == ("assistant", "Here is the answer")
    assert window.chats["alice"]["messages"] == []
    assert "coder" not in window.chats


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
    window.chats["alice"] = {"messages": [], "history": [], "draft_attachments": [attachment]}
    window.composer.setPlainText("@alice hello")
    window.send_message()
    assert "draft attachments" in window.command_output.toPlainText()
    assert window.chats["alice"]["draft_attachments"] == [attachment]
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
        assert window.selected == GENERAL_TARGET
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


def test_the_workspace_commands_panel_closes_with_a_cross(window):
    """A cross, not the word Dismiss.

    Written out beside the heading it read as the only way out of the panel,
    and every other closable surface in the app -- the toast, an error line --
    closes with the same glyph. Asserted on the text so it cannot quietly go
    back to being a word, and clicked so the control is known to be the thing
    that closes the panel rather than something that merely says so.
    """
    dismiss = window.command_dismiss
    assert dismiss.text() == "\u00d7", (
        f"the panel's close control reads {dismiss.text()!r} rather than the "
        "cross every other surface uses")
    # And that the cross fits. Left on the ghost style it inherited 9px/12px of
    # padding meant for a text button, so its size hint came out wider than the
    # box it is fixed at and the glyph was clipped to part of itself -- which is
    # how the toast's cross went missing in the first place.
    assert dismiss.sizeHint().width() <= dismiss.width(), (
        f"the cross needs {dismiss.sizeHint().width()}px inside a "
        f"{dismiss.width()}px box, so it is drawn clipped")

    window.command_panel.show()
    assert window.command_panel.isHidden() is False

    window.command_dismiss.click()
    assert window.command_panel.isHidden() is True, (
        "the cross did not close the panel")


def test_the_commands_cross_follows_the_theme(window):
    """Switching themes must not leave the cross in the old theme's colour.

    The cross carries its own style sheet, so it does not follow the
    application sheet, and apply_theme() restyles the other hand-styled
    widgets for exactly that reason. It was not doing this one, so a cross
    built under the dark theme stayed dark-text on the light page for the rest
    of the session.
    """
    from desktop_app.theme import LIGHT, color

    window.apply_theme("DARK")
    window.apply_theme(LIGHT)

    assert color(LIGHT, "text_nav") in window.command_dismiss.styleSheet(), (
        "the cross still carries the previous theme's colour: "
        f"{window.command_dismiss.styleSheet()!r}")
    assert color("DARK", "text_nav") not in window.command_dismiss.styleSheet(), (
        "the cross carries both themes' colours at once")

