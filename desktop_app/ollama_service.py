"""Start a local Ollama server on demand and own only processes we launch."""
import asyncio
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlsplit

import httpx


def model_installed(model, models):
    tagged = model if ":" in model.rsplit("/", 1)[-1] else model + ":latest"
    return model in models or tagged in models


class OllamaService:
    START_TIMEOUT = 10
    RETRY_DELAY = 15

    def __init__(self):
        self.processes = {}
        self.failures = {}
        self.lock = asyncio.Lock()
        self.closed = False

    @staticmethod
    def local_address(base_url):
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1", "::1")
                or url.path.strip("/") or url.username or url.password or url.query or url.fragment):
            return None
        host = "[::1]" if url.hostname == "::1" else "127.0.0.1"
        return f"{host}:{url.port or 80}"

    @staticmethod
    def executable():
        installed = shutil.which("ollama")
        if installed:
            return installed
        # A desktop app launched before installation may have an old PATH.
        if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
            installed = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe"
            if installed.is_file():
                return str(installed)
        raise RuntimeError("Install Ollama to start local models automatically, then try again.")

    async def _models(self, http, config):
        response = await http.get(config.base_url.rstrip("/") + "/api/tags",
            headers={"Authorization": "Bearer " + config.api_key} if config.api_key else {},
            timeout=2, follow_redirects=False)
        response.raise_for_status()
        return list(dict.fromkeys(item["name"] for item in response.json()["models"]))

    def _start(self, address):
        environment = {**os.environ, "OLLAMA_HOST": address}
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen([self.executable(), "serve"], env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        self.processes[address] = process
        return process

    async def models(self, http, config):
        """Check first; start only unreachable loopback HTTP API roots.

        Deliberately not cached. This was tried with a ten second TTL, on the
        grounds that the poll asks the same question every three seconds. That
        is true, and it is also the only way the app finds out that a model the
        reader just imported in Ollama is now there: caching the answer means a
        refresh after an import still reports it missing, which is precisely the
        recovery the "recovers without import" test guards. A loopback GET every
        few seconds is cheap, and buying it back with staleness is a bad trade.
        """
        if self.closed:
            raise RuntimeError("The local model service is shutting down.")
        try:
            return await self._models(http, config)
        except httpx.ConnectError:
            address = self.local_address(config.base_url)
            if address is None:
                raise
        async with self.lock:
            if self.closed:
                raise RuntimeError("The local model service is shutting down.")
            # Another workspace or discovery request may have started it.
            try:
                models = await self._models(http, config)
                return models
            except httpx.ConnectError:
                pass
            retry_at, message = self.failures.get(address, (0, ""))
            if time.monotonic() < retry_at:
                raise RuntimeError(message)
            try:
                process = self.processes.get(address)
                if process is None or process.poll() is not None:
                    process = self._start(address)
                async with asyncio.timeout(self.START_TIMEOUT):
                    while True:
                        try:
                            models = await self._models(http, config)
                            self.failures.pop(address, None)
                            return models
                        except httpx.ConnectError:
                            if process.poll() is not None:
                                raise RuntimeError("Ollama could not start. Check its installation and the API root.") from None
                            await asyncio.sleep(.2)
            except (OSError, RuntimeError, TimeoutError) as exc:
                message = str(exc) or "Ollama did not become ready. Check its installation and the API root."
                self.failures[address] = (time.monotonic() + self.RETRY_DELAY, message)
                raise RuntimeError(message) from None

    async def close(self):
        self.closed = True
        async with self.lock:
            processes = list(self.processes.values())
            self.processes.clear()
        for process in processes:
            if process.poll() is None:
                with contextlib.suppress(OSError):
                    process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        process.kill()
                    await asyncio.to_thread(process.wait)
