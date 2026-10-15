import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import UNAVAILABLE, Storage
from tests.test_desktop_runtime import MemoryVault


class WorkspacePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault),
                                   lambda event, data: self.events.append((event, data)))
        await self.host.start()
        self.guest = None

    async def asyncTearDown(self):
        if self.guest:
            await self.guest.close()
        await self.host.close()
        self.directory.cleanup()

    async def attach_model(self, runtime, handler):
        if "model" not in runtime.credentials:
            await runtime.invite("model", runtime.active_url)
        await runtime._attach("model", runtime.credentials["model"]["token"], handler)

    async def test_workspace_deletion_discovers_keys_from_an_unfinished_agent_save(self):
        """Deleting root and scoped workspaces cleans search keys with no committed profile."""
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                if scoped:
                    await self.host.create_workspace("Unfinished model")
                engine = self.host.engine
                vault = engine.storage.vault
                original = vault.set
                profile = {"id": "unfinished", "provider": "bionic", "model": "test-model",
                           "base_url": "https://model.example/v1", "search_provider": "ollama",
                           "web_search": "auto", "autostart": False}

                def reject_relay(name, value):
                    """Fail relay creation after the new search credential has been stored."""
                    if name == "relay:unfinished":
                        raise RuntimeError("Relay creation failed")
                    original(name, value)

                with patch.object(vault, "set", reject_relay), patch.object(vault, "delete", return_value=UNAVAILABLE):
                    with self.assertRaisesRegex(RuntimeError, "recovery is pending"):
                        await self.host.save_agent(profile, "model-key", "search-key")
                self.assertNotIn("unfinished", engine.credentials)
                self.assertNotIn("unfinished", engine.app.state.relay.credentials)
                self.assertNotIn("unfinished", engine.storage.settings["identities"])
                self.assertFalse(any(p["id"] == "unfinished" for p in engine.storage.settings.get("agents", [])))
                names = engine.storage.agent_save_credential_names()
                self.assertEqual(vault.get("search:unfinished"), "search-key")
                await self.host.delete_workspace()
                self.assertFalse(engine.storage.agent_save_path.exists())
                for name in names:
                    self.assertIsNone(vault.get(name))
                self.assertEqual(self.host.storage.read_pending_cleanup(), [])

    async def test_failed_model_creation_recovers_before_workspace_restart(self):
        """Restart removes journal-owned search keys without inventing a model identity."""
        engine = self.host.engine
        original = engine.storage.vault.set

        def reject_relay(name, value):
            """Allow API-key writes and fail the new model's relay token creation."""
            if name == "relay:unfinished":
                raise RuntimeError("Relay creation failed")
            original(name, value)

        profile = {"id": "unfinished", "provider": "bionic", "model": "test-model",
                   "base_url": "https://model.example/v1", "search_provider": "ollama", "web_search": "auto"}
        with patch.object(engine.storage.vault, "set", reject_relay), patch.object(
                engine.storage.vault, "delete", return_value=UNAVAILABLE):
            with self.assertRaisesRegex(RuntimeError, "recovery is pending"):
                await self.host.save_agent(profile, "model-key", "search-key")
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        self.assertIsNone(self.host.engine.storage.vault.get("search:unfinished"))
        self.assertNotIn("unfinished", self.host.credentials)
        self.assertFalse(self.host.engine.storage.agent_save_path.exists())

    async def guest_join(self, invitation):
        self.guest = DesktopRuntime(Storage(Path(self.directory.name) / "guest", MemoryVault()))
        await self.guest.start()
        await self.guest.join(invitation["url"], invitation["token"], conversation_id=invitation.get("conversation_id"))

    async def test_full_history_survives_host_restart_and_model_context_is_restored(self):
        calls = []
        async def model(payload, source):
            calls.append(payload["messages"])
            return {"text": "Saved AI reply", "provider": "test"}
        await self.attach_model(self.host, model)
        history = [message for index in range(60) for message in (
            {"role": "user", "content": f"Remember fact {index}"},
            {"role": "assistant", "content": f"Acknowledged {index}"})]
        invitation = await self.host.invite("guest", self.host.active_url, target="model", messages=history)
        room_id = invitation["conversation_id"]
        await self.host.send_conversation(room_id, "Before closing")
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        await self.attach_model(self.host, model)
        await self.guest_join(invitation)
        result = await self.guest.send_conversation(room_id, "After reopening")
        self.assertEqual(len(result["messages"]), 124)
        self.assertEqual(result["messages"][0]["content"], "Remember fact 0")
        self.assertEqual(calls[-1][-3:], [{"role": "user", "content": "Before closing"},
            {"role": "assistant", "content": "Saved AI reply"}, {"role": "user", "content": "After reopening"}])
        self.assertLessEqual(len(calls[-1]), 100)
        self.assertLess(len(json.dumps(calls[-1]).encode()), 190000)

    async def test_workspaces_are_named_isolated_and_remain_hosted_when_switched(self):
        first_id = self.host.active_workspace_id
        first_url, first_device = self.host.active_url, self.host.active_id
        await self.host.rename_workspace("Research")
        invitation = await self.host.invite("guest", first_url)
        await self.guest_join(invitation)
        second = await self.host.create_workspace("Writing")
        self.assertNotEqual(first_url, self.host.active_url)
        self.assertNotEqual(first_device, self.host.active_id)
        self.assertNotIn("guest", self.host.credentials)
        result = await self.guest.send(first_device, {"text": "Works while owner views another workspace"})
        self.assertIn("Works while", result["text"])
        await self.host.close()
        await self.guest.close()
        self.guest = None
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        self.assertEqual(self.host.active_workspace_id, second["id"])
        self.assertEqual([entry["name"] for entry in self.host.catalog["workspaces"]], ["Research", "Writing"])
        await self.host.switch_workspace(first_id)
        self.assertEqual(self.host.active_id, first_device)
        self.assertIn("guest", self.host.credentials)
        saved = self.host.engine.history_store.load("ui")["state"]["chats"]["guest"]
        self.assertIn("Works while owner views another workspace", saved["messages"][0][1])

    async def test_a_background_workspace_forgets_a_room_deleted_elsewhere(self):
        """A room gone from a background workspace's listing goes from its archive.

        This branch only ever added rooms, so a conversation deleted on another
        device stayed in that workspace's saved chats for good, and came back
        if the room ever reappeared.
        """
        first_id = self.host.active_workspace_id
        await self.host.create_workspace("Elsewhere")
        store = self.host.engines[first_id].history_store

        room = {"id": "conversation-abc", "title": "Room", "target": "llama3",
                "owner": "owner", "members": ["owner", "guest"], "messages": [],
                "revision": 2, "pending": False}
        self.host.forward(first_id, "conversations", [room])
        saved = store.load("ui")["state"]
        self.assertIn("conversation-abc", saved["chats"])
        self.assertIn("conversation-abc", saved["conversations"])

        # It disappears from the relay: deleted on another device, or this one
        # was removed from it.
        self.host.forward(first_id, "conversations", [])
        saved = store.load("ui")["state"]
        self.assertNotIn("conversation-abc", saved["chats"], (
            "the archive kept a conversation the relay no longer lists"))
        self.assertNotIn("conversation-abc", saved["conversations"])

    async def test_a_background_reply_records_the_local_responder_in_the_role(self):
        """A reply reaching a background workspace keeps the responder readable.

        This branch used to write the agent id onto the front of the message
        text, which made it the only record of who had replied: the speaker
        logic could not see it, so those saved conversations render under the
        name of the peer who asked. It travels in the role now, the same way the
        active branch and ``member:<id>`` already do.
        """
        first_id = self.host.active_workspace_id
        await self.host.create_workspace("Elsewhere")
        self.assertNotEqual(self.host.active_workspace_id, first_id)
        store = self.host.engines[first_id].history_store

        self.host.forward(first_id, "incoming_reply", {
            "from": "guest", "to": "research-model", "text": "**Hi** from my agent."})
        saved = store.load("ui")["state"]["chats"]["guest"]
        self.assertEqual(saved["messages"],
                         [["local_agent:research-model", "**Hi** from my agent."]])

        # A failure names no responder, because nothing replied.
        self.host.forward(first_id, "incoming_reply", {
            "from": "guest", "to": "research-model",
            "text": "The local agent could not complete this request.", "error": True})
        saved = store.load("ui")["state"]["chats"]["guest"]
        self.assertEqual(saved["messages"][1],
                         ["error", "The local agent could not complete this request."])

    async def test_member_removal_revokes_tokens_and_shared_history_access_permanently(self):
        """Verify member removal revokes access and deletes the member's stored search key."""
        invitation = await self.host.invite("guest", self.host.active_url, target=self.host.active_id)
        await self.guest_join(invitation)
        self.host.engine.storage.vault.set("search:guest", "private-search-key")
        await self.host.remove_member("guest")
        self.assertNotIn("guest", self.host.credentials)
        self.assertNotIn("relay:guest", self.vault.values)
        self.assertIsNone(self.host.engine.storage.vault.get("search:guest"))
        base = self.host.active_url.replace("ws://", "http://").removesuffix("/connect")
        headers = {"Authorization": "Bearer " + invitation["token"]}
        self.assertEqual((await self.host.http.get(base + "/agents", headers=headers)).status_code, 401)
        self.assertEqual((await self.host.http.get(base + "/conversations", headers=headers)).status_code, 401)
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        self.assertNotIn("guest", self.host.credentials)
        self.assertNotIn("guest", self.host.app.state.relay.conversations.rooms[invitation["conversation_id"]]["members"])

    async def test_member_removal_says_so_when_a_token_cannot_be_deleted(self):
        """A revoked member whose token lingers is worth reporting.

        The member is out of the workspace either way, so the deletion
        succeeds. What must not happen is silence: a secret this device
        still holds for someone with no access is the part the owner
        would want to know about.
        """
        invitation = await self.host.invite("guest", self.host.active_url, target=self.host.active_id)
        await self.guest_join(invitation)
        original = self.vault.delete

        def locked(name):
            return UNAVAILABLE
        self.vault.delete = locked
        try:
            await self.host.remove_member("guest")
        finally:
            self.vault.delete = original

        self.assertNotIn("guest", self.host.credentials, "access is revoked either way")
        self.assertIn("relay:guest", self.vault.values, "the token could not be removed")
        notices = [data for kind, data in self.events
                   if kind == "notice" and "credential" in str(data).lower()]
        self.assertTrue(notices, "the user must be told the token survived")

    async def test_member_removal_is_quiet_when_the_token_is_gone(self):
        """The happy path should not cry wolf."""
        invitation = await self.host.invite("guest", self.host.active_url, target=self.host.active_id)
        await self.guest_join(invitation)

        await self.host.remove_member("guest")

        self.assertNotIn("relay:guest", self.vault.values)
        self.assertFalse([data for kind, data in self.events
                          if kind == "notice" and "credential" in str(data).lower()])

    async def test_guests_cannot_rename_or_remove_members_and_can_leave(self):
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(invitation)
        with self.assertRaisesRegex(ValueError, "owner"):
            await self.guest.rename_workspace("Unauthorised")
        with self.assertRaisesRegex(ValueError, "owner"):
            await self.guest.remove_member(self.host.active_id)
        remote_id = self.guest.active_workspace_id
        await self.guest.delete_workspace()
        self.assertNotIn(remote_id, {entry["id"] for entry in self.guest.catalog["workspaces"]})
        self.assertNotIn("guest", self.host.credentials)
        self.assertFalse(self.guest.remote)

    async def test_deletion_clears_only_selected_workspace_and_last_workspace_has_replacement(self):
        """Verify workspace deletion removes its credentials and preserves other workspace data."""
        root_id = self.host.active_workspace_id
        self.host.engine.history_store.save("ui", "state", {"chats": {"saved": {"messages": [["user", "Keep this"]]}}})
        second = await self.host.create_workspace("Temporary")
        deleted_vault = self.host.engine.storage.vault
        self.host.engine.storage.settings["agents"] = [{"id": "search-model"}]
        deleted_vault.set("search:search-model", "private-search-key")
        self.host.engine.storage.save()
        store = self.host.engine.history_store
        store.save("ui", "state", {"chats": {"deleted": {"messages": [["user", "Delete this"]]}}})
        await self.host.delete_workspace()
        self.assertEqual(self.host.active_workspace_id, root_id)
        self.assertIsNone(deleted_vault.get("search:search-model"))
        # The workspace's whole directory is removed, so its archive goes with
        # it rather than being emptied in place. The store object captured
        # above can no longer open its file, which is the point.
        self.assertFalse((Path(self.directory.name) / "host" / "workspaces" / second["id"]).exists())
        self.assertIn("Keep this", json.dumps(self.host.engine.history_store.load("ui")))
        self.assertNotIn(second["id"], {entry["id"] for entry in self.host.catalog["workspaces"]})
        await self.host.delete_workspace()
        self.assertEqual(len(self.host.catalog["workspaces"]), 1)
        self.assertNotEqual(self.host.active_workspace_id, root_id)
        self.assertFalse(self.host.engine.history_store.load("ui").get("state", {}).get("chats"))
        self.assertEqual(self.host.app.state.relay.conversations.rooms, {})

    async def test_deleted_workspace_notifications_cannot_recreate_state_or_break_replacement(self):
        removed = self.host.active_workspace_id
        original_close = self.host.engine.close
        async def close_with_late_events():
            self.host.forward(removed, "workspace_info", {"name": "Deleted"})
            self.host.forward(removed, "incoming", {"from": "guest", "text": "Too late"})
            await original_close()
        self.host.engine.close = close_with_late_events
        await self.host.delete_workspace()
        self.assertNotIn(removed, self.host.cache)
        self.assertNotIn(removed, self.host.engines)
        self.assertIn("Still running", (await self.host.send(self.host.active_id, {"text": "Still running"}))["text"])

    async def test_guest_can_forget_an_offline_workspace_without_losing_owned_workspaces(self):
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(invitation)
        owned_id = self.guest.catalog["workspaces"][0]["id"]
        remote_id, _store = self.guest.active_workspace_id, self.guest.engine.history_store
        await self.host.close()
        await self.guest.delete_workspace()
        self.assertEqual(self.guest.active_workspace_id, owned_id)
        self.assertNotIn(remote_id, {entry["id"] for entry in self.guest.catalog["workspaces"]})
        self.assertFalse((Path(self.directory.name) / "guest" / "workspaces" / remote_id).exists())
        self.assertIsNone(self.guest.storage.vault.get(remote_id + ":remote-token"))
        self.assertIn("guest", self.host.credentials)

    async def test_operator_managed_relays_allow_leaving_locally(self):
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(invitation)
        self.host.app.state.relay.member_remover = None
        remote_id = self.guest.active_workspace_id
        await self.guest.delete_workspace()
        self.assertNotIn(remote_id, {entry["id"] for entry in self.guest.catalog["workspaces"]})
        self.assertIn("guest", self.host.credentials)

    async def test_the_leave_endpoint_answers_every_way_it_can_be_called(self):
        """Every branch of leaving, including the two that say no.

        The desktop already covers the successful leave from its own side, but
        it decides that by the local catalogue, so it never sees what the relay
        answered. Nothing here asserted the relay's half: the owner's refusal
        is the branch that stops a host deleting itself, and an operator-managed
        relay is a deployment this app explicitly supports, so both are worth
        pinning to the status a caller can actually branch on.
        """
        import httpx
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(invitation)
        headers = {"Authorization": "Bearer " + invitation["token"]}
        base = f"http://127.0.0.1:{self.host.engine.port}"
        async with httpx.AsyncClient(timeout=10) as client:
            # 200, and the host really drops the member.
            response = await client.post(base + "/workspace/leave", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "left"})
            self.assertNotIn("guest", self.host.credentials)

            # 401 with no credentials at all.
            response = await client.post(base + "/workspace/leave")
            self.assertEqual(response.status_code, 401)

            # 403 for the owner. The owner holds the same token shape, so this
            # is the only thing standing between a host and removing itself.
            owner_headers = {"Authorization": "Bearer " + self.host.engine.active_token}
            response = await client.post(base + "/workspace/leave", headers=owner_headers)
            self.assertEqual(response.status_code, 403)
            self.assertIn("owner", response.json()["error"].lower())

            # 501 when the relay's operator manages membership itself.
            self.host.app.state.relay.member_remover = None
            again = await self.host.invite("second", self.host.active_url)
            second_headers = {"Authorization": "Bearer " + again["token"]}
            response = await client.post(base + "/workspace/leave", headers=second_headers)
            self.assertEqual(response.status_code, 501)

    async def test_a_second_workspace_is_refused_until_the_first_is_left(self):
        """One invitation at a time, and leaving is what releases the next.

        Two hosts would each be told this device's token is valid while neither
        had approved the other's members, so the second is refused with the
        name of the one to leave. Rejoining the workspace already joined is
        exempt, and that exemption is load-bearing rather than a nicety: the
        poll re-issues the same join to recover a dropped socket, and refusing
        it would strand the device in a workspace it could never re-enter.
        """
        first = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(first)
        joined = [entry for entry in self.guest.catalog["workspaces"] if entry["kind"] == "remote"]
        self.assertEqual([entry["url"] for entry in joined], [first["url"]])

        # The same invitation again, which is what reconnection does.
        await self.guest.join(first["url"], first["token"])
        self.assertEqual([entry["url"] for entry in self.guest.catalog["workspaces"]
                          if entry["kind"] == "remote"], [first["url"]])

        # A different host, refused, and refusing leaves the first intact.
        other = DesktopRuntime(Storage(Path(self.directory.name) / "other", MemoryVault()),
                               lambda event, data: None)
        await other.start()
        try:
            second = await other.invite("guest", other.active_url)
            with self.assertRaisesRegex(ValueError, "Leave that workspace"):
                await self.guest.join(second["url"], second["token"])
        finally:
            await other.close()
        self.assertTrue(self.guest.remote)
        self.assertEqual([entry["url"] for entry in self.guest.catalog["workspaces"]
                          if entry["kind"] == "remote"], [first["url"]])

        # And after leaving, the next invitation is accepted.
        await self.guest.delete_workspace()
        other2 = DesktopRuntime(Storage(Path(self.directory.name) / "other2", MemoryVault()),
                                lambda event, data: None)
        await other2.start()
        try:
            third = await other2.invite("guest", other2.active_url)
            await self.guest.join(third["url"], third["token"])
        finally:
            await other2.close()
        self.assertEqual([entry["url"] for entry in self.guest.catalog["workspaces"]
                          if entry["kind"] == "remote"], [third["url"]])

    async def test_legacy_remote_connection_and_provider_migrate_into_the_workspace_catalog(self):
        """Verify legacy connections and both model and search keys migrate into scoped storage."""
        invitation = await self.host.invite("legacy-guest", self.host.active_url)
        vault = MemoryVault()
        storage = Storage(Path(self.directory.name) / "guest", vault)
        storage.settings.update(remote={"url": invitation["url"], "allow_insecure": False}, remote_agent={
            "id": "legacy-guest", "provider": "bionic", "model": "test-model", "base_url": "https://model.example/v1",
            "autostart": True, "relay": invitation["url"]})
        vault.set("remote-token", invitation["token"])
        vault.set("provider:legacy-guest", "legacy-api-key")
        vault.set("search:legacy-guest", "legacy-search-key")
        storage.save()
        self.guest = DesktopRuntime(storage)
        await self.guest.start()
        remote_id = self.guest.active_workspace_id
        self.assertTrue(self.guest.remote)
        self.assertEqual(len(self.guest.catalog["workspaces"]), 2)
        self.assertEqual(self.guest.engine.storage.vault.get("provider:legacy-guest"), "legacy-api-key")
        self.assertEqual(self.guest.engine.storage.vault.get("search:legacy-guest"), "legacy-search-key")
        self.assertNotIn(invitation["token"], self.guest.catalog_path.read_text())
        await self.guest.delete_workspace()
        self.assertIsNone(vault.get(remote_id + ":provider:legacy-guest"))
        self.assertIsNone(vault.get("provider:legacy-guest"))
        self.assertIsNone(vault.get(remote_id + ":search:legacy-guest"))
        self.assertIsNone(vault.get("search:legacy-guest"))
        self.assertIsNone(vault.get("remote-token"))

    async def test_a_legacy_root_token_that_will_not_delete_is_retried(self):
        """The pre-catalog layout's tokens live on the root vault.

        They are a different entry from the workspace's own identically
        named ones, so a retry that treated them as workspace-scoped would
        look for <workspace-id>:remote-token and leave the root token in
        place forever. They also cannot be re-derived afterwards, because the
        pass that fails to delete them is the pass that removes the root
        settings naming them.
        """
        invitation = await self.host.invite("legacy-guest", self.host.active_url)
        vault = MemoryVault()
        storage = Storage(Path(self.directory.name) / "guest", vault)
        storage.settings.update(remote={"url": invitation["url"], "allow_insecure": False},
                                remote_agent={"id": "legacy-guest", "provider": "bionic",
                                              "model": "test-model", "base_url": "https://model.example/v1",
                                              "autostart": True, "relay": invitation["url"]})
        vault.set("remote-token", invitation["token"])
        vault.set("provider:legacy-guest", "legacy-api-key")
        vault.set("search:legacy-guest", "legacy-search-key")
        storage.save()
        self.guest = DesktopRuntime(storage)
        await self.guest.start()
        remote_id = self.guest.active_workspace_id
        self.guest.app.state.relay.member_remover = None
        # The store refuses the root entries, so the workspace's own
        # credentials are cleaned up as normal. A workspace-scoped name
        # arrives already prefixed with the workspace id.
        original = vault.delete

        def locked_root(name):
            if name.startswith("workspace-"):
                return original(name)
            return UNAVAILABLE
        vault.delete = locked_root
        try:
            await self.guest.delete_workspace()
        finally:
            vault.delete = original

        records = self.guest.storage.read_pending_cleanup()
        self.assertEqual([r["id"] for r in records], [remote_id])
        self.assertEqual(sorted(records[0]["root_credentials"]),
                         ["provider:legacy-guest", "remote-token", "search:legacy-guest"],
                         "the root names must be recorded separately")
        self.assertEqual(records[0]["credentials"], [],
                         "the workspace's own credentials were deleted")
        self.assertEqual(vault.get("remote-token"), invitation["token"])
        self.assertEqual(vault.get("provider:legacy-guest"), "legacy-api-key")
        self.assertEqual(vault.get("search:legacy-guest"), "legacy-search-key")
        self.assertIsNone(self.guest.storage.settings.get("remote"),
                          "the description that named them is already gone")

        # Next launch, with the store unlocked.
        await self.guest.close()
        self.guest = DesktopRuntime(Storage(Path(self.directory.name) / "guest", vault))
        await self.guest.start()

        self.assertIsNone(vault.get("remote-token"))
        self.assertIsNone(vault.get("provider:legacy-guest"))
        self.assertIsNone(vault.get("search:legacy-guest"))
        self.assertEqual(self.guest.storage.read_pending_cleanup(), [])
        self.assertNotIn(remote_id + ":remote-token", vault.values,
                         "the retry must not look behind the workspace prefix")

    async def test_a_member_token_that_will_not_delete_is_retried_on_the_next_launch(self):
        """The notice promises a retry, so there must be something to retry."""
        invitation = await self.host.invite("guest", self.host.active_url, target=self.host.active_id)
        await self.guest_join(invitation)
        self.vault.set("search:guest", "private-search-key")
        original = self.vault.delete

        def locked(name):
            """Simulate a locked credential store that cannot complete a deletion."""
            return UNAVAILABLE
        self.vault.delete = locked
        try:
            await self.host.remove_member("guest")
        finally:
            self.vault.delete = original

        records = self.host.storage.read_pending_cleanup()
        self.assertEqual([(r["type"], r["source"], r["workspace"]) for r in records],
                         [("credentials", "member", None)])
        self.assertEqual(sorted(records[0]["credentials"]),
                         ["provider:guest", "relay:guest", "search:guest"])
        self.assertIn("relay:guest", self.vault.values)

        await self.guest.close()
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()

        self.assertNotIn("relay:guest", self.vault.values)
        self.assertNotIn("provider:guest", self.vault.values)
        self.assertNotIn("search:guest", self.vault.values)
        self.assertEqual(self.host.storage.read_pending_cleanup(), [])

    async def test_an_owed_token_record_does_not_remove_a_live_workspace(self):
        """A record about credentials must not be read as a deletion.

        Both kinds share one file. Handled by the workspace-deletion path, a
        credential record would match its own workspace in the catalog and
        delete it on the next launch, and would delete the name through the
        root vault instead of the workspace's own.
        """
        second = await self.host.create_workspace("Writing")
        live = second["id"]
        live_token = "relay:device-livehost"
        self.vault.set(live_token, "the host's own working token")
        self.host.storage.write_pending_cleanup(
            self.host.storage.read_pending_cleanup()
            + [{"type": "credentials", "workspace": live, "source": "reset",
                "credentials": [live_token]}])

        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()

        self.assertIn(live, {e["id"] for e in self.host.catalog["workspaces"]},
                      "only credentials were owed; the workspace stays")
        self.assertEqual(self.vault.get(live_token), "the host's own working token",
                         "a workspace-scoped record must not delete the root entry")
        self.assertEqual(self.host.storage.read_pending_cleanup(), [])

    async def test_interrupted_request_is_recovered_without_replaying_model(self):
        invitation = await self.host.invite("guest", self.host.active_url, target=self.host.active_id)
        room = self.host.app.state.relay.conversations.rooms[invitation["conversation_id"]]
        room["pending"] = True
        self.host.app.state.relay.conversations.save(room)
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        restored = self.host.app.state.relay.conversations.rooms[room["id"]]
        self.assertFalse(restored["pending"])
        self.assertEqual(restored["messages"][-1]["role"], "error")
        self.assertIn("not replayed", restored["messages"][-1]["content"])
