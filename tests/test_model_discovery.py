"""Provider model discovery uses each API's authentication and pagination."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from desktop_app.workspace_runtime import WorkspaceRuntime
from desktop_app.ollama_service import OllamaService
from tests.test_desktop_runtime import MemoryVault


class ModelDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, handler, profiles=()):
        runtime = WorkspaceRuntime.__new__(WorkspaceRuntime)
        runtime.remote = False
        runtime.storage = SimpleNamespace(settings={"agents": list(profiles)}, vault=MemoryVault())
        runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        runtime.ollama_service = OllamaService()
        runtime.model_runners = {profile["id"] for profile in profiles}
        runtime.ollama_service._start = Mock(side_effect=RuntimeError("Mock Ollama is unavailable"))
        self.addAsyncCleanup(runtime.http.aclose)
        return runtime

    async def test_ollama_outage_and_recovery_update_model_availability_without_replaying_work(self):
        reachable, requests, changes = True, [], []
        profiles = [{"id": "chat", "provider": "ollama", "model": "chat"},
                    {"id": "missing", "provider": "ollama", "model": "missing:latest"},
                    {"id": "stopped", "provider": "ollama", "model": "chat:latest"}]

        def provider(request):
            requests.append(request)
            self.assertEqual(request.url.path, "/api/tags")
            if not reachable:
                raise httpx.ConnectError("Ollama stopped", request=request)
            return httpx.Response(200, json={"models": [{"name": "chat:latest"}]})

        runtime = self.runtime(provider, profiles)
        runtime.generation = 0
        runtime.runners = {"chat": object(), "missing": object()}
        runtime.model_runners = set(runtime.runners)
        runtime._ollama_availability = {}
        async def publish(profile, running=True):
            changes.append((profile["id"], running))
            return True
        runtime.publish_profile = publish
        profiles = {profile["id"]: profile for profile in profiles}
        await runtime.refresh_ollama_availability(profiles)
        self.assertEqual(changes, [("chat", True), ("missing", False)])
        self.assertEqual(len(requests), 1, "Models at the same endpoint share one health check")
        await runtime.refresh_ollama_availability(profiles)
        self.assertEqual(len(changes), 2, "Unchanged health must not rewrite saved profiles")
        reachable = False
        await runtime.refresh_ollama_availability(profiles)
        self.assertEqual(changes[-1], ("chat", False))
        reachable = True
        await runtime.refresh_ollama_availability(profiles)
        self.assertEqual(changes[-1], ("chat", True))
        self.assertTrue(all(identity != "stopped" for identity, _ in changes))

    async def test_ollama_health_for_a_previous_workspace_is_discarded(self):
        runtime = self.runtime(lambda request: None)
        runtime.generation = 0
        runtime.runners = {"chat": object()}
        runtime.model_runners = set(runtime.runners)
        runtime._ollama_availability = {}
        changes = []
        def provider(request):
            runtime.generation += 1
            return httpx.Response(200, json={"models": [{"name": "chat:latest"}]})
        await runtime.http.aclose()
        runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        self.addAsyncCleanup(runtime.http.aclose)
        async def publish(profile, running=True):
            changes.append(running)
        runtime.publish_profile = publish
        await runtime.refresh_ollama_availability({"chat": {"id": "chat", "provider": "ollama", "model": "chat"}})
        self.assertEqual(changes, [])

    async def test_ollama_lists_every_installed_model_and_removes_duplicates(self):
        def provider(request):
            self.assertEqual(request.url.path, "/api/tags")
            return httpx.Response(200, json={"models": [{"name": name} for name in ("coder:latest", "chat:latest", "coder:latest")]})
        runtime = self.runtime(provider)
        self.assertEqual(await runtime.provider_models("ollama", "http://localhost:11434"), ["coder:latest", "chat:latest"])

    async def test_anthropic_uses_api_key_headers_and_collects_all_pages(self):
        seen = []
        def provider(request):
            seen.append(request)
            self.assertEqual(request.headers["x-api-key"], "mock-key")
            self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
            self.assertNotIn("authorization", request.headers)
            if request.url.params.get("after_id"):
                return httpx.Response(200, json={"data": [{"id": "claude-second"}], "has_more": False})
            return httpx.Response(200, json={"data": [{"id": "claude-first"}], "has_more": True, "last_id": "claude-first"})
        runtime = self.runtime(provider)
        self.assertEqual(await runtime.provider_models("anthropic", "https://api.anthropic.com/v1", "mock-key"),
                         ["claude-first", "claude-second"])
        self.assertEqual(seen[1].url.params["after_id"], "claude-first")

    async def test_gemini_paginates_and_only_offers_models_that_can_answer_chat(self):
        def provider(request):
            self.assertEqual(request.headers["x-goog-api-key"], "mock-key")
            if request.url.params.get("pageToken") == "next-page":
                return httpx.Response(200, json={"models": [{"name": "models/gemini-second", "supportedGenerationMethods": ["generateContent"]}]})
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-first", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding", "supportedGenerationMethods": ["embedContent"]}], "nextPageToken": "next-page"})
        runtime = self.runtime(provider)
        self.assertEqual(await runtime.provider_models("gemini", "https://generativelanguage.googleapis.com/v1beta", "mock-key"),
                         ["gemini-first", "gemini-second"])

    async def test_saved_key_is_reused_only_for_the_saved_provider_endpoint(self):
        seen = []
        def provider(request):
            seen.append(request)
            return httpx.Response(200, json={"data": [{"id": "available-model"}]})
        runtime = self.runtime(provider, [{"id": "saved", "provider": "openai-compatible", "base_url": "https://saved.example/v1"}])
        runtime.storage.vault.set("provider:saved", "saved-mock-key")
        await runtime.provider_models("openai-compatible", "https://saved.example/v1", agent_id="saved")
        await runtime.provider_models("openai-compatible", "https://changed.example/v1", agent_id="saved")
        self.assertEqual(seen[0].headers["authorization"], "Bearer saved-mock-key")
        self.assertNotIn("authorization", seen[1].headers)

    async def test_repeated_pagination_cursor_fails_instead_of_hanging_or_truncating(self):
        runtime = self.runtime(lambda request: httpx.Response(200, json={
            "data": [{"id": "one-model"}], "has_more": True, "last_id": "same-cursor"}))
        with self.assertRaisesRegex(ValueError, "repeated"):
            await runtime.provider_models("anthropic", "https://api.anthropic.com/v1", "mock-key")

    async def test_missing_pagination_cursor_does_not_silently_drop_models(self):
        runtime = self.runtime(lambda request: httpx.Response(200, json={"data": [{"id": "one-model"}], "has_more": True}))
        with self.assertRaisesRegex(ValueError, "missing"):
            await runtime.provider_models("anthropic", "https://api.anthropic.com/v1", "mock-key")
