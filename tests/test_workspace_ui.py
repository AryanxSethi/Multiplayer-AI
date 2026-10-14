"""Exercise saved context and workspace controls through native desktop windows."""
import asyncio
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox
from starlette.responses import JSONResponse
from starlette.routing import Route

from network_a2a.persistence import HistoryStore

from desktop_app.dialogs import InviteDialog
from desktop_app.agent_list_dialog import AgentListDialog
from desktop_app.storage import Storage
from desktop_app.window import AGENTS_PAGE, MainWindow, WORKSPACES_PAGE
from desktop_app.workspace_dialog import WorkspaceDialog
from tests.test_desktop_runtime import MemoryVault
from tests.test_attachments import make_files


class WorkspaceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("Workspace UI condition timed out")
            time.sleep(.01)
        self.app.processEvents()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.host = self.open_window("host", self.vault)
        self.guest = None

    def open_window(self, name, vault):
        window = MainWindow(Storage(Path(self.directory.name) / name, vault))
        window.show()
        self.wait(lambda: window.ready)
        return window

    def stop(self, window):
        window.close()
        self.wait(lambda: not window.network.isRunning())

    def tearDown(self):
        if self.guest:
            self.stop(self.guest)
        self.stop(self.host)
        self.directory.cleanup()

    def model_card(self, window=None):
        window = window or self.host
        window.navigate(AGENTS_PAGE)
        return window

    def _agent_ready(self, window, agent_id):
        entry = window.network.runtime.engine.runners.get(agent_id)
        return bool(entry and entry[0] is not None and entry[0].ready.is_set())

    def wait_for_agent(self, window=None, agent_id="saved-model"):
        window = window or self.host
        self.wait(lambda: any(a["id"] == agent_id and a["online"] for a in window.agents),
                  30 if window.remote else 15)
        if not window.remote:
            self.wait(lambda: self._agent_ready(window, agent_id))
        return window

    def model_error_text(self, window=None):
        """The card's validation line, which replaces the dialog error box."""
        window = window or self.host
        line = window.model_error_line
        return line.message.text() if line.isVisible() else ""

    def save_model_card(self, window=None, expect_error=False):
        """Press Save and wait for the card to finish with the save.

        AgentDialog closed on success and stayed open on failure, so its closing
        was an honest signal. The card never closes and its save button is
        enabled before and after, because save_model re-enables it on failure too
        and never relabels it. So the only honest signal is the window's in-flight
        flag, and it is an honest one: save_agent is fully awaited, so the vault
        write happens inside it and the flag clears only once everything has been
        persisted. Waiting for the saved profile instead would be wrong, because
        saving an unchanged profile leaves it identical and the restart test does
        exactly that.
        """
        window = window or self.host
        window.model_save_button.click()
        if expect_error:
            self.wait(lambda: bool(self.model_error_text(window)))
            return window
        self.wait(lambda: window.busy)
        self.wait(lambda: not window.busy)
        return window

    def edit_model(self, profile, window=None):
        """Load a profile into the model card the way the app itself does.

        edit_agent takes an entry from the agent list, not a bare profile: it
        reads agent["profile"], and a bare profile has no such key, so it is given
        {} and loads an empty form instead -- no model id, search off, no keys.
        The save then fails validation on the empty model id and never persists,
        so the test goes on to read back the previous, stale profile. That is
        where a web_search of "off" and an empty vault both came from.
        """
        window = self.model_card(window)
        window.edit_agent({"id": profile["id"], "provider": profile["provider"],
                           "profile": profile})
        self.wait(lambda: window.model_id.currentText() == profile["model"])
        return window

    def join_workspace(self, window, invitation):
        """Join through the workspace card's invitation box.

        JoinDialog reported a bad invitation by staying open. The card writes the
        refusal on its join line, so wait for the room to be selected rather than
        for a dialog to close.
        """
        window.navigate(WORKSPACES_PAGE)
        window.workspace_invitation.setPlainText(invitation)
        window.workspace_join_button.click()
        return window

    def configure_model(self, handler, vision=False):
        self.host.network.runtime.app.router.routes.append(
            Route("/mock/v1/chat/completions", handler, methods=["POST"]))
        async def installed(request):
            return JSONResponse({"models": [{"name": "test-model:latest"}]})
        self.host.network.runtime.app.router.routes.append(
            Route("/mock/v1/api/tags", installed, methods=["GET"]))
        window = self.model_card()
        window.model_provider.setCurrentIndex(window.model_provider.findData("bionic"))
        window.change_model_provider()
        window.model_name.setText("saved-model")
        window.model_base.setText(f"http://127.0.0.1:{self.host.port}/mock/v1")
        window.model_id.setCurrentText("test-model")
        window.model_key.setText("test-key")
        window.model_vision.setChecked(vision)
        self.save_model_card(window)
        self.wait_for_agent(window)
        window.select_agent("saved-model")

    @staticmethod
    async def echo_model(request):
        body = await request.json()
        return JSONResponse({"choices": [{"message": {"content": "AI: " + body["messages"][-1]["content"]}, "finish_reason": "stop"}]})

    def send(self, window, text):
        self.wait(lambda: window.send_button.isEnabled())
        window.composer.setPlainText(text)
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)

    def reply_received(self, window, target, text):
        return ("assistant", "AI: " + text) in window.chats.get(target, {}).get("messages", [])

    def switch(self, window, workspace_id):
        QTest.mouseClick(window.workspace_buttons[workspace_id], Qt.MouseButton.LeftButton)
        self.wait(lambda: window.workspace_id == workspace_id and window.network.runtime.active_workspace_id == workspace_id)

    def test_ai_context_draft_and_selected_chat_survive_a_full_app_restart(self):
        calls = []
        async def model(request):
            calls.append((await request.json())["messages"])
            return await self.echo_model(request)
        self.configure_model(model)
        self.send(self.host, "My project is named Aurora")
        self.wait(lambda: self.reply_received(self.host, "saved-model", "My project is named Aurora"))
        self.host.composer.setPlainText("Unsaved draft survives too")
        self.stop(self.host)
        self.host = self.open_window("host", self.vault)
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", model, methods=["POST"]))
        self.assertEqual(self.host.selected, "saved-model")
        self.assertEqual(self.host.composer.toPlainText(), "Unsaved draft survives too")
        self.assertTrue(self.reply_received(self.host, "saved-model", "My project is named Aurora"))
        self.assertEqual(len(calls), 1)
        self.send(self.host, "What was my project name?")
        self.wait(lambda: len(calls) == 2 and self.reply_received(self.host, "saved-model", "What was my project name?"))
        self.assertEqual(calls[-1][-3:], [{"role": "user", "content": "My project is named Aurora"},
            {"role": "assistant", "content": "AI: My project is named Aurora"}, {"role": "user", "content": "What was my project name?"}])

    def test_create_rename_switch_and_delete_controls_preserve_other_workspace_history(self):
        original = self.host.workspace_id
        self.host.select_agent(self.host.identity)
        target = self.host.identity
        self.send(self.host, "Keep the original chat")
        self.wait(lambda: not self.host.chats[target]["pending"])
        original_messages = list(self.host.chats[target]["messages"])
        self.host.navigate(WORKSPACES_PAGE)
        self.host.workspace_name_input.setText("Research")
        self.host.create_workspace()
        self.wait(lambda: self.host.workspace_id != original)
        second = self.host.workspace_id
        self.assertEqual(self.host.workspace_label.text(), "Research")
        self.assertNotIn(target, self.host.chats)
        dialog = WorkspaceDialog(self.host)
        dialog.show()
        dialog.name.setText("Research lab")
        QTest.mouseClick(dialog.rename_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: self.host.workspace_label.text() == "Research lab")
        self.assertEqual(self.host.workspace_buttons[second].toolTip(), "Research lab")
        dialog.close()
        self.switch(self.host, original)
        self.assertEqual(self.host.chats[target]["messages"], original_messages)
        self.switch(self.host, second)
        deleted_directory = Path(self.directory.name) / "host" / "workspaces" / second
        deleted_directory.mkdir(parents=True, exist_ok=True)
        (deleted_directory / "settings.json").write_text("{}", encoding="utf-8")
        deleted_archive = deleted_directory / "history.sqlite3"
        HistoryStore(deleted_directory).save("ui", "state", {"chats": {"gone": {}}})
        dialog = WorkspaceDialog(self.host)
        dialog.show()
        with patch("desktop_app.workspace_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: self.host.workspace_id == original and not dialog.isVisible())
        self.assertNotIn(second, self.host.workspace_buttons)
        self.assertEqual(self.host.chats[target]["messages"], original_messages)
        # The workspace's directory is removed outright, so its settings and
        # chat archive go with it rather than being emptied in place.
        self.assertFalse(deleted_directory.exists())

    def test_leaving_says_what_only_happens_on_this_device(self):
        """What leaving a shared workspace does is three things, and only one is certain.

        The local half always happens: the conversations saved for that workspace
        are deleted from this device. What the relay does with this device's
        membership is not certain, and two ordinary cases are not either -- an
        offline host cannot revoke anything, because delete_workspace catches
        the transport error and forgets the token locally, and a relay whose
        operator manages membership accepts the leave while the device stays a
        member.

        The wording said "the host will remove this device", which is true in
        neither case and is the single fact a reader deciding to leave would
        most want. Answered No throughout, so the workspaces are untouched.
        """
        asked = []

        def question(parent, title, message, *args, **kwargs):
            asked.append(message)
            return QMessageBox.StandardButton.No

        self.host.remote = True
        leaving = WorkspaceDialog(self.host)
        with patch("desktop_app.workspace_dialog.QMessageBox.question", question):
            leaving.delete()
        leaving.close()
        self.host.remote = False

        self.assertEqual(len(asked), 1, "leaving must ask exactly once")
        self.assertIn("deleted from this device", asked[0])
        self.assertIn("only forgotten here", asked[0])
        self.assertIn("manages membership", asked[0])

        asked.clear()
        deleting = WorkspaceDialog(self.host)
        with patch("desktop_app.workspace_dialog.QMessageBox.question", question):
            deleting.delete()
        deleting.close()

        self.assertEqual(len(asked), 1, "deleting must ask exactly once")
        self.assertIn("All members will be disconnected", asked[0])
        self.assertNotIn("only forgotten here", asked[0],
                         "deleting your own workspace does not need the relay caveat")

    def test_repeated_last_workspace_deletion_keeps_window_open_and_stops_closed_dialogs(self):
        dialogs = []
        for _ in range(3):
            original = self.host.workspace_id
            dialog = WorkspaceDialog(self.host)
            dialogs.append(dialog)
            QTimer.singleShot(20, dialog.delete_button.click)
            with patch("desktop_app.workspace_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                dialog.exec()
            self.assertFalse(dialog.timer.isActive())
            self.assertNotEqual(self.host.workspace_id, original)
            self.assertTrue(self.host.isVisible())
            self.assertTrue(self.host.network.isRunning())
            self.assertEqual(len(self.host.workspace_list), 1)
        # A closed dialog used to fire its table-refresh timer here and crash Qt.
        QTest.qWait(1200)
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.host.network.isRunning())
        self.host.command("create_workspace", "After deletion")
        self.wait(lambda: len(self.host.workspace_list) == 2)

    def test_invitation_network_selector_updates_address_and_preserves_manual_wss(self):
        with patch("desktop_app.dialogs.lan_addresses", return_value=[
                ("Wi-Fi", "192.168.43.12"), ("Ethernet", "192.168.1.2")]):
            dialog = InviteDialog(self.host)
        self.assertEqual(dialog.url.text(), f"ws://192.168.43.12:{self.host.port}/connect")
        dialog.network_address.setCurrentIndex(1)
        self.assertEqual(dialog.url.text(), f"ws://192.168.1.2:{self.host.port}/connect")
        dialog.lan.setChecked(False)
        dialog.url.setText("wss://relay.example.com/connect")
        dialog.network_address.setCurrentIndex(0)
        self.assertEqual(dialog.url.text(), "wss://relay.example.com/connect")
        dialog.close()

    def test_create_from_the_page_preserves_an_animated_rail_button(self):
        """Creating through the real UI must not disturb the rail.

        The rail button's hover animation used to be running when the flow
        was a pop-up menu and a prompt, so both had to survive the menu
        opening and closing under the pointer. Creating is a page now, so
        what has to survive is the navigation and the click.
        """
        original = self.host.workspace_id
        button = self.host.workspace_buttons[original]
        button.animation.setDuration(10000)
        button.animation.setStartValue(23.0)
        button.animation.setEndValue(15.0)
        button.animation.start()

        self.host.navigate(WORKSPACES_PAGE)
        self.host.workspace_name_input.setText("Created through the page")
        self.wait(lambda: self.host.workspace_create_button.isEnabled())
        QTest.mouseClick(self.host.workspace_create_button, Qt.MouseButton.LeftButton)

        self.wait(lambda: self.host.workspace_id != original)
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.host.network.isRunning())
        self.assertEqual(self.host.workspace_label.text(), "Created through the page")
        self.assertIs(self.host.workspace_buttons[original], button)
        self.assertEqual(self.host.workspace_rail.count(), 2)
        self.switch(self.host, original)
        self.assertIs(self.host.workspace_buttons[original], button)

    def test_shared_chat_is_readable_offline_and_members_can_be_managed_after_restart(self):
        self.configure_model(self.echo_model)
        self.send(self.host, "Remember this shared project")
        self.wait(lambda: self.reply_received(self.host, "saved-model", "Remember this shared project"))
        invite = InviteDialog(self.host, target="saved-model", messages=list(self.host.chats["saved-model"]["history"]))
        invite.show()
        invite.name.setText("guest-laptop")
        invite.url.setText(f"ws://127.0.0.1:{self.host.port}/connect")
        QTest.mouseClick(invite.create_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: bool(invite.result.toPlainText()))
        invitation, room_id = invite.result.toPlainText(), invite.conversation_id
        invite.close()
        guest_vault = MemoryVault()
        self.guest = self.open_window("guest", guest_vault)
        self.join_workspace(self.guest, invitation)
        self.wait(lambda: self.guest.selected == room_id)
        self.wait_for_agent(self.guest)
        guest_settings = WorkspaceDialog(self.guest)
        self.assertTrue(guest_settings.name.isReadOnly())
        self.assertFalse(guest_settings.rename_button.isEnabled())
        self.assertFalse(guest_settings.invite_button.isEnabled())
        guest_settings.close()
        self.stop(self.guest)
        self.stop(self.host)
        self.guest = self.open_window("guest", guest_vault)
        self.assertEqual(self.guest.selected, room_id)
        self.assertTrue(self.reply_received(self.guest, room_id, "Remember this shared project"))
        self.assertFalse(self.guest.send_button.isEnabled())
        self.host = self.open_window("host", self.vault)
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", self.echo_model, methods=["POST"]))
        self.join_workspace(self.guest, invitation)
        # engine.remote, not active_url and not selected. active_url is the local
        # relay's from the moment the window opens, and None before that, and
        # None != "" -- so that wait was true before the join was even attempted.
        # selected is no better: this guest restored it from disk, so the
        # assertion above already holds.
        #
        # remote is the one thing a join has to earn. __init__ and _use_local
        # leave it False, join sets it once the invitation has been inspected,
        # and a failure rolls it back to whatever it was. So a rejoin that did
        # not work fails here, instead of surfacing later as an unexplained
        # timeout waiting for an agent the host was never asked about.
        # Thirty seconds, as elsewhere for a remote window. This join is a
        # relay round trip and measures around fifteen, which is the default
        # wait -- so the earlier condition, being true before the join was
        # even attempted, was the only reason this never timed out.
        self.wait(lambda: self.guest.network.runtime.engine.remote, 30)
        self.wait_for_agent(self.guest)
        self.send(self.guest, "Continue after reopening")
        self.wait(lambda: self.reply_received(self.guest, room_id, "Continue after reopening"))
        self.wait(lambda: self.reply_received(self.host, room_id, "Continue after reopening"))
        settings = WorkspaceDialog(self.host)
        settings.show()
        row = next(row for row in range(settings.members.rowCount()) if settings.members.item(row, 0).text() == "guest-laptop")
        with patch("desktop_app.workspace_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(settings.members.cellWidget(row, 3), Qt.MouseButton.LeftButton)
        self.wait(lambda: "guest-laptop" not in self.host.network.runtime.credentials)
        self.wait(lambda: all(member["id"] != "guest-laptop" for member in self.host.workspace_meta["members"]))
        settings.close()

    def test_reply_survives_switching_away_and_back_while_request_runs(self):
        started, release = threading.Event(), threading.Event()
        async def delayed_model(request):
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return await self.echo_model(request)
        self.configure_model(delayed_model)
        first = self.host.workspace_id
        self.host.navigate(WORKSPACES_PAGE)
        self.host.workspace_name_input.setText("Other workspace")
        self.host.create_workspace()
        self.wait(lambda: self.host.workspace_id != first)
        second = self.host.workspace_id
        self.switch(self.host, first)
        self.send(self.host, "Reply after switching back")
        self.wait(started.is_set)
        self.switch(self.host, second)
        self.switch(self.host, first)
        self.assertTrue(self.host.chats["saved-model"]["pending"])
        release.set()
        self.wait(lambda: self.reply_received(self.host, "saved-model", "Reply after switching back"))
        self.assertFalse(self.host.chats["saved-model"]["pending"])
        started.clear()
        release.clear()
        self.send(self.host, "Reply while away")
        self.wait(started.is_set)
        self.switch(self.host, second)
        release.set()
        self.wait(lambda: not self.host.live_requests)
        self.assertNotIn("saved-model", self.host.chats)
        self.switch(self.host, first)
        self.assertTrue(self.reply_received(self.host, "saved-model", "Reply while away"))
        self.assertFalse(self.host.chats["saved-model"]["pending"])

    def test_counts_and_clickable_lists_include_models_and_exclude_devices(self):
        self.configure_model(self.echo_model)
        result = []
        self.host.command("invite", "another-user", f"ws://127.0.0.1:{self.host.port}/connect", success=result.append)
        self.wait(lambda: bool(result) and len(self.host.agents) == 3)
        self.assertEqual(self.host.stat_values[0].text(), "1")
        self.assertEqual(self.host.stat_values[1].text(), "1")
        self.assertEqual(self.host.chat_agent_count.text(), "1 agent")
        for button in (self.host.chat_agent_count, self.host.stat_values[0]):
            captured = []
            def inspect_dialog():
                dialog = self.app.activeModalWidget()
                if isinstance(dialog, AgentListDialog):
                    captured.append([dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())])
                    dialog.accept()
            driver = QTimer(self.host)
            driver.setInterval(10)
            driver.timeout.connect(inspect_dialog)
            driver.start()
            try:
                button.click()
            finally:
                driver.stop()
            self.assertEqual(captured, [["saved-model"]])

    def test_edit_model_saves_and_uses_searxng_settings(self):
        """Verify legacy profiles retain SearXNG and edited settings drive actual searches."""
        calls, searches = [], []
        async def model(request):
            body = await request.json()
            calls.append(body)
            text = '{"web_search_query":"current public facts"}' if "optional web search tool" in body["messages"][0]["content"] else "A verified answer"
            return JSONResponse({"choices": [{"message": {"content": text}}]})
        async def search(request):
            """Record SearXNG query parameters and return a cited source for the UI test."""
            searches.append(dict(request.query_params))
            return JSONResponse({"results": [{"title": "Public facts", "url": "https://source.example/current", "content": "Evidence today"}]})
        self.configure_model(model)
        self.host.network.runtime.app.router.routes.append(Route("/search", search))
        profile = dict(next(agent["profile"] for agent in self.host.agents if agent["id"] == "saved-model"))
        profile.pop("search_provider", None)  # Profiles saved before the provider choice use SearXNG.
        card = self.edit_model(profile)
        card.model_internet.setChecked(True)
        self.assertEqual(card.model_search_provider.currentData(), "searxng")
        self.save_model_card(card, expect_error=True)
        self.assertIn("SearXNG", self.model_error_text(card))
        self.assertIn("server address", self.model_error_text(card))
        self.app.processEvents()
        self.assertTrue(card.model_search_url.hasFocus())
        search_url = f"http://127.0.0.1:{self.host.port}"
        card.model_search_url.setText(search_url)
        card.model_search_test.click()
        self.wait(lambda: "reachable" in card.model_search_status.text())
        card.model_vision.setChecked(True)
        self.save_model_card(card)
        saved = self.host.network.runtime.storage.settings["agents"][0]
        self.assertEqual(saved["web_search"], "auto")
        self.assertTrue(saved["vision"])
        self.edit_model(saved, card)
        self.assertTrue(card.model_internet.isChecked())
        self.assertEqual(card.model_search_mode.currentData(), "auto")
        self.assertEqual(card.model_search_url.text(), search_url)
        self.send(self.host, "Tell me the latest facts")
        self.wait(lambda: not self.host.chats["saved-model"]["pending"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(searches[-1]["q"], "current public facts")
        self.assertIn("Evidence today", calls[-1]["messages"][0]["content"])
        self.assertIn("https://source.example/current", self.host.chats["saved-model"]["messages"][-1][1])

    def test_hosted_search_key_is_saved_separately_and_reused_after_restart(self):
        """Verify hosted search credentials stay in the vault and work after app restart."""
        calls, searches = [], []
        async def model(request):
            """Record model prompts and request search evidence before returning an answer."""
            body = await request.json()
            calls.append(body)
            text = '{"web_search_query":"latest public facts"}' if "optional web search tool" in body["messages"][0]["content"] else "An answer with sources"
            return JSONResponse({"choices": [{"message": {"content": text}}]})
        async def search(service, query):
            """Record the search key and query while supplying evidence for the model answer."""
            searches.append((service.api_key, query))
            return [{"title": "Fresh facts", "url": "https://source.example/new", "snippet": "Current evidence"}]
        self.configure_model(model)
        saved = self.host.network.runtime.storage.settings["agents"][0]
        card = self.edit_model(saved)
        card.model_internet.setChecked(True)
        card.model_search_provider.setCurrentIndex(card.model_search_provider.findData("ollama"))
        self.assertFalse(card.model_search_url.isVisible())
        self.assertTrue(card.model_search_key.isVisible())
        card.model_search_key.setText("private-search-key")
        with patch("network_a2a.web_search.OllamaSearch.search", search):
            card.model_search_test.click()
            self.wait(lambda: "reachable" in card.model_search_status.text())
            self.save_model_card(card)
            self.assertEqual(self.host.network.runtime.storage.vault.get("search:saved-model"), "private-search-key")
            self.assertEqual(self.host.network.runtime.storage.vault.get("provider:saved-model"), "test-key")
            self.assertNotIn("private-search-key", self.host.network.runtime.storage.path.read_text())
            saved = self.host.network.runtime.storage.settings["agents"][0]
            self.assertEqual(saved["search_provider"], "ollama")
            self.stop(self.host)
            self.host = self.open_window("host", self.vault)
            self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", model, methods=["POST"]))
            card = self.edit_model(saved)
            self.assertEqual(card.model_search_provider.currentData(), "ollama")
            self.assertEqual(card.model_search_key.text(), "")
            card.model_search_test.click()
            self.wait(lambda: "reachable" in card.model_search_status.text())
            self.save_model_card(card)
            self.host.select_agent("saved-model")
            self.send(self.host, "Tell me the latest facts")
            self.wait(lambda: not self.host.chats["saved-model"]["pending"])
        self.assertEqual(searches, [("private-search-key", "web search"), ("private-search-key", "web search"), ("private-search-key", "latest public facts")])
        self.assertEqual(len(calls), 2)
        self.assertIn("Current evidence", calls[-1]["messages"][0]["content"])
        self.assertNotIn("private-search-key", str(calls))
        self.assertIn("https://source.example/new", self.host.chats["saved-model"]["messages"][-1][1])

    def test_new_search_setup_defaults_to_hosted_and_explains_missing_key(self):
        """Verify new connections default to hosted search and focus a missing key error."""
        card = self.model_card()
        card.model_name.setText("searching-model")
        card.model_provider.setCurrentIndex(card.model_provider.findData("ollama"))
        card.change_model_provider()
        card.model_base.setText("http://127.0.0.1:11434")
        card.model_id.setCurrentText("llama3")
        card.model_internet.setChecked(True)
        self.save_model_card(card, expect_error=True)

    def test_periodic_agent_refresh_keeps_overview_at_the_user_scroll_position(self):
        """Verify polling preserves cards and scroll position while actual state changes render."""
        self.configure_model(self.echo_model)
        self.host.navigate(0)
        self.host.resize(self.host.width(), self.host.minimumHeight())
        self.app.processEvents()
        page = self.host.stack.widget(0)
        scrollbar = page.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(scrollbar.maximum())
        position = scrollbar.value()
        card = self.host.overview_cards.itemAtPosition(0, 0).widget()
        # Both periodic events used to destroy and recreate the same cards.
        for _ in range(3):
            self.host.network_event("agents", {"agents": self.host.agents, "connected": True})
            self.host.network_event("conversations", list(self.host.conversations.values()))
            self.app.processEvents()
        self.assertIs(self.host.overview_cards.itemAtPosition(0, 0).widget(), card)
        self.assertEqual(scrollbar.value(), position)
        # Actual model state changes must still reach the page.
        self.host.agents = [{**agent, "online": False} if agent["id"] == "saved-model" else agent for agent in self.host.agents]
        self.host.render_agents()
        self.assertEqual(self.host.stat_values[1].text(), "0")

    def test_help_opens_bundled_documentation_in_the_browser(self):
        """Verify the help button opens the bundled page containing the core project guides."""
        with patch("desktop_app.window.QDesktopServices.openUrl", return_value=True) as opened:
            self.host.help_button.click()
        opened.assert_called_once()
        path = Path(opened.call_args.args[0].toLocalFile())
        self.assertEqual(path.name, "docs.html")
        page = path.read_text(encoding="utf-8")
        self.assertIn('id="desktop"', page)
        self.assertIn('id="network"', page)
        self.assertIn('id="providers"', page)

    def test_uploaded_image_and_pdf_reach_model_and_survive_restart(self):
        """Verify uploaded model attachments and their conversation history survive restart."""
        calls = []
        async def model(request):
            """Record attachment-bearing model requests and return a successful response."""
            calls.append(await request.json())
            return JSONResponse({"choices": [{"message": {"content": "Files understood"}}]})
        self.configure_model(model, vision=True)
        files = make_files(Path(self.directory.name))
        with patch("desktop_app.window.QFileDialog.getOpenFileNames", return_value=([str(path) for path in files[:2]], "")):
            self.host.attach_button.click()
        self.wait(lambda: not self.host.preparing_files)
        attached = self.host.chats["saved-model"]["draft_attachments"]
        self.assertEqual(len(attached), 2)
        self.host.composer.setPlainText("Please understand these files")
        self.host.send_button.click()
        self.wait(lambda: ("assistant", "Files understood") in self.host.chats["saved-model"]["messages"])
        content = calls[0]["messages"][-1]["content"]
        self.assertTrue(any(part.get("type") == "image_url" for part in content))
        self.assertTrue(any("Aurora" in part.get("text", "") for part in content))
        self.assertIn("report.pdf", self.host.chats["saved-model"]["messages"][0][1])
        self.assertNotIn("base64", self.host.chats["saved-model"]["messages"][0][1])
        self.stop(self.host)
        self.host = self.open_window("host", self.vault)
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", model, methods=["POST"]))
        self.send(self.host, "What did the PDF say?")
        self.wait(lambda: len(calls) == 2 and not self.host.chats["saved-model"]["pending"])
        self.assertEqual(calls[-1]["messages"][0]["content"], content)
        self.assertEqual(calls[-1]["messages"][-1]["content"], "What did the PDF say?")

    def test_guest_can_upload_files_to_shared_model_chat(self):
        calls = []
        async def model(request):
            calls.append(await request.json())
            return JSONResponse({"choices": [{"message": {"content": "Shared files understood"}}]})
        self.configure_model(model, vision=True)
        invite = InviteDialog(self.host, target="saved-model")
        invite.show()
        invite.name.setText("guest-laptop")
        invite.url.setText(f"ws://127.0.0.1:{self.host.port}/connect")
        invite.create_button.click()
        self.wait(lambda: bool(invite.result.toPlainText()))
        room_id = invite.conversation_id
        self.guest = self.open_window("guest", MemoryVault())
        self.join_workspace(self.guest, invite.result.toPlainText())
        self.wait(lambda: self.guest.selected == room_id)
        self.wait_for_agent(self.guest)
        self.assertEqual(self.guest.stat_values[0].text(), "1")
        self.assertEqual(self.guest.chat_agent_count.text(), "1 agent")
        files = make_files(Path(self.directory.name))
        self.guest.prepare_files([str(path) for path in files[:2]])
        self.wait(lambda: not self.guest.preparing_files)
        self.assertEqual(len(self.guest.chats[room_id]["draft_attachments"]), 2)
        self.send(self.guest, "Understand these shared files")
        self.wait(lambda: ("assistant", "Shared files understood") in self.guest.chats[room_id]["messages"])
        self.wait(lambda: ("assistant", "Shared files understood") in self.host.chats[room_id]["messages"])
        self.assertTrue(any("Aurora" in part.get("text", "") for part in calls[0]["messages"][-1]["content"]))
        self.assertIn("report.pdf", self.host.chats[room_id]["messages"][0][1])
        self.assertIsInstance(self.host.chats[room_id]["messages"][0][1], str)
        self.assertIsInstance(self.host.conversations[room_id]["messages"][0]["content"], list)
        invite.close()
