"""Automatic local startup, ownership, failures, and saved model restoration."""
import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

from desktop_app.ollama_service import OllamaService, model_installed
from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage
from network_a2a.adapters import ProviderConfig
from network_a2a.orchestration import GENERAL_TARGET
from tests.test_desktop_runtime import MemoryVault


class OllamaServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = OllamaService()
        self.config = ProviderConfig("ollama", "chat", "http://localhost:11434")
        self.addAsyncCleanup(self.service.close)

    def client(self, handler):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        return http

    async def test_existing_server_is_reused_and_never_stopped(self):
        http = self.client(lambda request: httpx.Response(200, json={"models": [{"name": "chat:latest"}]}))
        with patch("desktop_app.ollama_service.subprocess.Popen") as launch:
            self.assertEqual(await self.service.models(http, self.config), ["chat:latest"])
            await self.service.close()
        launch.assert_not_called()
        self.assertEqual(self.service.processes, {})

    async def test_concurrent_discovery_starts_one_hidden_server_and_waits_for_readiness(self):
        ready = False
        process = Mock()
        process.poll.return_value = None

        async def provider(request):
            await asyncio.sleep(0)
            if not ready:
                raise httpx.ConnectError("stopped", request=request)
            return httpx.Response(200, json={"models": [{"name": "chat:latest"}]})

        def start(*args, **kwargs):
            nonlocal ready
            ready = True
            return process

        http = self.client(provider)
        with patch.object(self.service, "executable", return_value="ollama.exe"), \
                patch("desktop_app.ollama_service.subprocess.Popen", side_effect=start) as launch:
            results = await asyncio.gather(*(self.service.models(http, self.config) for _ in range(3)))
        self.assertEqual(results, [["chat:latest"]] * 3)
        launch.assert_called_once()
        args, kwargs = launch.call_args
        self.assertEqual(args, (["ollama.exe", "serve"],))
        self.assertEqual(kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        if os.name == "nt":
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        await self.service.close()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    async def test_remote_tls_and_proxy_endpoints_are_never_started_locally(self):
        def offline(request):
            raise httpx.ConnectError("stopped", request=request)
        http = self.client(offline)
        with patch.object(self.service, "_start") as launch:
            for base in ("https://models.example", "http://192.168.1.12:11434",
                         "https://localhost:11434", "http://localhost:11434/proxy"):
                with self.subTest(base=base), self.assertRaises(httpx.ConnectError):
                    config = ProviderConfig("ollama", "chat", base, allow_insecure=True)
                    await self.service.models(http, config)
        launch.assert_not_called()

    async def test_server_errors_and_missing_models_do_not_spawn_another_server(self):
        with patch.object(self.service, "_start") as launch:
            for status in (401, 503):
                http = self.client(lambda request: httpx.Response(status, json={"error": "unavailable"}))
                with self.assertRaises(httpx.HTTPStatusError):
                    await self.service.models(http, self.config)
            http = self.client(lambda request: httpx.Response(200, json={"models": []}))
            self.assertEqual(await self.service.models(http, self.config), [])
        launch.assert_not_called()

    async def test_failed_installation_is_throttled_but_external_recovery_is_immediate(self):
        ready = False
        def provider(request):
            if not ready:
                raise httpx.ConnectError("stopped", request=request)
            return httpx.Response(200, json={"models": [{"name": "chat:latest"}]})
        http = self.client(provider)
        with patch.object(self.service, "executable", side_effect=RuntimeError("Install Ollama")) as executable:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "Install Ollama"):
                    await self.service.models(http, self.config)
            executable.assert_called_once()
            ready = True
            self.assertEqual(await self.service.models(http, self.config), ["chat:latest"])

    async def test_exited_owned_server_is_started_again(self):
        def offline(request):
            raise httpx.ConnectError("stopped", request=request)
        http = self.client(offline)
        old = Mock()
        old.poll.return_value = 1
        self.service.processes["127.0.0.1:11434"] = old
        new = Mock()
        new.poll.return_value = 1
        with patch.object(self.service, "executable", return_value="ollama"), \
                patch("desktop_app.ollama_service.subprocess.Popen", return_value=new) as launch:
            with self.assertRaisesRegex(RuntimeError, "could not start"):
                await self.service.models(http, self.config)
        launch.assert_called_once()
        self.assertIs(self.service.processes["127.0.0.1:11434"], new)

    async def test_explicit_retry_bypasses_failure_backoff_without_replaying_work(self):
        ready = False
        def provider(request):
            if not ready:
                raise httpx.ConnectError("stopped", request=request)
            return httpx.Response(200, json={"models": [{"name": "chat:latest"}]})
        http = self.client(provider)
        with patch.object(self.service, "_start", side_effect=RuntimeError("Install Ollama")):
            with self.assertRaisesRegex(RuntimeError, "Install Ollama"):
                await self.service.models(http, self.config)
        process = Mock()
        process.poll.return_value = None
        def start(address):
            nonlocal ready
            ready = True
            self.service.processes[address] = process
            return process
        with patch.object(self.service, "_start", side_effect=start) as launch:
            with self.assertRaisesRegex(RuntimeError, "Install Ollama"):
                await self.service.models(http, self.config)
            await asyncio.gather(*(self.service.ensure_model(http, self.config, retry=True) for _ in range(3)))
        launch.assert_called_once()

    async def test_startup_timeout_is_bounded_and_owned_process_is_cleaned_up(self):
        def offline(request):
            raise httpx.ConnectError("stopped", request=request)
        http = self.client(offline)
        self.service.START_TIMEOUT = .01
        process = Mock()
        process.poll.return_value = None
        with patch.object(self.service, "executable", return_value="ollama"), \
                patch("desktop_app.ollama_service.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                await self.service.models(http, self.config)
        await self.service.close()
        process.terminate.assert_called_once()

    async def test_unresponsive_owned_process_is_killed_on_exit(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("ollama", 5), 0]
        self.service.processes["127.0.0.1:11434"] = process
        await self.service.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_latest_alias_and_loopback_binding(self):
        self.assertTrue(model_installed("namespace/chat", ["namespace/chat:latest"]))
        self.assertFalse(model_installed("chat:small", ["chat:latest"]))
        self.assertEqual(self.service.local_address("http://[::1]:11435/"), "[::1]:11435")


class SavedOllamaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = Storage(self.directory.name, MemoryVault())
        self.runtime = DesktopRuntime(self.storage)
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()
        # This suite exercises real relays and persistence without a real Ollama.
        self.models = ["chat:latest"]
        self.ready = True
        async def catalog(service, http, config):
            if not self.ready:
                raise httpx.ConnectError("stopped")
            return self.models
        self.catalog_patch = patch.object(OllamaService, "_models", catalog)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)
        self.launch_patch = patch.object(OllamaService, "_start", side_effect=RuntimeError("Install Ollama"))
        self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)
        self.profile = {"id": "chat-agent", "provider": "ollama", "model": "chat", "autostart": True}

    async def mock_chat(self, status=200):
        self.calls = []
        async def provider(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(status, json={"message": {"content": "Ready to help"}, "done_reason": "stop"})
        await self.runtime.http.aclose()
        self.runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))

    def start_service(self, address):
        self.ready = True
        process = Mock()
        process.poll.return_value = None
        self.runtime.ollama_service.processes[address] = process
        return process

    async def test_import_retries_immediately_after_failed_background_discovery(self):
        self.ready = False
        config = self.runtime.engine._config(self.profile, None)
        with self.assertRaisesRegex(RuntimeError, "Install Ollama"):
            await self.runtime.ollama_service.models(self.runtime.http, config)
        with patch.object(self.runtime.ollama_service, "_start", self.start_service):
            await self.runtime.save_agent(self.profile)
        self.assertTrue(self.runtime.app.state.relay.agent_description("chat-agent")["online"])

    async def test_chat_starts_offline_ollama_before_general_routing_and_sends_once(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        self.ready = False
        await self.runtime.refresh()
        self.assertFalse(self.runtime.app.state.relay.agent_description("chat-agent")["online"])
        published = self.runtime.cache[self.runtime.active_workspace_id]["agents"]["agents"]
        model = next(agent for agent in published if agent["id"] == "chat-agent")
        self.assertTrue(model["can_start"])
        self.assertTrue(model["local"])
        with patch.object(self.runtime.ollama_service, "_start", self.start_service):
            result = await self.runtime.send(GENERAL_TARGET, {"text": "Write a Python function"})
        self.assertEqual(result["text"], "Ready to help")
        self.assertEqual(result["responder"]["agent_id"], "chat-agent")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["messages"][-1]["content"], "Write a Python function")

    async def test_direct_chat_recovers_a_server_that_stopped_since_the_last_poll(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        self.ready = False
        with patch.object(self.runtime.ollama_service, "_start", self.start_service):
            result = await self.runtime.send("chat-agent", {"text": "Hello"})
        self.assertEqual(result["text"], "Ready to help")
        self.assertEqual(len(self.calls), 1)

    async def test_direct_chat_checks_available_model_only_in_the_handler(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        with patch.object(self.runtime.ollama_service, "ensure_model",
                          wraps=self.runtime.ollama_service.ensure_model) as ensure:
            result = await self.runtime.send("chat-agent", {"text": "Hello"})
        ensure.assert_awaited_once()
        self.assertEqual(result["text"], "Ready to help")
        self.assertEqual(len(self.calls), 1)

    async def test_direct_chat_prepares_connected_unavailable_model_before_dispatch(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        self.ready = False
        await self.runtime.refresh()
        self.assertFalse(self.runtime.app.state.relay.agent_description("chat-agent")["online"])
        with patch.object(self.runtime.ollama_service, "_start", self.start_service), \
                patch.object(self.runtime.ollama_service, "ensure_model",
                             wraps=self.runtime.ollama_service.ensure_model) as ensure:
            result = await self.runtime.send("chat-agent", {"text": "Hello"})
        self.assertEqual(ensure.await_count, 2)
        self.assertTrue(self.runtime.app.state.relay.agent_description("chat-agent")["online"])
        self.assertEqual(result["text"], "Ready to help")
        self.assertEqual(len(self.calls), 1)

    async def test_invited_device_can_wake_a_host_model_through_general_chat(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        invitation = await self.runtime.invite("guest", self.runtime.active_url)
        self.ready = False
        await self.runtime.refresh()
        with patch.object(self.runtime.ollama_service, "_start", self.start_service):
            response = await self.runtime.direct_http.post(f"http://127.0.0.1:{self.runtime.port}/orchestrate",
                json={"text": "Hello from another device"},
                headers={"Authorization": "Bearer " + invitation["token"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "Ready to help")
        self.assertEqual(len(self.calls), 1)

    async def test_shared_general_chat_starts_ollama_before_its_availability_check(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        room = self.runtime.app.state.relay.conversations.create(self.runtime.active_id, GENERAL_TARGET)
        self.ready = False
        await self.runtime.refresh()
        with patch.object(self.runtime.ollama_service, "_start", self.start_service):
            result = await self.runtime.send_conversation(room["id"], "Hello from the shared chat")
        self.assertFalse(result["pending"])
        self.assertEqual(result["messages"][-1]["content"], "Ready to help")
        self.assertEqual(len(self.calls), 1)

    async def test_general_chat_respects_stop_and_direct_chat_can_start_that_model(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        await self.runtime.save_agent({**self.profile, "id": "second-agent"})
        await self.runtime.stop_agent("chat-agent")
        result = await self.runtime.send(GENERAL_TARGET, {"text": "Hello"})
        self.assertEqual(result["responder"]["agent_id"], "second-agent")
        self.assertNotIn("chat-agent", self.runtime.model_runners)
        result = await self.runtime.send("chat-agent", {"text": "Start this model"})
        self.assertEqual(result["text"], "Ready to help")
        self.assertIn("chat-agent", self.runtime.model_runners)
        saved = next(profile for profile in self.storage.settings["agents"] if profile["id"] == "chat-agent")
        self.assertFalse(saved["autostart"])
        self.assertEqual(len(self.calls), 2)

    async def test_shared_chat_rejects_a_second_message_while_startup_is_waiting(self):
        await self.mock_chat()
        await self.runtime.save_agent(self.profile)
        room = self.runtime.app.state.relay.conversations.create(self.runtime.active_id, GENERAL_TARGET)
        self.ready = False
        await self.runtime.refresh()
        entered, released = asyncio.Event(), asyncio.Event()
        ensure = self.runtime.ollama_service.ensure_model
        async def slow_start(*args, **kwargs):
            entered.set()
            await released.wait()
            return await ensure(*args, **kwargs)
        with patch.object(self.runtime.ollama_service, "ensure_model", side_effect=slow_start), \
                patch.object(self.runtime.ollama_service, "_start", self.start_service):
            first = asyncio.create_task(self.runtime.send_conversation(room["id"], "First message"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                with self.assertRaisesRegex(RuntimeError, "Wait for the current AI reply"):
                    await self.runtime.send_conversation(room["id"], "Second message")
            finally:
                released.set()
                await first
        self.assertFalse(room["pending"])
        self.assertEqual(len(self.calls), 1)

    async def test_failed_generation_is_not_replayed_after_startup(self):
        await self.mock_chat(status=500)
        await self.runtime.save_agent(self.profile)
        self.ready = False
        with patch.object(self.runtime.ollama_service, "_start", self.start_service):
            with self.assertRaisesRegex(RuntimeError, "Remote provider error"):
                await self.runtime.send("chat-agent", {"text": "Only send once"})
        self.assertEqual(len(self.calls), 1)

    async def test_stopped_guest_model_stays_stopped_when_its_connectivity_agent_polls(self):
        guest = DesktopRuntime(Storage(os.path.join(self.directory.name, "guest"), MemoryVault()))
        self.addAsyncCleanup(guest.close)
        await guest.start()
        invitation = await self.runtime.invite("guest", self.runtime.active_url)
        await guest.join(invitation["url"], invitation["token"])
        await guest.save_agent({**self.profile, "id": "guest"})
        await guest.stop_agent("guest")
        with patch.object(guest.ollama_service, "models", side_effect=AssertionError("Stopped models must not start")) as catalog:
            await guest.refresh()
        catalog.assert_not_called()
        self.assertIn("guest", guest.runners)
        self.assertNotIn("guest", guest.model_runners)
        self.assertFalse(self.runtime.app.state.relay.agent_description("guest")["online"])

    async def test_missing_import_is_rejected_before_writing_profiles_or_keys(self):
        self.models = []
        before = dict(self.storage.settings)
        keys = dict(self.storage.vault.values)
        with self.assertRaisesRegex(ValueError, "not installed"):
            await self.runtime.save_agent(self.profile, "private-key")
        self.assertEqual(self.storage.settings, before)
        self.assertEqual(self.storage.vault.values, keys)
        self.assertNotIn("chat-agent", self.runtime.runners)

    async def test_restored_missing_or_unreachable_model_stays_offline_and_recovers_without_import(self):
        await self.runtime.save_agent(self.profile)
        for available, installed in ((False, ["chat:latest"]), (True, [])):
            with self.subTest(available=available, installed=installed):
                await self.runtime.close()
                self.ready, self.models = available, installed
                self.runtime = DesktopRuntime(Storage(self.directory.name, self.storage.vault))
                self.addAsyncCleanup(self.runtime.close)
                await self.runtime.start()
                self.assertIn("chat-agent", self.runtime.runners)
                self.assertFalse(self.runtime.app.state.relay.agent_description("chat-agent")["online"])
                self.ready, self.models = True, ["chat:latest"]
                await self.runtime.refresh()
                self.assertTrue(self.runtime.app.state.relay.agent_description("chat-agent")["online"])
                self.assertEqual(len(self.runtime.engine.storage.settings["agents"]), 1)

    async def test_workspaces_share_one_service_and_stopped_models_stay_stopped(self):
        await self.runtime.save_agent(self.profile)
        service = self.runtime.ollama_service
        await self.runtime.create_workspace("Second")
        self.assertIs(self.runtime.engine.ollama_service, service)
        self.assertTrue(all(engine.ollama_service is service for engine in self.runtime.engines.values()))
        await self.runtime.switch_workspace("local")
        await self.runtime.stop_agent("chat-agent")
        await self.runtime.close()
        self.runtime = DesktopRuntime(Storage(self.directory.name, self.storage.vault))
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()
        self.assertNotIn("chat-agent", self.runtime.runners)
        self.assertFalse(self.runtime.engine.storage.settings["agents"][0]["autostart"])

    async def test_import_and_next_launch_automatically_serve_the_saved_model(self):
        launches = []
        def start(service, address):
            self.ready = True
            process = Mock()
            process.poll.return_value = None
            def stop():
                self.ready = False
                process.poll.return_value = 0
            process.terminate.side_effect = stop
            service.processes[address] = process
            launches.append(process)
            return process

        with patch.object(OllamaService, "_start", start):
            self.ready = False
            await self.runtime.save_agent(self.profile)
            self.assertEqual(len(launches), 1)
            self.assertTrue(self.runtime.app.state.relay.agent_description("chat-agent")["online"])
            await self.runtime.close()
            self.assertFalse(self.ready)
            self.runtime = DesktopRuntime(Storage(self.directory.name, self.storage.vault))
            self.addAsyncCleanup(self.runtime.close)
            await self.runtime.start()
            self.assertEqual(len(launches), 2)
            self.assertTrue(self.runtime.app.state.relay.agent_description("chat-agent")["online"])
            self.assertEqual(len(self.runtime.engine.storage.settings["agents"]), 1)
