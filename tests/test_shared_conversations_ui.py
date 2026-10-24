"""Exercise native invitation, join, and send controls on two desktop windows."""
import json
import tempfile
import time
import unittest
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from starlette.responses import JSONResponse

from desktop_app.dialogs import InviteDialog
from tests.model_card import connect_model, join_workspace
from desktop_app.markdown import MarkdownMessage
from desktop_app.storage import Storage
from desktop_app.window import CONVERSATIONS_PAGE, GENERAL_TARGET, WORKSPACES_PAGE, MainWindow
from tests.test_desktop_runtime import MemoryVault


class SharedConversationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("Native UI condition timed out")
            time.sleep(.01)
        self.app.processEvents()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = MainWindow(Storage(Path(self.directory.name) / "host", MemoryVault()))
        self.guest = MainWindow(Storage(Path(self.directory.name) / "guest", MemoryVault()))
        self.host.show()
        self.guest.show()
        self.wait(lambda: self.host.ready and self.guest.ready)
        # Startup emits "ready" and then the poller emits "agents" right
        # away, so this window's first agent list arrives as a queued Qt
        # signal. Everything below runs in the same QApplication, which is
        # shared with every other UI test file, so that signal can land
        # after this test starts poking at state unless it is drained here.
        self.wait(lambda: bool(self.host.agents))

    def tearDown(self):
        self.host.close()
        self.guest.close()
        self.wait(lambda: not self.host.network.isRunning() and not self.guest.network.isRunning())
        self.directory.cleanup()

    def send(self, window, text):
        self.wait(lambda: window.send_button.isEnabled())
        window.composer.setPlainText(text)
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: any(role == "assistant" and value == "AI: " + text
                             for role, value in window.chats[window.selected]["messages"]))

    def test_invitation_adds_shared_chat_to_both_devices_and_syncs_ai_replies(self):
        async def model(request):
            body = await request.json()
            return JSONResponse({"choices": [{"message": {"content": "AI: " + body["messages"][-1]["content"]},
                                               "finish_reason": "stop"}]})
        connect_model(self, self.host, "shared-model", handler=model)
        self.send(self.host, "Before the guest joined")
        history = [{"role": role, "content": text}
                   for role, text in self.host.chats["shared-model"]["messages"]]
        invite = InviteDialog(self.host, target="shared-model", messages=history)
        invite.show()
        invite.name.setText("guest-laptop")
        invite.url.setText(f"ws://127.0.0.1:{self.host.port}/connect")
        QTest.mouseClick(invite.create_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: bool(invite.result.toPlainText()))
        room_id = invite.conversation_id
        self.assertEqual(self.host.selected, room_id)
        join_workspace(self, self.guest, invite.result.toPlainText())
        self.wait(lambda: self.guest.selected == room_id)
        for window in (self.host, self.guest):
            self.assertIn(room_id, window.conversations)
            self.assertGreaterEqual(window.conversation_picker.findData(room_id), 0)
            self.assertTrue(any(text == "Before the guest joined" for _, text in window.chats[room_id]["messages"]))
            window.select_agent("@jev")
            window.conversation_picker.activated.emit(window.conversation_picker.findData(room_id))
            self.assertEqual(window.selected, room_id)
        self.send(self.guest, "Guest message")
        self.wait(lambda: any(text == "AI: Guest message" for _, text in self.host.chats[room_id]["messages"]))
        self.send(self.host, "Host message")
        self.wait(lambda: any(text == "AI: Host message" for _, text in self.guest.chats[room_id]["messages"]))
        self.assertEqual([text for _, text in self.host.chats[room_id]["messages"]],
                         [text for _, text in self.guest.chats[room_id]["messages"]])
        self.assertIn(("member:guest-laptop", "Guest message"), self.host.chats[room_id]["messages"])
        self.assertIn(("user", "Guest message"), self.guest.chats[room_id]["messages"])
        invite.close()

    def check_workspace_invitation(self, paste):
        async def model(request):
            body = await request.json()
            return JSONResponse({"choices": [{"message": {"content": "AI: " + body["messages"][-1]["content"]}}]})
        connect_model(self, self.host, "shared-model", handler=model)
        self.host.select_agent(GENERAL_TARGET)
        self.send(self.host, "Our shared project is Aurora")
        self.host.invite_device()
        self.host.workspace_invite_name.setText("guest-laptop")
        self.host.workspace_invite_url.setText(f"ws://127.0.0.1:{self.host.port}/connect")
        self.host.workspace_invite_create_button.click()
        self.wait(lambda: bool(self.host.workspace_invite_result.toPlainText()))
        invitation = json.loads(self.host.workspace_invite_result.toPlainText())
        room_id = invitation["conversation_id"]
        self.guest.navigate(WORKSPACES_PAGE)
        if paste:
            self.guest.workspace_invitation.setPlainText(json.dumps(invitation))
        else:
            self.guest.workspace_relay.setText(invitation["url"])
            self.guest.workspace_token.setText(invitation["token"])
            self.guest.workspace_lan.setChecked(True)
        self.guest.workspace_join_button.click()
        self.wait(lambda: not self.guest.workspace_busy["join"])
        self.assertEqual(self.guest.selected, room_id)
        self.assertEqual(self.guest.stack.currentIndex(), CONVERSATIONS_PAGE)
        self.assertIn(("assistant", "AI: Our shared project is Aurora"), self.guest.chats[room_id]["messages"])
        self.host.select_agent(GENERAL_TARGET)
        self.assertEqual(self.host.selected, room_id)
        self.assertEqual(self.host.conversation_picker.findData(GENERAL_TARGET), -1)
        self.send(self.guest, "Continue our project")
        self.wait(lambda: ("assistant", "AI: Continue our project") in self.host.chats[room_id]["messages"])
        self.send(self.host, "Host follow-up")
        self.wait(lambda: ("assistant", "AI: Host follow-up") in self.guest.chats[room_id]["messages"])
        self.assertEqual([text for _, text in self.host.chats[room_id]["messages"]],
                         [text for _, text in self.guest.chats[room_id]["messages"]])

    def test_workspace_invitation_opens_shared_chat_with_existing_history(self):
        self.check_workspace_invitation(paste=True)

    def test_joining_with_just_address_and_token_opens_the_same_shared_chat(self):
        self.check_workspace_invitation(paste=False)

    def test_model_replies_render_markdown_without_changing_conversation_history(self):
        text = "## Model heading\n\nHere is **bold text** and `inline code`."
        self.host.select_agent(self.host.identity)
        chat = self.host.chats[self.host.identity]
        # The local responder's id rides along in the role now, so both forms
        # of an agent reply are covered: the bare role is still what a saved
        # conversation holds, and the suffixed one is what arrives today. They
        # are matched by prefix, so both have to go through the renderer --
        # compared exactly, the suffixed form would quietly be drawn as raw
        # text with its markdown left in it.
        chat["messages"] = [("user", text), ("assistant", text),
                            ("local_agent", text),
                            ("local_agent:" + self.host.identity, text)]
        self.host.render_messages()
        # render_messages() rebuilds the bubble layout from scratch, and
        # render_agents() is reachable from a queued poller signal, so the
        # reply widgets are settled on rather than read the instant they are
        # created. Every other test in this file waits for the UI; this one
        # used to assert a single synchronous snapshot.
        self.wait(lambda: len(self.host.messages_widget.findChildren(MarkdownMessage)) == 3)
        replies = self.host.messages_widget.findChildren(MarkdownMessage)
        self.assertEqual(len(replies), 3)
        for reply in replies:
            self.assertEqual(reply.document().begin().blockFormat().headingLevel(), 2)
            self.assertNotIn("**", reply.toPlainText())
            self.assertIn("bold text", reply.toPlainText())
        self.assertEqual(chat["messages"], [("user", text), ("assistant", text),
                                            ("local_agent", text),
                                            ("local_agent:" + self.host.identity, text)])
        self.wait(lambda: self.host.messages.count() > 0)
        # The user's own text is rendered plainly, with the markdown left
        # in it, while the two replies above went through the renderer. It
        # is found by scanning the row's labels rather than by index: a
        # message row now carries the speaker's mark, the body and a slot
        # for hover actions, so no single position identifies the body.
        first_row = self.host.messages.itemAt(0).widget()
        bodies = [child.text() for child in first_row.findChildren(QLabel) if child.text()]
        self.assertIn(text, bodies,
                      "the user's message must show its text exactly as sent, "
                      "markdown included")
