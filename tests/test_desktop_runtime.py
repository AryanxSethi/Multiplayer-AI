import asyncio
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import httpx

from desktop_app.runtime import DesktopRuntime, relay_http_url
from desktop_app.storage import ABSENT, REMOVED, UNAVAILABLE, Storage
import desktop_app.runtime as runtime_module
from desktop_app.workspace_runtime import WorkspaceRuntime


class MemoryVault:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        # Speaks the same vocabulary as the real Vault so a cleanup that
        # reports an outstanding credential is exercised for real.
        if name in self.values:
            del self.values[name]
            return REMOVED
        return ABSENT


class DesktopRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.storage = Storage(self.directory.name, self.vault)
        self.runtime = DesktopRuntime(self.storage, lambda kind, data: self.events.append((kind, data)))
        await self.runtime.start()
        self.other = None

    async def asyncTearDown(self):
        if self.other:
            await self.other.close()
        await self.runtime.close()
        self.directory.cleanup()

    async def test_launch_sets_up_relay_identity_and_real_connectivity(self):
        self.assertTrue(any(kind == "ready" for kind, _ in self.events))
        result = await self.runtime.send(self.runtime.active_id, {"text": "Hello desktop"})
        self.assertIn("Hello desktop", result["text"])
        async with httpx.AsyncClient() as http:
            response = await http.get(f"http://127.0.0.1:{self.runtime.port}/health")
            self.assertEqual(response.json(), {"status": "ok"})
            self.assertEqual((await http.get(f"http://127.0.0.1:{self.runtime.port}/ui/")).status_code, 404)
        settings_text = Path(self.directory.name, "settings.json").read_text()
        self.assertNotIn(self.runtime.active_token, settings_text)

    async def test_second_desktop_joins_and_exchanges_messages(self):
        invite = await self.runtime.invite("second-device", self.runtime.active_url)
        other_directory = Path(self.directory.name) / "other"
        self.other = DesktopRuntime(Storage(other_directory, MemoryVault()))
        await self.other.start()
        await self.other.join(invite["url"], invite["token"])
        self.assertTrue(self.other.remote)
        result = await self.runtime.send("second-device", {"text": "Across desktops"})
        self.assertIn("Across desktops", result["text"])
        await self.other.use_local()
        self.assertFalse(self.other.remote)

    async def test_joined_devices_receive_visible_messages_in_both_directions(self):
        invite = await self.runtime.invite("visible-device", self.runtime.active_url)
        received = []
        self.other = DesktopRuntime(Storage(Path(self.directory.name) / "visible", MemoryVault()),
                                    lambda kind, data: received.append((kind, data)))
        await self.other.start()
        await self.other.join(invite["url"], invite["token"])
        await self.runtime.send("visible-device", {"text": "Hello from the host"})
        incoming = [data for kind, data in received if kind == "incoming"]
        self.assertEqual(incoming[-1], {"from": self.runtime.active_id, "to": "visible-device", "text": "Hello from the host"})
        self.assertTrue(any(kind == "incoming_reply" for kind, _ in received))
        await self.other.send(self.runtime.active_id, {"messages": [{"role": "user", "content": "Reply from the guest"}]})
        incoming = [data for kind, data in self.events if kind == "incoming"]
        self.assertEqual(incoming[-1]["from"], "visible-device")
        self.assertEqual(incoming[-1]["text"], "Reply from the guest")
        count = len(incoming)
        await self.runtime.send(self.runtime.active_id, {"text": "Self check"})
        self.assertEqual(len([data for kind, data in self.events if kind == "incoming"]), count)

    async def test_saved_remote_workspace_restores_on_launch(self):
        invite = await self.runtime.invite("returning-device", self.runtime.active_url)
        directory = Path(self.directory.name) / "returning"
        vault = MemoryVault()
        first = DesktopRuntime(Storage(directory, vault))
        await first.start()
        await first.join(invite["url"], invite["token"])
        await first.close()
        self.other = DesktopRuntime(Storage(directory, vault))
        await self.other.start()
        self.assertTrue(self.other.remote)
        self.assertEqual(self.other.active_id, "returning-device")
        self.assertIn("remote", self.other.storage.settings)

    async def test_provider_agent_runs_in_process_and_restores(self):
        old_http = self.runtime.http
        async def transport(request):
            if request.url.host == "model.example":
                body = json.loads(request.content)
                return httpx.Response(200, json={"choices": [{"message": {"content": "Native model reply"}, "finish_reason": "stop"}]})
            return await old_http.send(request)
        self.runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        profile = {"id": "researcher", "provider": "bionic", "model": "test-model", "base_url": "https://model.example/v1", "autostart": True}
        await self.runtime.save_agent(profile, "private-provider-key")
        result = await self.runtime.send("researcher", {"text": "Hello"})
        self.assertEqual(result["text"], "Native model reply")
        invite = await self.runtime.invite("model-guest", self.runtime.active_url)
        self.other = DesktopRuntime(Storage(Path(self.directory.name) / "model-guest", MemoryVault()))
        await self.other.start()
        await self.other.join(invite["url"], invite["token"])
        result = await self.other.send("researcher", {"text": "Use the host's model from the joined device"})
        self.assertEqual(result["text"], "Native model reply")
        self.assertTrue(any(kind == "incoming" and data["from"] == "model-guest" and data["to"] == "researcher"
                            for kind, data in self.events))
        self.assertNotIn("private-provider-key", self.storage.path.read_text())
        await self.runtime.stop_agent("researcher")
        self.assertFalse(self.storage.settings["agents"][0]["autostart"])
        await old_http.aclose()

    async def test_invalid_join_preserves_local_workspace(self):
        identity = self.runtime.active_id
        with self.assertRaisesRegex(ValueError, "rejected"):
            await self.runtime.join(self.runtime.active_url, "incorrect-token-" * 3)
        self.assertEqual(self.runtime.active_id, identity)
        self.assertFalse(self.runtime.remote)
        self.assertIn("still connected", (await self.runtime.send(identity, {"text": "still connected"}))["text"])

    async def test_unreachable_join_has_visible_error_and_preserves_workspace(self):
        original_http = self.runtime.http
        identity = self.runtime.active_id
        try:
            for exception, expected in ((httpx.ReadTimeout(""), "did not respond"),
                                        (httpx.ConnectError(""), "Could not connect")):
                def fail(request):
                    raise exception
                self.runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
                with self.assertRaisesRegex(ConnectionError, expected):
                    await self.runtime.join("wss://unreachable.example/connect", "x" * 32)
                self.assertEqual(self.runtime.active_id, identity)
                self.assertFalse(self.runtime.remote)
                await self.runtime.http.aclose()
        finally:
            self.runtime.http = original_http

    async def test_join_rejects_identity_already_in_use_without_disconnect(self):
        identity = self.runtime.active_id
        with self.assertRaisesRegex(ValueError, "already connected"):
            await self.runtime.join(self.runtime.active_url, self.runtime.active_token)
        self.assertTrue(self.runtime.runners[identity][0].ready.is_set())
        self.assertFalse(self.runtime.remote)

    async def test_shutdown_releases_sockets_and_preserves_identity(self):
        identity = self.runtime.active_id
        await self.runtime.close()
        self.assertTrue(self.runtime.server_task.done())
        self.runtime = DesktopRuntime(Storage(self.directory.name, self.vault))
        await self.runtime.start()
        self.assertEqual(self.runtime.active_id, identity)

    def test_internet_join_requires_tls(self):
        with self.assertRaisesRegex(ValueError, "require wss"):
            relay_http_url("ws://remote.example/connect")
        self.assertEqual(relay_http_url("wss://remote.example/connect"), "https://remote.example")
        with self.assertRaises(ValueError):
            relay_http_url("wss://remote.example/connect?token=secret")


class WorkspaceDurabilityTests(unittest.IsolatedAsyncioTestCase):
    """Paths where a failure could undo or hide a destructive action.

    Each of these wrote something the user asked for, then did a network
    or disk operation that can fail before the change reached disk.
    """

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.storage = Storage(self.directory.name, self.vault)
        self.runtime = DesktopRuntime(self.storage, lambda kind, data: self.events.append((kind, data)))
        await self.runtime.start()

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_stopping_an_agent_survives_a_failed_reconnect(self):
        """Stopping an agent must be recorded even if the echo agent fails.

        The remote branch re-attaches the connectivity agent before it
        saved, and that attach waits on the network. When it raised, the
        stop was lost and the next launch started the agent again.
        """
        self.runtime.engine.storage.settings["remote_agent"] = {
            "id": self.runtime.active_id, "provider": "bionic", "model": "test-model", "autostart": True}
        self.runtime.engine.remote = True
        original = self.runtime.engine._attach

        async def fail(*args, **kwargs):
            raise TimeoutError("Agent did not connect.")

        self.runtime.engine._attach = fail
        try:
            with self.assertRaises(TimeoutError):
                await self.runtime.engine.stop_agent(self.runtime.active_id)
        finally:
            self.runtime.engine._attach = original
            self.runtime.engine.remote = False

        on_disk = json.loads(self.storage.path.read_text(encoding="utf-8"))
        self.assertFalse(on_disk["remote_agent"]["autostart"])
        self.assertFalse(self.runtime.app.state.relay.agent_profiles[self.runtime.active_id]["running"])

    async def test_deleting_a_workspace_is_recorded_before_anything_is_destroyed(self):
        """A failure after the wipe must not resurrect the workspace.

        ensure_engine() starts a relay and touches the credential store.
        It previously ran after the credentials and history were gone but
        before workspaces.json was written, so a failure there left the
        workspace listed on disk with nothing behind it.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        original = self.runtime.ensure_engine

        async def fail(entry):
            raise RuntimeError("Local relay could not start")

        self.runtime.ensure_engine = fail
        with self.assertRaisesRegex(RuntimeError, "relay could not start"):
            await self.runtime.delete_workspace()

        catalog = json.loads(self.runtime.catalog_path.read_text(encoding="utf-8"))
        remaining = {entry["id"] for entry in catalog["workspaces"]}
        self.assertNotIn(second["id"], remaining)
        self.assertTrue(remaining, "a replacement workspace must remain")
        self.runtime.ensure_engine = original

    async def test_reset_identity_rotates_every_local_workspace(self):
        """Each local workspace owns its own relay and credential set.

        The catalog manager delegates the rotation per workspace, and this
        loop had no coverage at all, which is how a method call chained
        onto an un-awaited coroutine survived review.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        engines = [self.runtime.engines[entry["id"]]
                   for entry in self.runtime.catalog["workspaces"] if entry["kind"] == "local"]
        before = {engine.active_id: engine.credentials[engine.active_id]["token"]
                  for engine in engines}
        self.assertEqual(len(engines), 2, "expected two local workspaces")

        results = await self.runtime.reset_identity()

        self.assertEqual(len(results), 2)
        for engine in engines:
            after = engine.credentials[engine.active_id]["token"]
            self.assertNotEqual(after, before[engine.active_id],
                                "each workspace should have received a new token")
            # Read through the engine's own vault: only the workspace
            # called "local" shares the root one, the rest are namespaced.
            self.assertTrue(engine.storage.vault.get("relay:" + engine.active_id),
                            "the replacement token must be in the vault")

    async def test_a_cancelled_reset_still_leaves_the_record_behind(self):
        """Cancelling the reset between forgetting and announcing used to lose both.

        The record used to be written after ``await self._stop_agents()``, so
        a cancellation there left a superseded token that no settings entry
        named and nothing would ever retry.
        """
        engine = self.runtime.engine
        engine.storage.settings["identities"]["guest"] = "workspace"
        engine.storage.save()
        engine.storage.vault.set("relay:guest", "the superseded token")
        original = self.vault.delete

        def locked(name):
            return UNAVAILABLE
        self.vault.delete = locked

        async def stop_forever():
            raise asyncio.CancelledError()
        engine._stop_agents = stop_forever
        try:
            with self.assertRaises(asyncio.CancelledError):
                await self.runtime.reset_identity()
        finally:
            self.vault.delete = original
            del engine._stop_agents

        records = self.runtime.storage.read_pending_cleanup()
        self.assertEqual([(r["source"], r["credentials"]) for r in records],
                         [("reset", ["relay:guest"])],
                         "the record must not depend on reaching the notice")

    async def test_an_undeleted_reset_token_is_recorded_and_retried(self):
        """The reset's notice promises a retry, so it must record one.

        A superseded token that outlives the reset belongs to nobody and
        nothing on disk names it, so without a record it would sit in the
        credential store for good.
        """
        engine = self.runtime.engine
        engine.storage.settings["identities"]["guest"] = "workspace"
        engine.storage.save()
        engine.storage.vault.set("relay:guest", "the superseded token")
        original = self.vault.delete

        def locked(name):
            return UNAVAILABLE
        self.vault.delete = locked
        try:
            results = await self.runtime.reset_identity()
        finally:
            self.vault.delete = original

        owed = sorted({agent_id for result in results for agent_id in result["undeleted"]})
        self.assertTrue(owed, "the reset should have been unable to delete a token")
        records = self.runtime.storage.read_pending_cleanup()
        self.assertEqual(sorted((r["type"], r["source"]) for r in records),
                         [("credentials", "reset")],
                         "one record per workspace, both scoped to the root vault")
        self.assertEqual(records[0]["workspace"], None,
                         "the local workspace shares the root vault")
        self.assertEqual(sorted(records[0]["credentials"]),
                         sorted("relay:" + agent_id for agent_id in owed))

        self.vault.delete = original
        self.runtime.retry_pending_cleanup()

        for name in records[0]["credentials"]:
            self.assertIsNone(self.vault.get(name), f"{name} should be gone")
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [])

    async def test_a_workspace_folder_that_will_not_go_keeps_the_record(self):
        """A chat archive left on disk must not pass for a finished deletion.

        rmtree used to be called with ignore_errors, so a locked or read-only
        folder reported success: the workspace left the catalog, the record
        was discarded, and settings.json plus history.sqlite3 stayed behind
        with nothing pointing at them.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        folder = self.runtime._workspace_directory(second["id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "history.sqlite3").write_text("a chat archive", encoding="utf-8")

        with unittest.mock.patch("desktop_app.runtime.shutil.rmtree",
                                 side_effect=OSError("folder is in use")):
            await self.runtime.delete_workspace()

        self.assertTrue((folder / "history.sqlite3").exists(), "the archive is still there")
        records = self.runtime.storage.read_pending_cleanup()
        self.assertEqual([r["id"] for r in records], [second["id"]],
                         "nothing else is owed, so only the folder can be holding it")
        self.assertTrue(records[0]["data_owed"], "the record must say what is left")
        self.assertEqual(records[0]["credentials"], [])
        self.assertTrue([1 for kind, data in self.events
                         if kind == "notice" and "files" in str(data).lower()],
                        "the user must be told their files survived")

    async def test_a_stranded_folder_is_retried_until_it_goes(self):
        """Retaining the record is only useful if a later launch converges."""
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        folder = self.runtime._workspace_directory(second["id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "history.sqlite3").write_text("a chat archive", encoding="utf-8")
        data = Path(self.directory.name)
        held = unittest.mock.patch("desktop_app.runtime.shutil.rmtree",
                                   side_effect=OSError("folder is in use"))

        async def relaunch():
            await self.runtime.close()
            self.runtime = DesktopRuntime(
                Storage(data, self.vault),
                lambda kind, payload: self.events.append((kind, payload)))

        with held:
            await self.runtime.delete_workspace()
        self.assertTrue(self.runtime.storage.read_pending_cleanup(),
                        "the first failure leaves the record")

        # Two more launches where the folder is still held. The mock has to
        # wrap the start-up itself, because that is where the retry happens.
        for attempt in range(2):
            await relaunch()
            with held:
                await self.runtime.start()
            self.assertTrue(self.runtime.storage.read_pending_cleanup(),
                            f"failure {attempt + 2} must not clear the record")

        await relaunch()
        await self.runtime.start()

        self.assertFalse(folder.exists(), "the archive is gone once the folder releases")
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [])

    async def test_the_local_workspace_is_never_held_back_by_its_folder(self):
        """It shares the root, so it has no folder of its own to be stuck on."""
        self.runtime.app.state.relay.member_remover = None

        await self.runtime.delete_workspace()

        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [],
                         "the local workspace must not retain itself forever")

    async def test_a_directory_that_will_not_flush_does_not_undo_the_deletion(self):
        """The whole path, from the failing flush rather than a stubbed save.

        The rename happens before the directory flush, so a flush failure
        once raised out of a write that had already succeeded. delete_workspace
        caught it, put the workspace back into memory and dropped the pending
        record, while the file no longer listed it. Memory and disk then
        disagreed, and the next unrelated save_catalog would have written the
        workspace back with its directory and credentials already destroyed.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        attempted = []
        real = runtime_module.write_json_durably

        def failing(path, text):
            # Only the catalog's own flush is refused; every earlier write in
            # this sequence has to succeed for the deletion to get that far.
            if Path(path) != self.runtime.catalog_path:
                return real(path, text)
            attempted.append(path)
            with unittest.mock.patch("desktop_app.storage._fsync_directory",
                                     side_effect=OSError("no space left on device")):
                return real(path, text)

        with unittest.mock.patch.object(runtime_module, "write_json_durably", failing):
            await self.runtime.delete_workspace()

        self.assertTrue(attempted, "the catalog's flush should have been attempted")
        self.assertNotIn(second["id"], {e["id"] for e in self.runtime.catalog["workspaces"]})
        on_disk = json.loads(self.runtime.catalog_path.read_text(encoding="utf-8"))
        self.assertNotIn(second["id"], {e["id"] for e in on_disk["workspaces"]},
                         "memory and disk must agree once the rename has landed")
        self.assertIn(self.runtime.active_workspace_id, self.runtime.engines,
                      "the replacement must still be usable")
        self.assertEqual([r["id"] for r in self.runtime.storage.read_pending_cleanup()],
                         [second["id"]],
                         "a power cut could still restore the old catalog, which "
                         "would bring the workspace back empty")

        self.runtime.retry_pending_cleanup()
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [],
                         "re-running the purge is idempotent and settles it")

    async def test_an_unflushed_catalog_still_finishes_the_deletion(self):
        """A rename that landed must not be rolled back.

        The rename happens before the directory flush, so a flush failure
        raises from a write that already succeeded. Rolling the workspace
        back into memory there left the two disagreeing: the file no longer
        listed it, and the next unrelated save_catalog would have written it
        back with its directory and credentials already destroyed.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        real = self.runtime.save_catalog
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                real()          # the rename lands
                return False    # but the directory flush could not confirm it
            return real()
        self.runtime.save_catalog = flaky

        await self.runtime.delete_workspace()

        self.assertNotIn(second["id"], {e["id"] for e in self.runtime.catalog["workspaces"]})
        on_disk = json.loads(self.runtime.catalog_path.read_text(encoding="utf-8"))
        self.assertNotIn(second["id"], {e["id"] for e in on_disk["workspaces"]},
                         "memory and disk must agree once the rename has landed")
        self.assertIn(self.runtime.active_workspace_id, self.runtime.engines,
                      "the replacement must still be usable")
        records = self.runtime.storage.read_pending_cleanup()
        self.assertEqual([r["id"] for r in records], [second["id"]],
                         "the record is kept, because a power cut could restore "
                         "the old catalog and bring the workspace back empty")
        self.assertTrue([1 for kind, data in self.events
                         if kind == "notice" and "next launch" in str(data).lower()],
                        "the user must be told it will be checked again")

        self.runtime.save_catalog = real
        self.runtime.retry_pending_cleanup()
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [],
                         "re-running the purge is idempotent and settles it")

    async def test_the_reset_records_the_owed_names_before_it_awaits(self):
        """The record has to exist while the reset is still suspended.

        Written after the await instead, a cancellation there would lose the
        record and the notice together.
        """
        engine = self.runtime.engine
        engine.storage.settings["identities"]["guest"] = "workspace"
        engine.storage.save()
        engine.storage.vault.set("relay:guest", "the superseded token")
        original = self.vault.delete
        self.vault.delete = lambda name: UNAVAILABLE
        during = []
        stop = engine._stop_agents

        async def peek():
            # Look, then really stop, so the re-attach later cannot collide
            # with a connection this left open.
            records = self.runtime.storage.read_pending_cleanup()
            during.append(sorted(name for r in records for name in r["credentials"]))
            await stop()
        engine._stop_agents = peek
        try:
            await self.runtime.reset_identity()
        finally:
            self.vault.delete = original
            del engine._stop_agents

        self.assertTrue(during, "the reset should have awaited at least once")
        self.assertEqual(during[0], ["relay:guest"],
                         "the ledger must already be written at the first await")

    async def test_a_stranded_folder_is_reported_without_blaming_the_keyring(self):
        """A folder that will not go owes no names, so nothing would report it.

        The pending-cleanup counter only ever counted credential names. A
        workspace held back solely by its directory counted zero, so no notice
        and no Settings line, for as long as the failure lasted.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        folder = self.runtime._workspace_directory(second["id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "history.sqlite3").write_text("a chat archive", encoding="utf-8")
        data = Path(self.directory.name)

        with unittest.mock.patch("desktop_app.runtime.shutil.rmtree",
                                 side_effect=OSError("folder is in use")):
            await self.runtime.delete_workspace()
            self.assertEqual(self.runtime.pending_cleanup_count(), 0,
                             "no credentials are owed; only the folder is")
            self.assertEqual(self.runtime.pending_data_cleanup_count(), 1)
            published = [d for k, d in self.events if k == "pending_cleanup"][-1]
            self.assertEqual(published, {"credentials": 0, "files": 1})

            await self.runtime.close()
            self.runtime = DesktopRuntime(
                Storage(data, self.vault),
                lambda kind, payload: self.events.append((kind, payload)))
            with unittest.mock.patch("desktop_app.runtime.shutil.rmtree",
                                     side_effect=OSError("folder is in use")):
                await self.runtime.start()

        # Only what the relaunched runtimes said: the delete above already
        # raised its own notice about the same folder.
        notices = " ".join(str(payload) for kind, payload in self.events
                           if kind == "notice" and "files" in str(payload).lower()).lower()
        self.assertNotIn("credential store", notices,
                         "the keyring is not what is wrong here")
        self.assertIn("next launch", notices,
                      "a later launch is where the user learns it is still stuck")

    # --- an interrupted deletion ------------------------------------------

    async def test_a_locked_credential_store_leaves_a_record_the_user_can_see(self):
        """A token that outlives its workspace must not pass unnoticed.

        The workspace really is deleted, so nothing on disk points at the
        leftover credentials. The record is what lets a later launch finish
        the job, and the count is what lets Settings say so.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        original = self.runtime.engine.storage.vault.delete

        def locked(name):
            return UNAVAILABLE
        self.runtime.engine.storage.vault.delete = locked
        try:
            await self.runtime.delete_workspace()
        finally:
            self.runtime.engine.storage.vault.delete = original

        records = self.runtime.storage.read_pending_cleanup()
        self.assertEqual([r["id"] for r in records], [second["id"]])
        self.assertTrue(records[0]["credentials"], "the owed names must be recorded")
        owed = [data for kind, data in self.events if kind == "pending_cleanup"]
        self.assertTrue(owed and owed[-1]["credentials"] > 0)
        self.assertTrue([1 for kind, data in self.events
                         if kind == "notice" and "credential" in str(data).lower()],
                        "the user must be told the cleanup did not finish")

    async def test_a_recorded_deletion_is_finished_on_the_next_launch(self):
        """The interrupted half is completed without any further action."""
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        vault = self.runtime.engine.storage.vault
        owed_names = list(self.runtime._credential_names_for(self.runtime.engine.storage))
        self.runtime.storage.record_workspace_deletion(
            {"id": second["id"], "kind": "local"}, owed_names)

        self.runtime.retry_pending_cleanup()

        for name in owed_names:
            self.assertIsNone(vault.get(name), f"{name} should have been deleted")
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [])

    async def test_retrying_a_finished_deletion_is_harmless(self):
        """Idempotent, because a crash after the purge leaves the record."""
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        self.runtime.app.state.relay.member_remover = None
        self.runtime.storage.record_workspace_deletion(
            {"id": second["id"], "kind": "local"},
            list(self.runtime._credential_names_for(self.runtime.engine.storage)))

        self.runtime.retry_pending_cleanup()
        self.runtime.storage.record_workspace_deletion(
            {"id": second["id"], "kind": "local"}, ["remote-token"])
        self.runtime.retry_pending_cleanup()

        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [])

    async def test_a_failed_catalog_write_keeps_the_engine_registered(self):
        """close() only closes engines it still knows about.

        Dropping the engine before the catalog was written would strand its
        relay and its port for the rest of the process, with no error.
        """
        engine = self.runtime.engine
        workspace_id = self.runtime.active_workspace_id

        def refuse():
            raise OSError("disk full")
        self.runtime.save_catalog = refuse
        with self.assertRaises(OSError):
            await self.runtime.delete_workspace()

        self.assertIn(workspace_id, self.runtime.engines,
                      "the engine must stay registered so close() can reach it")
        self.assertEqual(self.runtime.active_workspace_id, workspace_id)
        self.assertIn(workspace_id,
                      {e["id"] for e in self.runtime.catalog["workspaces"]})
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [],
                         "nothing was recorded, so nothing should be owed")

    async def test_the_local_workspace_directory_is_never_removed(self):
        """The local workspace shares the root, so its directory must stay.

        Every other workspace lives under workspaces/<id>. Treating the
        local one the same way would delete settings.json, the catalog and
        every other workspace along with it.
        """
        self.assertIsNone(self.runtime._workspace_directory("local"))
        second = await self.runtime.create_workspace("Writing")
        self.assertTrue(self.runtime._workspace_directory(second["id"]))

        self.runtime.app.state.relay.member_remover = None
        await self.runtime.delete_workspace()

        self.assertTrue(self.storage.path.exists(), "the root settings must survive")
        self.assertTrue(self.runtime.catalog_path.exists())

    async def test_a_ledger_of_hostile_scopes_does_not_stop_the_app_starting(self):
        """A record whose scope is the wrong type used to abort start-up.

        re.fullmatch raises TypeError on anything that is not a string, and
        the retry let that escape, so a corrupt file turned into a fatal
        error rather than the dropped-and-ignored record it should have been.
        """
        hostile = [{"type": "credentials", "workspace": scope, "source": "reset",
                    "credentials": ["relay:guest"]}
                   for scope in (5, ["x"], {"a": 1}, True, 1.5, "workspace-../../etc")]
        hostile += [{"type": "workspace", "id": bad, "kind": "local",
                     "credentials": ["remote-token"]}
                    for bad in (5, ["x"], {"a": 1}, True, "workspace-../../etc")]
        self.vault.set("relay:guest", "a token that must survive a dropped record")
        self.runtime.storage.write_pending_cleanup(hostile)

        # Asserted before the retry runs: afterwards the records would be gone
        # either way, because a retry that did reach them would also discard
        # them, and the reader's own check would look like it had worked.
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [],
                         "the reader must drop a scope it cannot match")

        await self.runtime.close()
        self.runtime = DesktopRuntime(Storage(Path(self.directory.name) / "run", self.vault))
        await self.runtime.start()

        self.assertIn("relay:guest", self.vault.values,
                      "a dropped record must not delete anything on the way past")

    def test_a_hand_edited_record_cannot_point_outside_the_workspaces_folder(self):
        """The record's id is used to build a path that is then removed."""
        attempted = []
        original = self.vault.delete
        for hostile in ("../../..", "workspace-../../etc", "local/../..",
                        "C:/Windows", "", "workspace-" + "z" * 32):
            with self.subTest(workspace_id=hostile):
                with self.assertRaises(ValueError):
                    self.runtime._workspace_directory(hostile)
                self.vault.delete = lambda name: attempted.append(name) or ABSENT
                self.runtime.storage.ledger_path.write_text(json.dumps(
                    [{"type": "workspace", "id": hostile, "kind": "local",
                      "credentials": ["remote-token"]}]), encoding="utf-8")

                self.runtime.retry_pending_cleanup()

                self.assertEqual(attempted, [],
                                 "a malformed id must be dropped, not acted on")
                self.assertEqual(self.runtime.storage.read_pending_cleanup(), [])
        self.vault.delete = original

    def test_a_hand_edited_record_cannot_reach_outside_the_credential_namespace(self):
        """Names in the file become calls into the OS credential store.

        The store is shared with every other application on the machine, so
        a hand-edited ledger must not be able to name an entry this app
        never minted. Note what this does not do: a name the app *could*
        have minted, such as the device's own live token, still matches and
        would be deleted. The file lives in the user's own data directory,
        so it is not a remote attack surface, and ruling that out would need
        the ledger to know which identity is still current, which is
        precisely what a record of the past cannot know.
        """
        attempted = []
        original = self.vault.delete

        def record(name):
            attempted.append(name)
            return original(name)
        self.vault.delete = record
        self.runtime.storage.ledger_path.write_text(json.dumps(
            [{"type": "credentials", "workspace": None, "source": "reset",
              "credentials": ["SomeOtherApp:token", "../../relay:device", "relay:",
                              "relay:" + "x" * 200, "provider:has space",
                              "remote-token", "relay:guest"]}]), encoding="utf-8")

        self.runtime.retry_pending_cleanup()

        rejected = {"SomeOtherApp:token", "../../relay:device", "relay:",
                    "relay:" + "x" * 200, "provider:has space"}
        self.assertFalse(rejected & set(attempted),
                         "a name the app never mints must never be passed on")
        self.assertEqual(set(attempted) & {"remote-token", "relay:guest"},
                         {"remote-token", "relay:guest"},
                         "the names the app does mint are still cleaned up")
        self.assertEqual(self.runtime.storage.read_pending_cleanup(), [])


class BackgroundArchiveTests(unittest.IsolatedAsyncioTestCase):
    """The archive of a workspace nobody is looking at.

    Every workspace gets its own engine and its own poll, so this branch runs
    three seconds apart for all of them, for as long as the app is open.
    """

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.runtime = DesktopRuntime(Storage(self.directory.name, self.vault),
                                      lambda kind, data: self.events.append((kind, data)))
        await self.runtime.start()
        await self.runtime.create_workspace("Second")
        self.other_id, self.other = next((i, engine) for i, engine in self.runtime.engines.items()
                                         if i != self.runtime.active_workspace_id)
        self.saves = []
        original = self.other.history_store.save
        def counted(*args, **kwargs):
            self.saves.append(1)
            return original(*args, **kwargs)
        self.other.history_store.save = counted

    async def asyncTearDown(self):
        await self.runtime.close()
        self.directory.cleanup()

    def room(self, revision="r1", room_id="conversation-one"):
        return {"id": room_id, "revision": revision, "pending": False,
                "messages": [{"id": "m1", "role": "user", "from": "someone", "content": "hello"}]}

    async def test_an_unchanged_room_is_not_rewritten_to_disk(self):
        """The whole archive, twenty times a minute, to reproduce what is there.

        The listing arrives on every poll tick whether or not anything moved,
        and the rewrite below rebuilds every room and every message -- plus
        state["conversations"], which keeps the rooms raw, so an attachment
        still in. A revision is the room's own account of what has changed, so
        an unchanged one means the copy on disk is already right.
        """
        self.runtime.forward(self.other_id, "conversations", [self.room()])
        first = len(self.saves)
        self.assertTrue(self.saves, "nothing was written even the first time")

        self.runtime.forward(self.other_id, "conversations", [self.room()])
        self.assertEqual(len(self.saves), first,
                         "an unchanged room was rewritten to SQLite again")

        self.runtime.forward(self.other_id, "conversations", [self.room(revision="r2")])
        self.assertGreater(len(self.saves), first,
                           "a room whose revision moved was not written at all")

    async def test_a_room_deleted_elsewhere_is_still_removed_here(self):
        """The skip must not also skip the cleanup.

        Rooms that vanish from the listing are deleted from the archive, and
        that is the only code that does it. A guard that only compared
        revisions would leave a conversation deleted on another device in this
        workspace's archive for ever, because nothing would ever remove it.
        """
        self.runtime.forward(self.other_id, "conversations",
                             [self.room(), self.room("r1", "conversation-two")])
        saved = self.other.history_store.load("ui")["state"]["chats"]
        self.assertIn("conversation-two", saved)

        self.runtime.forward(self.other_id, "conversations", [self.room()])
        saved = self.other.history_store.load("ui")["state"]["chats"]
        self.assertNotIn("conversation-two", saved,
                         "a conversation deleted on another device was kept for good")
        self.assertIn("conversation-one", saved)

    async def test_a_locked_credential_store_is_not_reported_as_a_relay_failure(self):
        """Vault.get raises when the OS store is locked, and the read was unguarded.

        It escaped refresh() into the poll loop above it, which reported
        "Relay connection interrupted" -- blaming the relay for a keyring
        problem, and repeating it every three seconds.
        """
        class Locked:
            def get(self, name):
                raise RuntimeError("The OS credential store is unavailable.")

        self.other.storage.vault = Locked()
        # runners holds (client, task) pairs; only the client's ready event is
        # read on this path, and teardown cancels the task, so both are given.
        task = asyncio.ensure_future(asyncio.sleep(3600))
        self.addAsyncCleanup(task.cancel)
        self.other.runners["chat-agent"] = (unittest.mock.Mock(ready=asyncio.Event()), task)
        profiles = {"chat-agent": {"id": "chat-agent", "provider": "ollama",
                                   "model": "chat", "base_url": "http://127.0.0.1:1"}}

        result = await self.other.refresh_ollama_availability(profiles)

        self.assertNotIn("offline", [kind for kind, _ in self.events],
                         "a locked keyring was reported as a lost relay")
        self.assertIsInstance(result, dict)


class InvitationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """An invitation has to be showable more than once.

    It used to be readable exactly once, in the dialog that created it. Close
    it without copying and that device could never join, because the token was
    minted, saved to the credential store, and never written anywhere the
    reader could get at again -- and the only documented way out was to reset
    their own identity, which destroys every other identity in the workspace.
    """

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.runtime = DesktopRuntime(Storage(self.directory.name, self.vault),
                                      lambda kind, data: self.events.append((kind, data)))
        await self.runtime.start()
        self.engine = self.runtime.engine

    async def asyncTearDown(self):
        await self.runtime.close()
        self.directory.cleanup()

    async def test_an_invitation_can_be_shown_again_after_it_was_made(self):
        made = await self.runtime.invite("guest-laptop", self.runtime.active_url)

        assert await self.runtime.outstanding_invitations() == ["guest-laptop"], (
            "an invitation that was just made cannot be listed, so it cannot be "
            "shown again")

        again = await self.runtime.invitation_for("guest-laptop")
        assert again == made, (
            f"the invitation shown again is not the one that was issued:\n"
            f"  issued: {made}\n  again:  {again}")

    async def test_the_token_is_read_back_from_the_credential_store_not_stored(self):
        """A settings file copied off this machine must not carry anyone's access."""
        await self.runtime.invite("guest-laptop", self.runtime.active_url)
        recorded = self.engine.storage.settings["invitations"]["guest-laptop"]

        assert "token" not in recorded, (
            f"the token was written into settings.json: {recorded}")
        assert self.engine.storage.vault.get("relay:guest-laptop"), (
            "the token is not in the credential store, so it cannot be read back")

        written = json.loads(Path(self.directory.name, "settings.json").read_text())
        assert "guest-laptop" in written["invitations"], (
            "the invitation was not recorded at all, so it cannot be recovered")
        blob = json.dumps(written)
        token = self.engine.storage.vault.get("relay:guest-laptop")
        assert token not in blob, (
            "the relay token appears in the settings file on disk")

    async def test_a_removed_member_cannot_have_their_invitation_reissued(self):
        await self.runtime.invite("guest-laptop", self.runtime.active_url)
        await self.runtime.remove_member("guest-laptop")

        assert await self.runtime.outstanding_invitations() == [], (
            "a removed member is still offered an invitation, and the token it "
            "would read back has been deleted, so the offer only fails on use")
        with self.assertRaisesRegex(ValueError, "no saved invitation"):
            await self.runtime.invitation_for("guest-laptop")

    async def test_a_locked_credential_store_says_so_rather_than_issuing_a_broken_one(self):
        """An invitation missing its token looks valid until the guest is refused."""
        await self.runtime.invite("guest-laptop", self.runtime.active_url)

        class Locked:
            def get(self, name):
                raise RuntimeError("The OS credential store is unavailable.")

        self.engine.storage.vault = Locked()
        with self.assertRaisesRegex(ValueError, "credential store"):
            await self.runtime.invitation_for("guest-laptop")

    async def test_a_guest_is_never_shown_another_device_token(self):
        await self.runtime.invite("guest-laptop", self.runtime.active_url)
        self.engine.remote = True

        assert await self.runtime.outstanding_invitations() == [], (
            "a guest can list the invitations its host made")
        with self.assertRaisesRegex(ValueError, "host"):
            await self.runtime.invitation_for("guest-laptop")


class SettingsConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Two threads write the same dict the GUI reads.

    Storage.save() took the lock around json.dumps, but nothing took it around
    a mutation, and the dict is handed out by reference. A write landing inside
    a serialisation raises RuntimeError: dictionary changed size during
    iteration -- which surfaces at whichever command happened to be running
    rather than as what it is.
    """

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.storage = Storage(self.directory.name, self.vault)
        self.storage.update(theme="dark", relay_port=7000)
        self.errors = []

    async def asyncTearDown(self):
        self.directory.cleanup()

    def hammer(self, key, value):
        """Write the same key from another thread, as the network loop does."""
        for index in range(400):
            try:
                self.storage.update(**{key: f"{value}-{index}"})
            except Exception as error:
                self.errors.append(error)

    async def test_a_write_from_the_network_thread_cannot_break_a_save(self):
        import threading

        thread = threading.Thread(target=self.hammer, args=("relay_port", "port"))
        thread.start()
        for index in range(400):
            # What the GUI thread does while a chat is rendering.
            self.storage.update(theme="light" if index % 2 else "dark")
        thread.join()

        assert not self.errors, (
            f"{len(self.errors)} writes from the network thread failed against "
            f"the GUI thread's: {self.errors[0]!r}")

    async def test_every_guarded_entry_point_persists_what_it_changed(self):
        """update/discard/change write under the lock, and the write is the point."""
        self.storage.update(theme="light")
        assert json.loads(Path(self.directory.name, "settings.json").read_text())["theme"] == "light"

        self.storage.discard("theme")
        assert "theme" not in json.loads(Path(self.directory.name, "settings.json").read_text())

        self.storage.change(lambda settings: settings.__setitem__("theme", "light"))
        on_disk = json.loads(Path(self.directory.name, "settings.json").read_text())
        assert on_disk["theme"] == "light"

    async def test_a_read_modify_write_is_one_locked_act(self):
        """change() exists because the new value depends on the old one.

        Reading the dict outside the lock and writing inside it leaves the
        window in between, which is the bug; a caller that only has update()
        will reach for it and split the act.
        """
        import inspect

        signature = inspect.signature(Storage.change)
        assert list(signature.parameters) == ["self", "mutate"], (
            f"change() takes {list(signature.parameters)}, so it is not the "
            f"single-callable form the migration relies on")

    async def test_the_daemon_does_not_need_a_bare_assignment_to_persist(self):
        """The point of the helpers: no bare assignment, no second call."""
        self.storage.update(share_lan=True)
        assert json.loads(Path(self.directory.name, "settings.json").read_text())["share_lan"] is True

        # The old pattern, deliberately: change the dict, then remember to save.
        # This is what the GUI thread was doing concurrently with the above.
        self.storage.settings["reduce_motion"] = True
        self.storage.save()
        assert json.loads(Path(self.directory.name, "settings.json").read_text())["reduce_motion"] is True


class _HeldPeers(dict):
    """Relay peers that have not noticed a socket closing yet.

    The relay drops an identity the moment its socket closes, which is
    immediate, so a test that lets the real close happen is racing the thing it
    is trying to measure. This refuses to release one identity until it is told
    to -- which is precisely the condition the drain waits for.
    """

    held = False

    def pop(self, key, *args, **kwargs):
        if self.held and key == "researcher":
            return self.get(key)
        return super().pop(key, *args, **kwargs)

    def __delitem__(self, key):
        if self.held and key == "researcher":
            return
        super().__delitem__(key)


class EditedProfileTests(unittest.IsolatedAsyncioTestCase):
    """Reconnecting an agent that is already running.

    Editing a profile reuses its identity, so the replacement connects while the
    old registration may still be on the relay. Runner removal used to wait a
    fixed 100ms rather than for the relay to drop the old peer, so when the peer
    took longer than that to go -- a busy machine, a contended event loop -- the
    replacement was refused as a duplicate identity, leaving a model that was
    saved, listed as online, and not running, so every send came back "Target
    agent is offline".
    """

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.runtime = DesktopRuntime(Storage(self.directory.name, self.vault),
                                      lambda kind, data: self.events.append((kind, data)))
        await self.runtime.start()
        self.old_http = self.runtime.http
        self.replies = []

        async def transport(request):
            if request.url.host == "model.example":
                body = json.loads(request.content)
                self.replies.append(body)
                return httpx.Response(200, json={"choices": [
                    {"message": {"content": "Replied"}, "finish_reason": "stop"}]})
            return await self.old_http.send(request)

        self.runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.profile = {"id": "researcher", "provider": "bionic", "model": "test-model",
                        "base_url": "https://model.example/v1", "autostart": True}
        await self.runtime.save_agent(self.profile, "private-provider-key")

    async def asyncTearDown(self):
        await self.runtime.close()
        await self.old_http.aclose()
        self.directory.cleanup()

    async def test_removing_a_runner_waits_for_the_identity_to_be_released(self):
        """The wait is for the condition, not for an interval.

        Deterministic: the relay's peer table is told to keep the identity
        registered, which a real socket close would not do, so the only thing
        that can end the wait is the release itself. The old code slept 100ms
        and returned, so at 300ms -- three times that -- it was long done, and
        the replacement it was clearing the way for would be free to be refused
        as a duplicate.
        """
        import asyncio

        engine = self.runtime.engine
        relay = engine.app.state.relay
        original = relay.peers
        held = _HeldPeers(original)
        held.held = True
        relay.peers = held
        returned_early = None
        try:
            task = asyncio.ensure_future(engine._remove_runner("researcher"))
            await asyncio.sleep(.3)
            returned_early = task.done()
            held.held = False
            held.pop("researcher", None)
            await task
        finally:
            relay.peers = original

        self.assertFalse(returned_early,
                         "removing a runner returned while the relay still had "
                         "the identity registered, so the replacement was free "
                         "to be refused as a duplicate")

    async def test_an_edited_profile_is_left_running_and_able_to_answer(self):
        """The whole point: save an edit, then use the agent it edited."""
        edited = {**self.profile, "system_prompt": "Answer in one word."}
        await self.runtime.save_agent(edited, "private-provider-key")

        self.assertIn("researcher", self.runtime.engine.runners,
                      "the edited agent was saved but never launched, so the UI "
                      "lists a model that nothing is running behind")
        result = await self.runtime.send("researcher", {"text": "Hello after editing"})
        self.assertEqual(result["text"], "Replied")
        self.assertTrue(self.replies, "the agent was not actually called")

    async def test_editing_twice_still_leaves_one_working_agent(self):
        """Each edit replaces the last, and none of them is left half-attached."""
        for attempt in range(3):
            await self.runtime.save_agent(
                {**self.profile, "system_prompt": f"Attempt {attempt}"},
                "private-provider-key")
            self.assertIn("researcher", self.runtime.engine.runners,
                          f"attempt {attempt} left the agent not running")
        result = await self.runtime.send("researcher", {"text": "Hello after three edits"})
        self.assertEqual(result["text"], "Replied")

if __name__ == "__main__":
    unittest.main()


class PollBackoffTests(unittest.IsolatedAsyncioTestCase):
    """A repaired outage must not leave the poll quiet.

    The backoff lived in a local of _poll, which loops forever, so within one
    call it accumulated correctly and nothing outside could touch it. A guest
    that rejoined after its host restarted therefore kept a backoff earned by a
    fault that was already over and saw no agents at all while it waited out the
    rest of it -- up to five minutes on a client that was already connected.

    The state is on the engine now, so joining and Retry can both clear it.

    Built directly instead of through DesktopRuntime.start(). start() populates
    the engine and spawns the _poll loop, and that loop owns these same fields:
    it resets them on a successful pass and doubles them on a failed one, either
    of which lands between the setup and the assertion and decides the result.
    Nothing here needs a relay, a socket or a clock.
    """

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = WorkspaceRuntime(Storage(self.directory.name, MemoryVault()),
                                       lambda kind, data: None)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_the_backoff_is_state_someone_else_can_reach(self):
        """It starts on the engine, not inside the loop that owns it."""
        self.assertEqual(self.engine._poll_delay, 3)
        self.assertEqual(self.engine._poll_failures, 0)

    async def test_retry_clears_a_backoff_earned_by_an_outage_that_is_over(self):
        """The reader pressing Retry should not wait out a repaired fault."""
        called = []

        async def refresh():
            called.append(True)

        self.engine.refresh = refresh
        self.engine._poll_delay, self.engine._poll_failures = 300, 9
        await self.engine.retry_poll()
        self.assertTrue(called, "the stubbed refresh was never used")
        self.assertEqual(self.engine._poll_delay, 3)
        self.assertEqual(self.engine._poll_failures, 0)

    async def test_a_retry_that_fails_leaves_the_backoff_where_it_was(self):
        """Otherwise Retry would promise a cadence the relay cannot keep.

        Clearing on a failed retry would put the poll straight back to hammering
        a relay that is still gone, which is what the backoff is for.
        """
        async def refresh():
            raise OSError("relay still gone")

        self.engine.refresh = refresh
        self.engine._poll_delay, self.engine._poll_failures = 48, 5
        await self.engine.retry_poll()
        self.assertEqual(self.engine._poll_delay, 48)
        self.assertEqual(self.engine._poll_failures, 5)
