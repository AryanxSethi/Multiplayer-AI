"""Automatic local startup, ownership, failures, and saved model restoration."""
import asyncio
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
