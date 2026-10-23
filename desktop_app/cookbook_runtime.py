"""Device-wide Ollama downloads and model lifecycle, independent of workspaces."""

import asyncio
import json
import re
import time

import httpx

from network_a2a.adapters.base import ProviderConfig

LOCAL_OLLAMA = "http://127.0.0.1:11434"
ACTIVE = {"starting", "downloading", "loading", "stopping"}


def check_model_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:\-]{0,511}", name):
        raise ValueError("Choose a valid Ollama model name.")
    return name


class CookbookRuntime:
    def __init__(self, service, emit, http=None):
        self.service = service
        self.emit = emit
        # Loopback requests must never follow a system proxy or redirects.
        self._http = http
        self.installed = []
        self.running = []
        self.jobs = {}
        self.tasks = {}
        self.error = ""
        self.closed = False

    @property
    def http(self):
        if self._http is None:
            self._http = httpx.AsyncClient(trust_env=False)
        return self._http

    def snapshot(self):
        return {"installed": list(self.installed), "running": list(self.running),
                "jobs": [dict(job) for job in self.jobs.values()], "error": self.error}

    def publish(self):
        self.emit("cookbook", self.snapshot())

    async def _json(self, method, path, **kwargs):
        timeout = kwargs.pop("timeout", 300)
        response = await self.http.request(method, LOCAL_OLLAMA + path,
                                           follow_redirects=False, timeout=timeout, **kwargs)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError(str(result.get("error", "Invalid Ollama response."))
                             if isinstance(result, dict) else "Invalid Ollama response.")
        return result

    async def status(self):
        """Read local state without starting Ollama or contacting a model host."""
        try:
            tags = await self._json("GET", "/api/tags", timeout=5)
            running = await self._json("GET", "/api/ps", timeout=5)
            if not isinstance(tags.get("models"), list) or not isinstance(running.get("models"), list):
                raise ValueError("Ollama returned an invalid model list.")
            self.installed = [m for m in tags["models"] if isinstance(m, dict) and isinstance(m.get("name"), str)]
            self.running = [m for m in running["models"] if isinstance(m, dict) and isinstance(m.get("name"), str)]
            self.error = ""
        except (httpx.HTTPError, ValueError) as exc:
            self.installed, self.running = [], []
            self.error = ("Ollama is unavailable at 127.0.0.1:11434. Install Ollama, "
                          "then download or run a model to start it. " + self._message(exc))
        self.publish()
        return self.snapshot()

    @staticmethod
    def _message(exc):
        if isinstance(exc, httpx.HTTPStatusError):
            return f"Ollama returned HTTP {exc.response.status_code}."
        if isinstance(exc, httpx.HTTPError):
            return "The local Ollama request failed."
        return str(exc) or "The operation failed."

    async def start(self, name, operation="download", context=4096):
        """Return immediately; jobs continue across page and workspace changes."""
        if self.closed:
            raise RuntimeError("The local model service is shutting down.")
        name = check_model_name(name)
        if operation not in {"download", "run", "stop"}:
            raise ValueError("Choose Download, Run, or Stop.")
        if type(context) is not int or not 512 <= context <= 131072:
            raise ValueError("Choose a context length between 512 and 131072 tokens.")
        if name in self.tasks:
            return self.snapshot()
        # Keep active jobs and a bounded history of completed ones.
        finished = [key for key, job in self.jobs.items() if job["state"] not in ACTIVE]
        for key in finished[:-19]:
            self.jobs.pop(key)
        self.jobs[name] = {"name": name, "operation": operation, "state": "starting",
                           "message": "Starting…", "completed": 0, "total": 0}
        self.tasks[name] = asyncio.create_task(self._execute(name, operation, context))
        self.publish()
        return self.snapshot()

    async def _execute(self, name, operation, context):
        job = self.jobs[name]
        try:
            config = ProviderConfig("ollama", name, LOCAL_OLLAMA)
            models = await self.service.models(self.http, config, retry=True)
            if operation == "download":
                job.update(state="downloading", message="Downloading…")
                self.publish()
                await self._pull(name, job)
            else:
                from .ollama_service import model_installed
                if not model_installed(name, models):
                    raise ValueError("Download this model before running it.")
                job.update(state="loading" if operation == "run" else "stopping",
                           message="Loading model…" if operation == "run" else "Stopping model…")
                self.publish()
                body = {"model": name, "stream": False,
                        "keep_alive": "10m" if operation == "run" else 0}
                if operation == "run":
                    body["options"] = {"num_ctx": context}
                result = await self._json("POST", "/api/generate", json=body)
                if result.get("done") is not True:
                    raise ValueError("Ollama did not confirm the operation completed.")
            job.update(state="complete", message={"download": "Downloaded", "run": "Running",
                                                   "stop": "Stopped"}[operation])
        except asyncio.CancelledError:
            job.update(state="cancelled", message="Cancelled. Download again to resume.")
            raise
        except Exception as exc:
            job.update(state="failed", message=self._message(exc))
        finally:
            try:
                if not self.closed:
                    if job["state"] == "cancelled":
                        self.publish()
                    else:
                        await self.status()
            finally:
                self.tasks.pop(name, None)

    async def _pull(self, name, job):
        last_update = 0
        confirmed = False
        async with self.http.stream("POST", LOCAL_OLLAMA + "/api/pull",
                                    json={"model": name, "stream": True},
                                    follow_redirects=False,
                                    timeout=httpx.Timeout(120, connect=10)) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("Ollama returned invalid download progress.")
                if record.get("error"):
                    raise ValueError(str(record["error"]))
                message = record.get("status", "Downloading…")
                if not isinstance(message, str):
                    raise ValueError("Ollama returned invalid download progress.")
                completed, total = record.get("completed", 0), record.get("total", 0)
                completed = completed if type(completed) is int and completed >= 0 else 0
                total = total if type(total) is int and total >= 0 else 0
                job.update(message=message, completed=completed, total=total)
                if message == "success":
                    confirmed = True
                if time.monotonic() - last_update >= .15 or confirmed:
                    self.publish()
                    last_update = time.monotonic()
        if not confirmed:
            raise ValueError("The download ended before Ollama confirmed success. Download again to resume.")

    async def cancel(self, name):
        task = self.tasks.get(name)
        if task is not None:
            if self.jobs[name]["operation"] != "download":
                raise ValueError("Wait for the model operation to finish.")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.pop(name, None)
            self.jobs[name].update(state="cancelled", message="Cancelled. Download again to resume.")
            self.publish()
        return self.snapshot()

    async def close(self):
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        if self._http is not None:
            await self._http.aclose()
