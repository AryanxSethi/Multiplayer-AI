"""Async networking owned by the desktop process; contains no Qt dependencies."""
import asyncio
import contextlib
import json
import random
import re
import secrets
import socket
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import uvicorn

from network_a2a.adapters import PROVIDERS, ProviderConfig, ProviderError, create_adapter
from network_a2a.client import AgentClient, direct_relay_connection
from network_a2a.server import Relay, create_app
from network_a2a.persistence import HistoryStore
from network_a2a.content import MAX_FRAME_BYTES, content_summary
from network_a2a.web_search import create_search
from network_a2a.orchestration import GENERAL_TARGET, ORCHESTRATION_TIMEOUT, routing_profile
from network_a2a.chat_commands import HELP_TEXT, MODEL_ROLES, parse_command, resolve_identity

from .storage import UNAVAILABLE
from .ollama_service import OllamaService, model_installed


def relay_http_url(url, allow_insecure=False):
    # Reuse the device client's transport validation and enforce the endpoint.
    AgentClient(url, "unused", allow_insecure=allow_insecure)
    parsed = urlsplit(url)
    if parsed.path != "/connect" or parsed.query or parsed.fragment:
        raise ValueError("Relay URL must end in /connect without query parameters")
    return urlunsplit(("https" if parsed.scheme == "wss" else "http", parsed.netloc, "", "", ""))


class WorkspaceRuntime:
    def __init__(self, storage, emit=lambda event, data: None, restore_remote=True, ollama_service=None):
        self.storage = storage
        self.emit = emit
        self.restore_remote = restore_remote
        self.history_store = HistoryStore(storage.directory)
        self.credentials = {}
        self.app = None
        self.server = None
        self.server_task = None
        self.server_socket = None
        self.http = None
        self.direct_http = None
        self.runners = {}
        self.model_runners = set()
        self.active_url = None
        self.active_token = None
        self.active_id = None
        self.remote = False
        self.sharing = False
        # How long the poll waits before trying again, and how many attempts
        # have failed in a row. On the instance rather than local to _poll, so
        # that reconnecting can clear them: a device that was backing off
        # because the other end went away should start listening again the
        # moment it is reachable, not sit out a backoff earned by a fault that
        # has already been repaired.
        self._poll_delay = 3
        # Set to cut the poll's wait short. The loop reads the delay at the top
        # of a pass and sleeps it at the bottom, so clearing the stored delay
        # while it is already asleep changes nothing about that wait: on a relay
        # that had backed off, a reader pressing Retry would get one manual
        # refresh and then five more minutes of silence from the loop.
        self._poll_wake = asyncio.Event()
        self.port = None
        self.poller = None
        self.closed = False
        self.generation = 0
        self._ollama_availability = {}
        self.mutation = asyncio.Lock()
        self.ollama_service = ollama_service if ollama_service is not None else OllamaService()
        self.owns_ollama_service = ollama_service is None

    async def start(self):
        """Recover unfinished model saves before loading credentials or starting the relay."""
        self.storage.recover_agent_save()
        self.http = httpx.AsyncClient(timeout=65, follow_redirects=False)
        self.direct_http = httpx.AsyncClient(timeout=65, follow_redirects=False, trust_env=False)
        self.credentials = self.storage.credentials() or {}
        # Recorded by Storage.credentials() before it dropped anything, so
        # it has to be read before a new token is minted below.
        recovered = list(getattr(self.storage, "dropped_identities", []))
        device_id = self.storage.settings.get("device_id")
        if not device_id:
            device_id = "device-" + uuid4().hex[:8]
            self.storage.update(device_id=device_id)
        if device_id not in self.credentials:
            self.credentials[device_id] = {"token": secrets.token_urlsafe(32), "group": "workspace"}
        Relay(self.credentials)
        self.storage.save_credentials(self.credentials)
        self.storage.save()
        if recovered:
            # The device carries on with a new token, but any workspace
            # joined under the old one no longer recognises it, so this is
            # worth saying rather than leaving the user to wonder why
            # their shared workspace stopped connecting.
            self.emit("notice", "This device's saved identity was missing from the "
                                "credential store, so a new one was created. Workspaces "
                                "joined with the old identity need a new invitation.")
        self.app = create_app(self.credentials, conversation_store=self.history_store, workspace={
            "id": self.storage.settings.get("workspace_id", "local"),
            "name": self.storage.settings.get("workspace_name", "My workspace"),
            "owner": device_id, "models": [profile["id"] for profile in self.storage.settings.get("agents", [])],
            "coordinator": self.storage.settings.get("coordinator"),
            "agent_profiles": {profile["id"]: self.public_profile(profile, running=False)
                               for profile in self.storage.settings.get("agents", [])}})
        self.app.state.relay.member_remover = self.remove_member
        self.app.state.relay.model_preparer = self.prepare_models
        self.sharing = bool(self.storage.settings.get("share_lan", False))
        await self._start_server("0.0.0.0" if self.sharing else "127.0.0.1", self.storage.settings.get("relay_port", 0))
        self.storage.update(relay_port=self.port)
        saved = self.storage.settings.get("remote") if self.restore_remote else None
        await self.use_local(forget_remote=False)
        if saved:
            try:
                token = self.storage.vault.get("remote-token")
                if token:
                    await self.join(saved["url"], token, saved.get("allow_insecure", False), save=False)
            except Exception:
                self.emit("notice", "Saved workspace is unavailable. Your local workspace is ready; you can reconnect from Join workspace.")
        self.poller = asyncio.create_task(self._poll())
        self.emit("ready", {"port": self.port, "device_id": device_id})

    async def _start_server(self, host, port):
        sock = socket.socket()
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            self.port = sock.getsockname()[1]
            self.server_socket = sock
            config = uvicorn.Config(self.app, log_level="error", ws_max_size=MAX_FRAME_BYTES, ws_ping_interval=20, ws_ping_timeout=20,
                                    proxy_headers=False, timeout_graceful_shutdown=5, log_config=None)
            self.server = uvicorn.Server(config)
            self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
            async with asyncio.timeout(10):
                while not self.server.started:
                    if self.server_task.done():
                        await self.server_task
                        raise RuntimeError("Local relay could not start")
                    await asyncio.sleep(.02)
        except BaseException:
            sock.close()
            raise

    async def _stop_agents(self):
        self.generation += 1
        entries = list(self.runners.values())
        self.runners.clear()
        self.model_runners.clear()
        self._ollama_availability.clear()
        owned = {client.agent_id for client, _ in entries}
        for client, _ in entries:
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
        for client, task in entries:
            task.cancel()
        await asyncio.gather(*(task for _, task in entries), return_exceptions=True)
        for client, _ in entries:
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
        # Cancellation must release registered sockets before identities are reused.
        if self.active_url and not self.remote:
            for _ in range(100):
                if not owned.intersection(self.app.state.relay.peers):
                    break
                await asyncio.sleep(.02)

    async def _attach(self, agent_id, token, handler, allow_insecure=False):
        generation = self.generation
        async def visible_handler(payload, source, shared=False):
            incoming = not shared and source != self.active_id and generation == self.generation
            if incoming:
                text = payload.get("text") if isinstance(payload, dict) else payload
                if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
                    messages = payload["messages"]
                    text = messages[-1].get("content") if messages and isinstance(messages[-1], dict) else text
                if isinstance(text, list):
                    text = content_summary(text)
                elif not isinstance(text, str):
                    text = json.dumps(payload)
                self.emit("incoming", {"from": source, "to": agent_id, "text": text})
            try:
                result = await handler(payload, source)
            except Exception:
                if incoming and generation == self.generation:
                    self.emit("incoming_reply", {"from": source, "to": agent_id,
                                                "text": "The local agent could not complete this request.", "error": True})
                raise
            if incoming and generation == self.generation:
                text = result.get("text") if isinstance(result, dict) else result
                self.emit("incoming_reply", {"from": source, "to": agent_id,
                                            "text": text if isinstance(text, str) else json.dumps(result)})
            return result
        async def handle_frame(frame):
            return await visible_handler(frame["payload"], frame["from"], bool(frame.get("conversation_id")))
        client = AgentClient(self.active_url, token, visible_handler, allow_insecure=allow_insecure, frame_handler=handle_frame)
        task = asyncio.create_task(client.run())
        self.runners[agent_id] = (client, task)
        waiter = asyncio.create_task(client.ready.wait())
        try:
            done, _ = await asyncio.wait([task, waiter], timeout=12, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
                raise ConnectionError("Agent connection closed")
            if waiter not in done:
                raise TimeoutError("Agent did not connect. Check its token and relay address.")
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.runners.pop(agent_id, None)
            raise
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def _echo(self, payload, source):
        text = payload.get("text") if isinstance(payload, dict) else payload
        if not isinstance(text, str):
            text = json.dumps(payload)
        return {"text": f"This device received your message from {source}:\n\n{text}\n\nConfigure a model in Providers to turn this connectivity agent into an AI agent."}

    async def use_local(self, forget_remote=True):
        async with self.mutation:
            await self._use_local(forget_remote)

    async def _use_local(self, forget_remote=True):
        """Switch to the local workspace, assuming ``mutation`` is held.

        Split out because asyncio.Lock is not reentrant: reset_identity
        already holds it when it needs to return to the local workspace,
        and calling use_local from there would deadlock.
        """
        await self._stop_agents()
        self.remote = False
        self.active_url = f"ws://127.0.0.1:{self.port}/connect"
        self.active_id = self.storage.settings["device_id"]
        self.active_token = self.credentials[self.active_id]["token"]
        if forget_remote:
            self.storage.discard("remote")
        await self._attach(self.active_id, self.active_token, self._echo)
        await self._restore_profiles()
        self.emit("workspace", {"name": self.storage.settings.get("workspace_name", "My workspace"), "url": self.active_url, "self": self.active_id, "remote": False})
        await self.refresh()

    async def reset_identity(self):
        """Replace every identity in this workspace with a fresh token.

        Reachable from Settings. Useful when a credential is suspected to
        be exposed, or when the credential store has been partly lost and
        the leftovers are not wanted.

        The replacement is written before anything is deleted, so a store
        that refuses the write leaves the existing identity, the in-memory
        credentials and the relay exactly as they were. Only agents held
        back until the new token is durable, so a failure also leaves the
        running agents alone.

        Provider profiles get new tokens too, because _restore_profiles
        only relaunches an agent whose id is present in the credential
        set; dropping them would silently stop every model agent.
        """
        async with self.mutation:
            removed = list(self.storage.settings.get("identities", {}) or {})
            device_id = self.storage.settings.get("device_id")
            if not device_id:
                device_id = "device-" + uuid4().hex[:8]
                self.storage.update(device_id=device_id)
            fresh = {device_id: {"token": secrets.token_urlsafe(32), "group": "workspace"}}
            skipped = []
            for profile in self.storage.settings.get("agents", []):
                agent_id = profile.get("id")
                # A profile may name the device identity, which already has
                # a token above; a second entry would overwrite it.
                if agent_id == device_id or agent_id in fresh:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id or ""):
                    skipped.append(agent_id)
                    continue
                fresh[agent_id] = {"token": secrets.token_urlsafe(32), "group": "workspace"}
            Relay(fresh)
            self.storage.save_credentials(fresh)
            # Forget only what is genuinely superseded. Everything in fresh
            # was just written, so filtering on device_id alone deleted a
            # reissued provider profile's token moments after saving it,
            # leaving that identity present in memory but gone from the
            # vault and from settings, and so lost on the next launch.
            superseded = [i for i in removed if i not in fresh]
            forgotten, undeleted = self.storage.forget_identities(superseded)
            await self._stop_agents()
            self.credentials = fresh
            if self.app is not None:
                self.app.state.relay.credentials = fresh
            # Counted from the same set, so a reissued provider profile is
            # not reported to the user as a newly joined identity.
            joined = len(superseded)
            self.emit("notice", f"Replaced {len(removed)} saved "
                                f"{'identity' if len(removed) == 1 else 'identities'}. "
                                "Rejoin any shared workspace with a new invitation.")
            if skipped:
                # Skipping is better than refusing the reset outright, but the
                # user should know which agents did not get a new token.
                self.emit("notice", "Could not reissue an identity for "
                                    f"{', '.join(str(s) for s in skipped)}. Open Providers "
                                    "to rename and reconnect those agents.")
            if undeleted:
                # forget_identities has already recorded the names, before the
                # await above could be cancelled. All that is left is to say
                # so: silence would leave the user believing a clean removal.
                self.emit("notice", f"{len(undeleted)} old "
                                    f"{'token' if len(undeleted) == 1 else 'tokens'} "
                                    "could not be removed from the credential store. "
                                    "Unlock it and restart; the app will try again.")
            await self._use_local(forget_remote=True)
            return {"removed": removed, "device_id": device_id, "skipped": skipped,
                    "reissued": sorted(i for i in fresh if i != device_id),
                    "joined": joined, "undeleted": undeleted,
                    "forgotten": forgotten}

    async def _restore_profiles(self):
        for profile in self.storage.settings.get("agents", []):
            if profile.get("autostart", True) and profile["id"] in self.credentials:
                try:
                    await self._launch_profile(profile)
                except Exception:
                    self.emit("notice", f"Could not restore {profile['id']}. Open Providers to check its configuration.")

    def relay_http(self, url=None):
        return self.direct_http if direct_relay_connection(url or self.active_url) else self.http

    async def inspect_invitation(self, url, token, allow_insecure=False, conversation_id=None):
        if conversation_id is not None and (not isinstance(conversation_id, str)
                or not re.fullmatch(r"conversation-[0-9a-f]{32}", conversation_id)):
            raise ValueError("Use a valid shared conversation invitation")
        base = relay_http_url(url, allow_insecure)
        http = self.relay_http(url)
        address = urlsplit(url).netloc
        help_text = ("Keep the host app open, select its Wi-Fi or hotspot address in Invite a device, "
                     "and allow Multiplayer AI through the host firewall. Campus or guest Wi-Fi and some "
                     "hotspots block connections between devices, even with the same Wi-Fi name. "
                     "Use a network that allows device-to-device connections or a reachable WSS relay.")
        try:
            response = await http.get(base + "/agents", headers={"Authorization": "Bearer " + token}, timeout=10)
            if response.status_code == 200 and conversation_id:
                conversation = await http.get(base + f"/conversations/{conversation_id}",
                    headers={"Authorization": "Bearer " + token}, timeout=10)
                if conversation.status_code != 200:
                    raise ValueError("This shared conversation is unavailable. Keep the host app open and ask for a new conversation invitation.")
        except httpx.ConnectTimeout:
            raise ConnectionError(f"Could not reach the host at {address} within 10 seconds. " + help_text) from None
        except httpx.TimeoutException:
            raise ConnectionError(f"The relay at {address} did not respond within 10 seconds. " + help_text) from None
        except httpx.HTTPError:
            raise ConnectionError(f"Could not connect to the relay at {address}. " + help_text) from None
        if response.status_code != 200:
            raise ValueError("Relay rejected this invitation. Check the token and address.")
        try:
            result = response.json()
        except ValueError:
            raise ValueError("This address did not return a relay response. Use the host's invitation URL ending in /connect.") from None
        if not isinstance(result, dict) or not isinstance(result.get("self"), str):
            raise ValueError("This relay does not support desktop identity discovery")
        same_connection = self.remote and self.active_url == url and self.active_token == token
        if not same_connection and any(item.get("id") == result["self"] and item.get("online") for item in result.get("agents", [])):
            raise ValueError("This invitation's device identity is already connected. Ask the host to create a new invitation with a unique device name for this laptop.")
        return result

    async def join(self, url, token, allow_insecure=False, save=True, conversation_id=None):
        async with self.mutation:
            result = await self.inspect_invitation(url, token, allow_insecure, conversation_id)
            # The relay answered, so any backoff was earned by an outage that
            # has now been repaired. Left in place it would keep the poll quiet
            # for up to five minutes on a device that is already connected --
            # which is how a guest that had just rejoined saw no agents at all.
            self._poll_delay = 3
            if self.remote and self.active_url == url and self.active_token == token:
                await self.refresh()
                if conversation_id:
                    self.emit("conversation_joined", conversation_id)
                return
            previous = (self.active_url, self.active_token, self.active_id, self.remote)
            await self._stop_agents()
            self.active_url, self.active_token, self.active_id, self.remote = url, token, result["self"], True
            try:
                await self._attach(self.active_id, token, self._echo, allow_insecure)
                remote_profile = self.storage.settings.get("remote_agent")
                if remote_profile and remote_profile.get("autostart") and remote_profile["id"] == self.active_id and remote_profile.get("relay") == url:
                    await self._launch_profile(remote_profile, allow_insecure)
                elif remote_profile and remote_profile["id"] == self.active_id and remote_profile.get("relay") == url:
                    await self.publish_profile(remote_profile, running=False)
                if save:
                    self.storage.vault.set("remote-token", token)
                    self.storage.update(remote={"url": url, "allow_insecure": allow_insecure})
            except BaseException:
                await self._stop_agents()
                self.active_url, self.active_token, self.active_id, self.remote = previous
                if previous[0]:
                    await self._attach(self.active_id, self.active_token, self._echo,
                                       allow_insecure=previous[3] and urlsplit(previous[0]).scheme == "ws")
                    if not self.remote:
                        await self._restore_profiles()
                raise
            self.emit("workspace", {"name": "Shared workspace", "url": url, "self": self.active_id, "remote": True})
            await self.refresh()
            if conversation_id:
                self.emit("conversation_joined", conversation_id)

    async def _remove_runner(self, agent_id):
        """Disconnect and cancel an agent runner, then wait for its task to finish."""
        self.model_runners.discard(agent_id)
        self._ollama_availability.pop(agent_id, None)
        entry = self.runners.pop(agent_id, None)
        if entry:
            client, task = entry
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Explicitly close before a new connection reuses the same identity.
            if client.socket:
                with contextlib.suppress(Exception):
                    await client.socket.close()
            # Wait for the relay to drop the old registration, rather than for
            # a fixed interval. This used to sleep 100ms, which is long enough
            # most of the time and not the rest: when the peer took longer than
            # that to go, the relay refused the replacement as a duplicate
            # identity, and the editor was left with a model that had been
            # saved, was listed as online, and was not running -- so every send
            # came back "Target agent is offline". Editing a profile reuses its
            # identity, which is why only the edit path hit it.
            #
            # The same drain _stop_agents does, twenty lines below, for the
            # same stated reason.
            if self.active_url and not self.remote:
                for _ in range(100):
                    if agent_id not in self.app.state.relay.peers:
                        break
                    await asyncio.sleep(.02)

    def _config(self, profile, key, search_key=None):
        """Build validated provider settings from a profile and its separate vault keys."""
        spec = PROVIDERS[profile["provider"]]
        return ProviderConfig(provider=profile["provider"], model=profile["model"], base_url=profile.get("base_url") or spec.base_url,
                              api_key=key, system_prompt=profile.get("system_prompt"), allow_insecure=profile.get("allow_insecure", False),
                              vision=profile.get("vision", False), web_search=profile.get("web_search", "off"),
                              searxng_url=profile.get("searxng_url", ""), searxng_allow_insecure=profile.get("searxng_allow_insecure", False),
                              search_provider=profile.get("search_provider", "searxng"), search_api_key=search_key)

    @staticmethod
    def public_profile(profile, running=True):
        """Return model metadata suitable for sharing without credentials or endpoints."""
        return {"provider": profile["provider"], "model": profile["model"], "vision": profile.get("vision", False),
                "running": running, **routing_profile(profile)}

    async def publish_profile(self, profile, running=True):
        public = self.public_profile(profile, running)
        if not self.remote:
            self.app.state.relay.set_agent_profile(profile["id"], public)
            return True
        else:
            base = relay_http_url(self.active_url, allow_insecure=True)
            try:
                response = await self.relay_http().post(base + "/agent-profile", json=public,
                    headers={"Authorization": "Bearer " + self.active_token}, timeout=5)
                if response.status_code in (200, 404):
                    return True
            except httpx.HTTPError:
                pass
            if not self.closed:
                self.emit("notice", "Your model connected, but its details could not be published to the workspace.")
            return False

    async def _launch_profile(self, profile, allow_insecure=False, ollama_available=None):
        """Load vault credentials and replace the profile's running model client."""
        key = self.storage.vault.get("provider:" + profile["id"])
        search_key = self.storage.vault.get("search:" + profile["id"]) if profile.get("search_provider") == "ollama" and profile.get("web_search", "off") != "off" else None
        config = self._config(profile, key, search_key)
        adapter = create_adapter(config, self.http)
        handler = adapter
        if profile["provider"] == "ollama":
            async def handler(payload, source):
                # Check before generation, including requests arriving from another
                # device. A failed generation is never retried or replayed.
                try:
                    await self.ollama_service.ensure_model(self.http, config, retry=True)
                except (RuntimeError, ValueError) as exc:
                    raise ProviderError("connection", str(exc)) from None
                except (httpx.HTTPError, OSError, KeyError, TypeError):
                    raise ProviderError("connection", "Ollama is unavailable. Check its installation and model settings, then try again.") from None
                return await adapter(payload, source)
        if profile["provider"] == "ollama" and ollama_available is None:
            try:
                models = await self.ollama_service.models(self.http, config)
                ollama_available = model_installed(profile["model"], models)
                if not ollama_available:
                    self.emit("notice", f"{profile['model']} is not installed in Ollama. Install it in Ollama; this saved connection will recover automatically.")
            except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
                ollama_available = False
                self.emit("notice", f"{profile['id']} is unavailable. {exc} Its saved connection will recover automatically when Ollama is ready.")
        token = self.active_token if self.remote else self.credentials[profile["id"]]["token"]
        await self._remove_runner(profile["id"])
        await self.publish_profile(profile, running=False)
        await self._attach(profile["id"], token, handler, allow_insecure)
        self.model_runners.add(profile["id"])
        running = ollama_available if profile["provider"] == "ollama" else True
        published = await self.publish_profile(profile, running=running)
        if profile["provider"] == "ollama":
            if published:
                self._ollama_availability[profile["id"]] = running
            else:
                self._ollama_availability.pop(profile["id"], None)
        configured = self.storage.settings.get("coordinator")
        if not self.remote and (not configured or not self.app.state.relay.allowed(self.active_id, configured)
                                or self.app.state.relay.agent_description(configured)["kind"] != "model"):
            default = next((item["id"] for item in self.storage.settings.get("agents", [])
                            if self.app.state.relay.allowed(self.active_id, item["id"])), profile["id"])
            previous = self.storage.settings.get("coordinator")
            try:
                self.storage.update(coordinator=default)
            except BaseException:
                # Put the one key back, in place. Restoring the whole dict by
                # rebinding it would leave any reader holding the old object
                # looking at settings nothing writes to again.
                with contextlib.suppress(Exception):
                    if previous is None:
                        self.storage.discard("coordinator")
                    else:
                        self.storage.update(coordinator=previous)
                raise
            self.app.state.relay.workspace["coordinator"] = default

    async def save_agent(self, profile, key=None, search_key=None):
        """Validate and transactionally persist a model and its keys before restarting it."""
        async with self.mutation:
            self.storage.recover_agent_save()
            profile = {**profile, **routing_profile(profile)}
            agent_id = profile["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id):
                raise ValueError("Agent names need 1–64 letters, numbers, underscores, or hyphens")
            if self.remote and agent_id != self.active_id:
                raise ValueError("Use your invited identity for an agent in a shared workspace")
            if not self.remote and agent_id == self.active_id:
                raise ValueError("Choose a different name for your AI agent; the device identity stays available")
            saved_key = key if key else self.storage.vault.get("provider:" + agent_id)
            saved_search_key = search_key or (self.storage.vault.get("search:" + agent_id) if profile.get("search_provider") == "ollama" and profile.get("web_search", "off") != "off" else None)
            config = self._config(profile, saved_key, saved_search_key)
            create_adapter(config, self.http)  # validate before mutating storage
            if profile["provider"] == "ollama":
                await self.ollama_service.ensure_model(self.http, config, retry=True)
            updated = self.credentials
            if not self.remote:
                if agent_id not in self.credentials:
                    updated = {**self.credentials, agent_id: {"token": secrets.token_urlsafe(32), "group": "workspace"}}
                    Relay(updated)
            saved = {**profile, "relay": self.active_url} if self.remote else profile
            finalized = self.storage.save_agent_profile(saved, updated, key, search_key, self.remote)
            if not self.remote:
                self.credentials = updated
                self.app.state.relay.credentials = updated
                self.app.state.relay.workspace["models"] = [item["id"] for item in self.storage.settings.get("agents", [])]
            if not finalized:
                self.emit("notice", "The model was saved, but its saved credentials need another check. Unlock your credential store and restart the app to finish it.")
            await self._launch_profile(profile, self.storage.settings.get("remote", {}).get("allow_insecure", False),
                                       ollama_available=True if profile["provider"] == "ollama" else None)
            self.emit("activity", {"title": f"{agent_id} connected", "detail": f"{profile['provider']} · {profile['model']}"})
            await self.refresh()

    async def stop_agent(self, agent_id):
        async with self.mutation:
            if agent_id == self.storage.settings["device_id"] and not self.remote:
                raise ValueError("The device connectivity agent runs while the app is open")
            if agent_id not in self.runners:
                raise ValueError("You can stop only an agent running on this device")
            await self._remove_runner(agent_id)
            if self.remote:
                if self.storage.settings.get("remote_agent"):
                    self.storage.settings["remote_agent"]["autostart"] = False
            else:
                for profile in self.storage.settings.get("agents", []):
                    if profile["id"] == agent_id:
                        profile["autostart"] = False
            # Persist the stop and publish the offline profile before the
            # network reconnect, which can fail or be cancelled.
            self.storage.save()
            profile = self.storage.settings.get("remote_agent") if self.remote else next(
                (profile for profile in self.storage.settings.get("agents", []) if profile["id"] == agent_id), None)
            if profile:
                await self.publish_profile(profile, running=False)
            if self.remote:
                await self._attach(self.active_id, self.active_token, self._echo,
                                   self.storage.settings.get("remote", {}).get("allow_insecure", False))
            await self.refresh()

    async def prepare_models(self, source, target=GENERAL_TARGET):
        """Recover local Ollama models before routing an explicit chat request."""
        saved = [self.storage.settings.get("remote_agent")] if self.remote else self.storage.settings.get("agents", [])
        if not any(profile and profile.get("provider") == "ollama"
                   and (profile["id"] in self.model_runners if target == GENERAL_TARGET else profile["id"] == target)
                   and OllamaService.local_address(profile.get("base_url") or PROVIDERS["ollama"].base_url)
                   for profile in saved):
            return
        generation = self.generation
        async with self.mutation:
            if self.closed or generation != self.generation:
                raise ConnectionError("Workspace changed while starting the model; the request was not sent")
            if self.remote:
                if source != self.active_id:
                    return
                profile = self.storage.settings.get("remote_agent")
                profiles = [profile] if profile and profile.get("relay") == self.active_url else []
            else:
                profiles = self.storage.settings.get("agents", [])
            failures = []
            changed = False
            for profile in profiles:
                identity = profile["id"]
                base = profile.get("base_url") or PROVIDERS["ollama"].base_url
                if profile["provider"] != "ollama" or OllamaService.local_address(base) is None:
                    continue
                if not self.remote and not self.app.state.relay.allowed(source, identity):
                    continue
                if target == GENERAL_TARGET:
                    if identity not in self.model_runners:
                        continue  # A manual stop stays stopped in automatic routing.
                    if not self.remote and not self.app.state.relay.orchestration_authorized(identity):
                        continue
                elif target != identity:
                    continue
                runner = self.runners.get(identity)
                connected = bool(runner and runner[0].ready.is_set() and not runner[1].done())
                if connected and self._ollama_availability.get(identity):
                    continue
                if not connected or not self._ollama_availability.get(identity):
                    self.emit("activity", {"title": "Starting Ollama", "detail": profile["model"]})
                try:
                    key = self.storage.vault.get("provider:" + identity)
                    config = ProviderConfig("ollama", profile["model"], base, api_key=key,
                                            allow_insecure=profile.get("allow_insecure", False))
                    await self.ollama_service.ensure_model(self.http, config, retry=True)
                    if self.closed or generation != self.generation:
                        raise ConnectionError("Workspace changed while starting the model; the request was not sent")
                    if not connected or identity not in self.model_runners:
                        await self._launch_profile(profile, self.storage.settings.get("remote", {}).get("allow_insecure", False),
                                                   ollama_available=True)
                        changed = True
                    elif not self._ollama_availability.get(identity):
                        if await self.publish_profile(profile, running=True):
                            self._ollama_availability[identity] = True
                        changed = True
                except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
                    if self.closed or generation != self.generation:
                        raise ConnectionError("Workspace changed while starting the model; the request was not sent") from None
                    message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "Ollama is unavailable. Check its installation and model settings, then try again."
                    if target != GENERAL_TARGET:
                        raise ConnectionError(message) from None
                    failures.append(message)
            if changed:
                self._poll_wake.set()
            if failures and not self.remote and not any(model["online"] for model in self.app.state.relay.orchestrator.models(source)):
                raise ConnectionError(failures[0])

    async def refresh_ollama_availability(self, profiles):
        """Publish actual provider availability, independently of relay connectivity."""
        generation = self.generation
        active = {identity: profile for identity, profile in profiles.items()
                  if profile.get("provider") == "ollama" and identity in self.model_runners}
        runners = {identity: self.runners[identity] for identity in active}
        endpoints = {}
        for identity, profile in active.items():
            # Guarded, because Vault.get raises when the OS credential store is
            # locked or unavailable. It used to be read outside any try, so the
            # error escaped refresh() into the poll loop above it, which
            # reported the failure as "Relay connection interrupted" -- telling
            # the reader their relay had gone when their keyring was the thing
            # that had. It then repeated every three seconds.
            #
            # No key means no Ollama account, which is the same answer as a
            # probe that could not reach the server: nothing available.
            try:
                key = self.storage.vault.get("provider:" + identity)
            except (RuntimeError, OSError):
                key = None
            endpoint = (profile.get("base_url") or PROVIDERS["ollama"].base_url,
                        key, profile.get("allow_insecure", False))
            endpoints.setdefault(endpoint, []).append(identity)

        async def available(endpoint):
            base, key, insecure = endpoint
            try:
                config = ProviderConfig("ollama", "availability", base, api_key=key, allow_insecure=insecure)
                return await self.ollama_service.models(self.http, config)
            except (httpx.HTTPError, OSError, RuntimeError, ValueError, KeyError, TypeError):
                return set()

        results = await asyncio.gather(*(available(endpoint) for endpoint in endpoints))
        if generation != self.generation:
            return {}
        availability = {}
        for identities, models in zip(endpoints.values(), results):
            for identity in identities:
                if self.runners.get(identity) is not runners[identity]:
                    continue
                running = model_installed(active[identity]["model"], models)
                availability[identity] = running
                if self._ollama_availability.get(identity) != running:
                    if await self.publish_profile(active[identity], running=running):
                        self._ollama_availability[identity] = running
        return availability

    async def retry_poll(self):
        """One refresh now, because the reader asked for one.

        The poll backs off to a five minute ceiling while the relay is gone, so
        a host that has just come back would otherwise stay unnoticed until
        that elapsed. Clearing the stored delay is not enough on its own: the
        loop is asleep in the delay it read at the top of the pass it is on, so
        this runs one pass, resets the backoff if that succeeded, and wakes the
        loop so it resumes on the new cadence rather than the old one. A pass
        that fails leaves the backoff alone and reports offline, exactly as the
        loop would have: Retry is not a way to hammer a relay that is still gone.
        """
        try:
            await self.refresh()
            self._poll_delay = 3
            self._poll_wake.set()
        except Exception:
            self.emit("offline", "Relay connection interrupted. The app will keep trying to reconnect.")

    async def refresh(self):
        if not self.active_token:
            return
        generation = self.generation
        base = relay_http_url(self.active_url, allow_insecure=True)
        http = self.relay_http()
        profiles = {p["id"]: p for p in self.storage.settings.get("agents", [])}
        remote_profile = self.storage.settings.get("remote_agent")
        if self.remote:
            profiles = {remote_profile["id"]: remote_profile} if remote_profile and remote_profile.get("relay") == self.active_url else {}
        availability = await self.refresh_ollama_availability(profiles)
        if generation != self.generation:
            return
        response = await http.get(base + "/agents", headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
        response.raise_for_status()
        if generation != self.generation:
            return
        agents = []
        for item in response.json()["agents"]:
            profile = profiles.get(item["id"], {})
            agents.append({**item, "kind": "model" if profile else item.get("kind", "device"),
                           "online": item["online"] and availability.get(item["id"], True),
                           "provider": profile.get("provider", item.get("provider")), "model": profile.get("model", item.get("model")),
                           "vision": profile.get("vision", item.get("vision", False)),
                           "local": item["id"] in (self.model_runners if profile else self.runners),
                           "can_start": bool(profile.get("provider") == "ollama" and OllamaService.local_address(
                               profile.get("base_url") or PROVIDERS["ollama"].base_url)), "profile": profile})
        self.emit("agents", {"agents": agents, "self": self.active_id,
                            "connected": bool(self.runners.get(self.active_id, (None,))[0] and self.runners[self.active_id][0].ready.is_set())})
        response = await http.get(base + "/conversations", headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
        # Older relays still support direct agent chats.
        if response.status_code != 404:
            response.raise_for_status()
        if response.status_code == 200 and generation == self.generation:
            self.emit("conversations", response.json()["conversations"])
        if not self.remote:
            self.app.state.relay.workspace.update(name=self.storage.settings.get("workspace_name", "My workspace"),
                models=[profile["id"] for profile in self.storage.settings.get("agents", [])])
        response = await http.get(base + "/workspace", headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
        if response.status_code == 200 and generation == self.generation:
            self.emit("workspace_info", response.json())

    async def _poll(self):
        next_join = time.monotonic() + 15
        # How long to wait before the next attempt, and how many attempts have
        # failed in a row. The cadence was a flat three seconds whatever had
        # happened, which is right while things work and wrong the moment they
        # do not: an unreachable host was asked for /agents, /conversations and
        # /workspace every three seconds for as long as the app stayed open, and
        # every desktop client sharing that host came back at the same moment
        # and asked again together.
        while not self.closed:
            delay = self._poll_delay
            saved = self.storage.settings.get("remote") if self.restore_remote else None
            if saved and not self.remote and time.monotonic() >= next_join:
                next_join = time.monotonic() + 15
                try:
                    token = self.storage.vault.get("remote-token")
                    if token:
                        await self.join(saved["url"], token, saved.get("allow_insecure", False), save=False)
                except Exception:
                    pass
            try:
                await self.refresh()
                # Back to the steady cadence on the first success, so a blip
                # costs one slow moment rather than the rest of the session.
                self._poll_delay = 3
            except Exception:
                self.emit("offline", "Relay connection interrupted. The app will keep trying to reconnect.")
                # Doubling, to a ceiling of five minutes, with a little
                # jitter so a room full of clients does not come back in step.
                self._poll_delay = min(self._poll_delay * 2, 300)
            try:
                # Waiting on an event rather than sleeping, so a manual refresh
                # can end the wait instead of only clearing the number for the
                # next one. The event is only ever set to cut this short, so it
                # is cleared here rather than when it is set.
                await asyncio.wait_for(self._poll_wake.wait(),
                                       delay * (0.9 + 0.2 * random.random()))
            except asyncio.TimeoutError:
                pass
            else:
                self._poll_wake.clear()

    async def send(self, target, payload):
        generation = self.generation
        if self.remote:
            await self.prepare_models(self.active_id, target)
        if generation != self.generation:
            raise RuntimeError("Workspace changed while the request was running; it was not replayed")
        base = relay_http_url(self.active_url, allow_insecure=True)
        try:
            general = target == GENERAL_TARGET
            response = await self.relay_http().post(base + ("/orchestrate" if general else f"/agents/{target}/invoke"), json=payload,
                headers={"Authorization": "Bearer " + self.active_token},
                timeout=ORCHESTRATION_TIMEOUT + 5 if general else 65 + self.ollama_service.START_TIMEOUT + 4)
            result = response.json()
            if response.status_code != 200:
                raise RuntimeError(result.get("error", "The agent could not complete this request"))
            if generation != self.generation:
                raise RuntimeError("Workspace changed while the request was running; it was not replayed")
            return result
        except httpx.TimeoutException:
            raise RuntimeError("The request timed out and was not replayed. The agent may already have processed it.") from None
        except httpx.HTTPError:
            raise RuntimeError("Could not reach the relay. Your request was not replayed.") from None

    async def send_conversation(self, conversation_id, text):
        generation = self.generation
        if self.remote:
            await self.prepare_models(self.active_id, GENERAL_TARGET)
        if generation != self.generation:
            raise RuntimeError("Workspace changed while the request was running; it was not replayed")
        base = relay_http_url(self.active_url, allow_insecure=True)
        try:
            response = await self.relay_http().post(base + f"/conversations/{conversation_id}/messages",
                json={"content": text} if isinstance(text, list) else {"text": text},
                headers={"Authorization": "Bearer " + self.active_token}, timeout=ORCHESTRATION_TIMEOUT + 5)
            if generation != self.generation:
                raise RuntimeError("Workspace changed while the request was running; it was not replayed")
            await self.refresh()
            if response.status_code != 200:
                raise RuntimeError(response.json().get("error", "Could not send to the shared conversation"))
            return response.json()
        except httpx.TimeoutException:
            raise RuntimeError("The request timed out and was not replayed. Check the shared conversation for the reply.") from None
        except httpx.HTTPError:
            raise RuntimeError("Could not reach the relay. Your request was not replayed.") from None

    async def cookbook_refresh(self, repo, artifact_repo=None, base=None):
        """Read one model's details from the model catalogue, on request.

        Reached only from the Cookbook's Refresh control, which asks first and
        says where it is about to read from. Nothing in this app calls it on a
        timer or on open, because the Resources page tells the user that nothing
        is sent anywhere and a background call would make that untrue. There is
        deliberately no scheduled refresh either: a timer that quietly phones
        home contradicts the promise the same page is making.

        No ``self.mutation`` lock. It serialises writes to workspace state, and
        this reads a third-party site and writes nothing here -- the panel saves
        its result to the cache file itself. Taking the lock meant a refresh of
        four models, three requests each, with a fifteen-second timeout per
        request, could stand in front of every other mutation for minutes at a
        time, for no state in common. Reads do not need it.

        Always ``self.http``. ``relay_http()`` picks a client configured for
        talking to a relay -- ``direct_http``, which ignores the environment --
        when the relay is on a direct connection, and this request never goes to
        a relay at all: ``fetch_entry`` reads huggingface.co, or the mirror.
        The only thing the branch actually changed was whether a system proxy was
        honoured, and a catalogue read should honour it in every case, since
        that is what someone behind a corporate proxy is relying on.

        ``base`` selects the host. It defaults to the public catalogue, and a
        mirror serving the same three paths works identically -- which is the
        answer to reading this without the public host seeing the request.

        Returns the fields the Cookbook can check rather than the whole
        response, so a payload that has grown new keys cannot quietly become
        part of a recommendation. Anything absent is left out of the result
        rather than defaulted: a missing context length would otherwise become
        a key/value cache figure and an invented one is confidently wrong by a
        factor of several.

        The identifer is validated as ``publisher/name`` before it is put in a
        URL, so a record cannot redirect the request somewhere else, and the
        host is validated as an https origin for the same reason.
        """
        from .cookbook_hub import fetch_entry

        return await fetch_entry(self.http, repo, artifact_repo, base)

    async def chat_command(self, text):
        """Execute a typed workspace action without forwarding it to an agent."""
        command = parse_command(text)
        if command is None:
            raise ValueError("Enter a chat command, such as /help.")
        if command.name == "help":
            return {"text": HELP_TEXT}
        if command.name == "set" and self.remote:
            raise ValueError("Only the workspace owner can assign the Head or specialist roles.")
        if self.closed:
            raise ValueError("This workspace is closed. The command was not applied.")
        if self.remote:
            base = relay_http_url(self.active_url, allow_insecure=True)
            response = await self.relay_http().get(base + "/agents",
                headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
            response.raise_for_status()
            agents = response.json()["agents"]
            response = await self.relay_http().get(base + "/workspace",
                headers={"Authorization": "Bearer " + self.active_token}, timeout=8)
            response.raise_for_status()
            head = response.json().get("coordinator")
        else:
            relay = self.app.state.relay
            agents = [relay.agent_description(identity) for identity in relay.credentials
                      if relay.allowed(self.active_id, identity)]
            head = self.storage.settings.get("coordinator")
        if command.name == "set":
            identity = resolve_identity(command.identity, [agent["id"] for agent in agents]) if command.identity else None
            if command.role in ("Head", "Automatic"):
                await self.set_coordinator(identity)
                chosen = self.storage.settings.get("coordinator")
                return {"text": f"Head set to **@{chosen}**." if chosen else "Head set to **Automatic**. Import a model to start chat."}
            await self.set_model_role(identity, command.role)
            return {"text": f"**@{identity}** is now **{command.role}**. " +
                    ("Specialist delegation is off." if command.role == "Chat" else "Its purpose and permitted tasks were saved; specialist delegation is enabled.")}
        if command.name == "status":
            specialists = [agent for agent in agents if agent.get("delegation_enabled")
                           and agent.get("orchestration_authorized")]
            lines = [f"**Head:** @{head}" if head else "**Head:** Automatic (first imported model)", "", "**Specialists**"]
            lines += [f"- @{agent['id']}: {', '.join(agent['tasks'])}" for agent in specialists] or ["No specialist roles assigned."]
        else:
            models = command.name == "agents"
            rows = [agent for agent in agents if (agent.get("kind") == "model") == models]
            lines = ["**Model agents**" if models else "**Users / devices**", ""]
            lines += [f"- @{agent['id']} — {'Online' if agent['online'] else 'Offline'}" for agent in rows] or ["None in this workspace."]
        return {"text": "\n".join(lines)}

    async def set_model_role(self, identity, role):
        """Persist routing preferences without restarting the model or changing keys."""
        async with self.mutation:
            if self.remote or self.closed:
                raise ValueError("Only the owner of an open workspace can assign specialist roles.")
            self.storage.recover_agent_save()
            previous = self.storage.settings.get("agents", [])
            profile = next((profile for profile in previous if profile["id"] == identity), None)
            if profile is None:
                raise ValueError("Assign specialist roles to models imported on this device.")
            if role not in MODEL_ROLES:
                raise ValueError("Choose a specialist role from /help.")
            purpose, tasks = MODEL_ROLES[role]
            updated = {**profile, **routing_profile({"purpose": purpose, "tasks": list(tasks), "delegation_enabled": bool(tasks)})}
            try:
                self.storage.update(agents=[updated if item["id"] == identity else item for item in previous])
                running = self.app.state.relay.agent_profiles.get(identity, {}).get("running", False)
                await self.publish_profile(updated, running=running)
                await self.refresh()
            except BaseException:
                with contextlib.suppress(Exception):
                    self.storage.update(agents=previous)
                raise

    async def set_coordinator(self, agent_id):
        """Save the owner's default model, retaining import order for Automatic."""
        async with self.mutation:
            if self.closed:
                raise ValueError("This workspace is closed. The command was not applied.")
            if self.remote:
                raise ValueError("The workspace owner chooses the default model")
            relay = self.app.state.relay
            if not agent_id:
                agent_id = next((model["id"] for model in relay.orchestrator.models(self.active_id)), None)
            if agent_id and (not relay.allowed(self.active_id, agent_id)
                             or relay.agent_description(agent_id)["kind"] != "model"):
                raise ValueError("Choose a model in this workspace as the default")
            previous = self.storage.settings.get("coordinator")
            try:
                self.storage.update(coordinator=agent_id or None)
            except BaseException:
                with contextlib.suppress(Exception):
                    if previous is None:
                        self.storage.discard("coordinator")
                    else:
                        self.storage.update(coordinator=previous)
                raise
            relay.workspace["coordinator"] = agent_id or None
            await self.refresh()

    async def provider_models(self, provider, base_url, key=None, allow_insecure=False, agent_id=None):
        """List provider models, reusing a saved key only for its configured endpoint."""
        if key is None and agent_id:
            profile = self.storage.settings.get("remote_agent") if self.remote else next(
                (profile for profile in self.storage.settings.get("agents", []) if profile["id"] == agent_id), None)
            # Reuse a saved key only for the endpoint it was configured for.
            if profile and profile["provider"] == provider and (profile.get("base_url") or PROVIDERS[provider].base_url) == base_url:
                key = self.storage.vault.get("provider:" + agent_id)
        config = ProviderConfig(provider, "discovery", base_url, api_key=key, allow_insecure=allow_insecure)
        if provider == "ollama":
            return await self.ollama_service.models(self.http, config)
        path = "/models"
        headers = {"Authorization": "Bearer " + key} if key else {}
        params = {}
        if provider in ("anthropic", "gemini") and not key:
            raise ValueError("Enter an API key to list available models")
        if provider == "anthropic":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            params = {"limit": 1000}
        elif provider == "gemini":
            headers = {"x-goog-api-key": key}
            params = {"pageSize": 1000}
        models, cursors = [], set()
        async with asyncio.timeout(30):
            for _ in range(100):
                response = await self.http.get(config.base_url.rstrip("/") + path,
                    headers=headers, params=params, timeout=8, follow_redirects=False)
                response.raise_for_status()
                payload = response.json()
                if provider == "gemini":
                    models.extend(item["name"].removeprefix("models/") for item in payload.get("models", [])
                                  if "generateContent" in item.get("supportedGenerationMethods", []))
                else:
                    models.extend(item["id"] for item in payload["data"])
                cursor = (payload.get("nextPageToken") if provider == "gemini" else
                          payload.get("last_id") if payload.get("has_more") else None)
                if payload.get("has_more") and not cursor:
                    raise ValueError("The provider's model list is missing its next-page cursor. Try refreshing the list.")
                if not cursor:
                    return list(dict.fromkeys(models))
                if cursor in cursors:
                    raise ValueError("The provider repeated a model-list page. Try refreshing the list.")
                cursors.add(cursor)
                params["pageToken" if provider == "gemini" else "after_id"] = cursor
        raise ValueError("The provider's model list could not be completed. Try refreshing the list.")

    async def test_web_search(self, url="", allow_insecure=False, provider="searxng", key=None, agent_id=None):
        """Return a probe result count, reusing the saved hosted search key if needed."""
        if not key and agent_id and provider == "ollama":
            key = self.storage.vault.get("search:" + agent_id)
        search = create_search(self.http, provider, url=url, api_key=key, allow_insecure=allow_insecure)
        results = await search.search("web search")
        return len(results)

    async def invite(self, agent_id, public_url, lan=False, conversation_id=None, target=None, messages=None):
        """Create an invitation for a new device identity in the locally owned workspace."""
        async with self.mutation:
            if self.remote:
                raise ValueError("Only this workspace's host can create invitations")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id) or agent_id in self.credentials:
                raise ValueError("Choose a new device identity with 1–64 letters, numbers, underscores, or hyphens")
            relay_http_url(public_url, allow_insecure=lan)
            conversations = self.app.state.relay.conversations
            if conversation_id and not conversations.get(conversation_id, self.active_id):
                raise ValueError("Choose an existing conversation in this workspace")
            if target and not self.app.state.relay.allowed(self.active_id, self.active_id if target == GENERAL_TARGET else target):
                raise ValueError("Choose an agent in this workspace")
            if lan and not self.sharing:
                await self._stop_agents()
                self.server.should_exit = True
                await self.server_task
                self.server_socket.close()
                await self._start_server("0.0.0.0", self.port)
                self.sharing = True
                self.storage.update(share_lan=True)
                await self._attach(self.active_id, self.active_token, self._echo)
                await self._restore_profiles()
            updated = {**self.credentials, agent_id: {"token": secrets.token_urlsafe(32), "group": "workspace"}}
            Relay(updated)
            self.storage.save_credentials(updated)
            self.credentials = updated
            self.app.state.relay.credentials = updated
            if target and not conversation_id:
                conversation_id = conversations.create(self.active_id, target, messages or ())["id"]
            if conversation_id:
                conversations.invite(conversation_id, self.active_id, agent_id)
            self.emit("activity", {"title": "Device invitation created", "detail": agent_id})
            await self.refresh()
            invitation = {"version": 1, "agent_id": agent_id, "url": public_url, "token": updated[agent_id]["token"], "allow_insecure": lan}
            invitation["workspace_name"] = self.storage.settings.get("workspace_name", "My workspace")
            if conversation_id:
                invitation["conversation_id"] = conversation_id
            # Everything except the token, so the invitation can be shown again
            # later. The token itself is not written here and never leaves the
            # credential store: it is read back from the vault on demand, so a
            # settings file that is copied off this machine does not carry
            # anyone's access. An invitation used to be readable exactly once,
            # in the dialog that created it, which meant a reader who closed
            # it without copying had stranded the device they were inviting --
            # and the only way out was to reset their own identity, which
            # destroys every other identity in the workspace with it.
            self.storage.change(lambda settings: settings.__setitem__(
                "invitations", {**(settings.get("invitations") or {}), agent_id: {
                    "url": public_url, "allow_insecure": bool(lan),
                    "workspace_name": invitation["workspace_name"],
                    "conversation_id": conversation_id or ""}}))
            return invitation

    async def outstanding_invitations(self):
        """Device identities this device minted, and can still issue an invite for.

        Everything except the local identity and anything that is a connected
        model agent: a model agent has a profile in settings["agents"] and was
        connected rather than invited, so it has no invitation to reissue.
        """
        if self.remote:
            return []
        agents = {profile["id"] for profile in self.storage.settings.get("agents", [])}
        recorded = self.storage.settings.get("invitations") or {}
        return sorted(name for name in self.credentials
                      if name != self.active_id and name not in agents
                      and name in recorded)

    async def invitation_for(self, agent_id):
        """Rebuild one invitation, reading its token back from the credential store.

        Raises rather than returning a partial invitation: an invitation
        missing its token looks like a valid one until the guest is refused,
        and the reader has no way to tell which of the two they are holding.
        """
        if self.remote:
            raise ValueError("Only this workspace's host can issue invitations")
        recorded = (self.storage.settings.get("invitations") or {}).get(agent_id)
        if not recorded:
            raise ValueError(f"There is no saved invitation for {agent_id}")
        try:
            token = self.storage.vault.get("relay:" + agent_id)
        except (RuntimeError, OSError):
            # The store is locked. Vault.get raises rather than returning
            # nothing, and letting that through as a generic failure would say
            # the operation failed, when what is actually true is that it has
            # not been tried yet.
            raise ValueError(
                f"The saved invitation for {agent_id} cannot be read because "
                f"your credential store is locked. Unlock it and try again."
            ) from None
        if not token:
            raise ValueError(
                f"The saved invitation for {agent_id} is no longer in your "
                f"credential store. Create a new one.")
        invitation = {"version": 1, "agent_id": agent_id, "url": recorded["url"],
                      "token": token, "allow_insecure": bool(recorded["allow_insecure"])}
        invitation["workspace_name"] = recorded["workspace_name"]
        if recorded.get("conversation_id"):
            invitation["conversation_id"] = recorded["conversation_id"]
        return invitation

    async def remove_member(self, member):
        """Revoke a member's access and clean up its runner, history, and credentials."""
        async with self.mutation:
            self.storage.recover_agent_save()
            if self.remote:
                raise ValueError("Only the workspace owner can remove members")
            if member == self.active_id:
                raise ValueError("Delete the workspace to remove its owner")
            if member not in self.credentials:
                raise ValueError("This member is no longer in the workspace")
            updated = {name: config for name, config in self.credentials.items() if name != member}
            # The invitation record goes with the credential, or the Workspaces
            # page would keep offering to reissue an invitation for someone who
            # has just been removed -- and the token it would read back has
            # been deleted below, so the offer would only fail when used.
            self.storage.change(lambda settings: settings.__setitem__(
                "invitations", {name: saved for name, saved
                                in (settings.get("invitations") or {}).items()
                                if name != member}))
            self.storage.save_credentials(updated)
            self.credentials = updated
            self.app.state.relay.credentials = updated
            self.app.state.relay.conversations.remove_member(member)
            peer = self.app.state.relay.peers.get(member)
            if peer:
                await peer.socket.close(code=1008, reason="Removed from workspace")
            await self._remove_runner(member)
            self.storage.change(lambda settings: settings.__setitem__(
                "agents", [profile for profile in settings.get("agents", [])
                           if profile["id"] != member]))
            if self.storage.settings.get("coordinator") == member:
                self.storage.update(coordinator=None)
                self.app.state.relay.workspace["coordinator"] = None
            # The member is out of the workspace either way, but a token left
            # in the OS credential store would still be a secret this device
            # holds for someone who no longer has access, so say so rather
            # than reporting a clean removal, and record the names so a later
            # launch can finish the job rather than only telling the user to
            # try again.
            undeleted = [name for name in ("relay:" + member, "provider:" + member, "search:" + member)
                         if self.storage.vault.delete(name) == UNAVAILABLE]
            self.emit("activity", {"title": "Member removed", "detail": member})
            if undeleted:
                self.storage.record_owed_credentials(undeleted, "member")
                self.emit("notice", f"{member} was removed, but their saved credentials "
                                    "could not be deleted. Unlock your credential store "
                                    "and restart; the app will try again.")
            await self.refresh()

    async def close(self):
        self.closed = True
        if self.poller:
            self.poller.cancel()
            await asyncio.gather(self.poller, return_exceptions=True)
        await self._stop_agents()
        if self.server:
            self.server.should_exit = True
        if self.server_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server_task, 8)
        if self.server_socket:
            self.server_socket.close()
        if self.http:
            await self.http.aclose()
        if self.direct_http:
            await self.direct_http.aclose()
        if self.owns_ollama_service:
            await self.ollama_service.close()
