"""Exercise real Ollama request shapes without downloading or loading a model."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from desktop_app.cookbook_runtime import CookbookRuntime, LOCAL_OLLAMA

NAME = "hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M"


def manager(handler):
    events = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = SimpleNamespace(models=AsyncMock(return_value=[NAME]))
    return CookbookRuntime(service, lambda kind, data: events.append((kind, data)), client), events


def local_response(request):
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": NAME, "size": 4000}]})
    if request.url.path == "/api/ps":
        return httpx.Response(200, json={"models": []})
    return httpx.Response(200, json={"done": True})


async def finish(runtime):
    await asyncio.gather(*list(runtime.tasks.values()))
    return runtime.snapshot()["jobs"][-1]


async def test_download_streams_progress_and_only_reports_complete_after_success():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/api/pull":
            return httpx.Response(200, content='\n'.join([
                '{"status":"pulling weights","total":4000,"completed":2000}',
                '{"status":"success"}',
            ]))
        return local_response(request)

    runtime, events = manager(handle)
    try:
        await runtime.start(NAME)
        assert (await finish(runtime))["state"] == "complete"
        assert any(data["jobs"][0]["completed"] == 2000 for _, data in events)
        assert runtime.installed[0]["name"] == NAME
        assert all(str(r.url).startswith(LOCAL_OLLAMA) for r in requests)
        assert json.loads(requests[0].content) == {"model": NAME, "stream": True}
    finally:
        await runtime.close()


@pytest.mark.parametrize("body", [
    '{"status":"pulling weights"}',
    '{"error":"disk full"}',
    'not json',
    '[]',
])
async def test_truncated_and_failed_downloads_are_retryable(body):
    runtime, _ = manager(lambda r: httpx.Response(200, content=body)
                         if r.url.path == "/api/pull" else local_response(r))
    try:
        await runtime.start(NAME)
        assert (await finish(runtime))["state"] == "failed"
        assert not runtime.tasks
        await runtime.start(NAME)
        assert (await finish(runtime))["state"] == "failed"
    finally:
        await runtime.close()


async def test_run_and_stop_use_the_selected_context_and_never_pull_implicitly():
    requests = []

    def handle(request):
        requests.append(request)
        return local_response(request)

    runtime, _ = manager(handle)
    try:
        await runtime.start(NAME, "run", 8192)
        assert (await finish(runtime))["message"] == "Running"
        body = json.loads(requests[0].content)
        assert body == {"model": NAME, "stream": False, "keep_alive": "10m",
                        "options": {"num_ctx": 8192}}
        await runtime.start(NAME, "stop")
        assert (await finish(runtime))["message"] == "Stopped"
        assert json.loads(requests[3].content)["keep_alive"] == 0
        assert not any(r.url.path == "/api/pull" for r in requests)
    finally:
        await runtime.close()


async def test_run_missing_model_fails_without_downloading():
    runtime, _ = manager(local_response)
    runtime.service.models.return_value = []
    try:
        await runtime.start(NAME, "run")
        job = await finish(runtime)
        assert job["state"] == "failed"
        assert "Download" in job["message"]
    finally:
        await runtime.close()


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        self.started.set()
        await asyncio.Event().wait()
        yield b''

    async def aclose(self):
        self.closed = True


async def test_duplicate_download_is_single_and_cancel_closes_the_stream():
    stream = WaitingStream()
    runtime, _ = manager(lambda r: httpx.Response(200, stream=stream)
                         if r.url.path == "/api/pull" else local_response(r))
    try:
        await runtime.start(NAME)
        await asyncio.wait_for(stream.started.wait(), 2)
        first = runtime.tasks[NAME]
        await runtime.start(NAME)
        assert runtime.tasks[NAME] is first
        await runtime.cancel(NAME)
        assert stream.closed
        assert not runtime.tasks
        assert runtime.jobs[NAME]["state"] == "cancelled"
    finally:
        await runtime.close()


async def test_immediate_cancel_and_shutdown_leave_no_background_jobs():
    runtime, _ = manager(local_response)
    await runtime.start(NAME)
    await runtime.cancel(NAME)
    assert runtime.jobs[NAME]["state"] == "cancelled"
    assert not runtime.tasks
    await runtime.start(NAME)
    task = runtime.tasks[NAME]
    await runtime.close()
    assert task.done()
    assert not runtime.tasks
    with pytest.raises(RuntimeError, match="shutting down"):
        await runtime.start(NAME)


async def test_local_status_is_read_only_and_surfaces_an_unavailable_service():
    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    runtime, _ = manager(offline)
    try:
        runtime.installed = [{"name": NAME}]
        state = await runtime.status()
        assert state["error"] and not state["installed"]
        runtime.service.models.assert_not_called()
    finally:
        await runtime.close()


async def test_download_survives_workspace_switch_and_shutdown_closes_its_stream(tmp_path, monkeypatch):
    from desktop_app.runtime import DesktopRuntime
    from desktop_app.storage import Storage
    from tests.test_desktop_runtime import MemoryVault

    runtime = DesktopRuntime(Storage(tmp_path, MemoryVault()))
    stream = WaitingStream()
    runtime.cookbook._http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)))
    monkeypatch.setattr(runtime.ollama_service, "models", AsyncMock(return_value=[]))
    try:
        await runtime.start()
        await runtime.cookbook_start(NAME)
        await asyncio.wait_for(stream.started.wait(), 2)
        task = runtime.cookbook.tasks[NAME]
        await runtime.create_workspace("Another workspace")
        await runtime.switch_workspace("local")
        assert runtime.cookbook.tasks[NAME] is task
        assert not task.done()
    finally:
        await runtime.close()
    assert stream.closed
    assert not runtime.cookbook.tasks


@pytest.mark.parametrize("name,operation,context", [
    ("bad name", "download", 4096), (NAME, "delete", 4096),
    (NAME, "run", True), (NAME, "run", 999999),
])
async def test_invalid_operations_never_reach_ollama(name, operation, context):
    runtime, _ = manager(local_response)
    try:
        with pytest.raises(ValueError):
            await runtime.start(name, operation, context)
        runtime.service.models.assert_not_called()
        assert not runtime.tasks
    finally:
        await runtime.close()
