"""Workspace commands persist real routing policy without generating model requests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.responses import JSONResponse
from starlette.routing import Route

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage
from network_a2a.orchestration import GENERAL_TARGET
from tests.test_desktop_runtime import MemoryVault


class ChatCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = Storage(self.directory.name, MemoryVault())
        self.runtime = DesktopRuntime(self.storage)
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()
        self.calls = []
        async def provider(request):
            body = await request.json()
            self.calls.append(body)
            if body["messages"][-1]["content"].startswith("You are the task coordinator"):
                answer = json.dumps({"tasks": [{"agent_id": "coder", "task_type": "coding", "instruction": "Write the function"}],
                                     "reason": "Assigned Coder role", "reply": ""})
            else:
                answer = "Function ready"
            return JSONResponse({"choices": [{"message": {"content": answer}}]})
        self.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", provider, methods=["POST"]))
        self.profile = {"id": "first", "provider": "bionic", "model": "test-model", "autostart": True,
                        "base_url": f"http://127.0.0.1:{self.runtime.port}/mock/v1"}
        for identity in ("first", "coder"):
            await self.runtime.save_agent({**self.profile, "id": identity}, "private-key")

    async def test_role_commands_drive_real_planning_and_survive_restart(self):
        runners = dict(self.runtime.runners)
        keys = dict(self.storage.vault.values)
        await self.runtime.chat_command("/set @coder as Coder")
        await self.runtime.chat_command("/set @first as Head")
        self.assertEqual(self.calls, [], "Workspace commands must not call model providers")
        self.assertEqual(self.runtime.runners, runners, "Routing changes must not restart model adapters")
        self.assertEqual(self.storage.vault.values, keys)
        reply = await self.runtime.send(GENERAL_TARGET, {"text": "Write a Python function"})
        self.assertEqual(reply["text"], "Function ready")
        self.assertEqual(reply["routing"]["assignments"][0]["agent_id"], "coder")
        await self.runtime.close()
        self.runtime = DesktopRuntime(Storage(self.directory.name, self.storage.vault))
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()
        self.assertEqual(self.runtime.storage.settings["coordinator"], "first")
        coder = self.runtime.app.state.relay.agent_description("coder")
        self.assertTrue(coder["delegation_enabled"])
        self.assertEqual(coder["tasks"], ["coding", "debugging"])

    async def test_head_and_chat_commands_preserve_import_order_and_stop_state(self):
        await self.runtime.stop_agent("coder")
        await self.runtime.chat_command("/set @coder as Head")
        self.assertEqual(self.storage.settings["coordinator"], "coder")
        self.assertNotIn("coder", self.runtime.model_runners)
        await self.runtime.chat_command("/set @coder as Writer")
        self.assertFalse(self.runtime.app.state.relay.agent_description("coder")["online"])
        await self.runtime.chat_command("/set @coder as Chat")
        self.assertFalse(self.runtime.app.state.relay.agent_description("coder")["delegation_enabled"])
        await self.runtime.chat_command("/set head auto")
        self.assertEqual(self.storage.settings["coordinator"], "first")

    async def test_help_and_lists_work_without_ai_calls_or_private_configuration(self):
        for command, expected in (("/help", "/set @agent as Head"), ("/agents", "@coder"),
                                  ("/users", "@" + self.runtime.active_id), ("/status", "Head")):
            result = await self.runtime.chat_command(command)
            self.assertIn(expected, result["text"])
            self.assertNotIn("private-key", result["text"])
            self.assertNotIn("/mock/v1", result["text"])
        self.assertEqual(self.calls, [])

    async def test_invalid_head_user_unknown_identity_and_commands_do_not_change_settings(self):
        before = json.dumps(self.storage.settings, sort_keys=True)
        for command in (f"/set @{self.runtime.active_id} as Head", "/set @missing as Coder", "/bad", "/help extra"):
            with self.assertRaises(ValueError):
                await self.runtime.chat_command(command)
            self.assertEqual(json.dumps(self.storage.settings, sort_keys=True), before)

    async def test_failed_role_save_rolls_back_runtime_policy(self):
        previous = list(self.storage.settings["agents"])
        public = dict(self.runtime.app.state.relay.agent_profiles["coder"])
        with patch("desktop_app.storage.write_json_durably", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                await self.runtime.chat_command("/set @coder as Writer")
        self.assertEqual(self.storage.settings["agents"], previous)
        self.assertEqual(self.runtime.app.state.relay.agent_profiles["coder"], public)

    async def test_workspace_switch_does_not_apply_an_old_command_to_the_new_workspace(self):
        original = self.runtime.active_workspace_id
        await self.runtime.create_workspace("Other")
        with self.assertRaisesRegex(ValueError, "Workspace changed"):
            await self.runtime.chat_command("/set @coder as Head", original)
        self.assertNotEqual(self.runtime.active_workspace_id, original)

    async def test_invited_members_can_read_commands_but_cannot_assign_roles(self):
        guest = DesktopRuntime(Storage(Path(self.directory.name) / "guest", MemoryVault()))
        self.addAsyncCleanup(guest.close)
        await guest.start()
        invite = await self.runtime.invite("guest", self.runtime.active_url)
        await guest.join(invite["url"], invite["token"])
        self.assertIn("@coder", (await guest.chat_command("/agents"))["text"])
        self.runtime.app.state.relay.set_agent_profile("guest", {"provider": "bionic", "model": "guest-model",
            "running": True, "purpose": "Writing", "tasks": ["writing"], "delegation_enabled": True})
        self.assertNotIn("@guest", (await self.runtime.chat_command("/status"))["text"],
                         "A guest's advertised role must not be listed as approved routing policy")
        for command in ("/set @coder as Head", "/set @coder as Writer", "/set head auto"):
            with self.assertRaisesRegex(ValueError, "workspace owner"):
                await guest.chat_command(command)
        self.assertEqual(self.storage.settings["coordinator"], "first")
