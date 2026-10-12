"""Recovering when the credential store no longer holds a saved token.

The behaviour being pinned down here is that a lost token degrades to a
fresh identity instead of making the app unstartable. It used to raise,
which left the user with no way back except deleting settings.json by
hand, and it happened whenever the credential store was cleared, locked
or moved to another machine.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_app.workspace_runtime import WorkspaceRuntime
from desktop_app.storage import ABSENT, REMOVED, UNAVAILABLE, Storage


class MemoryVault:
    """In-memory stand-in for the OS credential store.

    ``fail_writes`` can be flipped after start-up so a reset fails part
    way through, which is the case that used to destroy the old identity
    before the replacement had been stored. ``operations`` records the
    order of calls so the write-then-delete ordering can be asserted.
    """

    def __init__(self, broken=False):
        self.values = {}
        self.broken = broken
        self.fail_writes = False
        self.operations = []

    def get(self, name):
        if self.broken:
            raise RuntimeError("The OS credential store is unavailable.")
        self.operations.append(("get", name))
        return self.values.get(name)

    def set(self, name, value):
        if self.broken:
            raise RuntimeError("Could not save credentials to the OS credential store.")
        self.operations.append(("set", name))
        if self.fail_writes:
            raise RuntimeError("Could not save credentials to the OS credential store.")
        self.values[name] = value

    def delete(self, name):
        if self.broken:
            return UNAVAILABLE
        self.operations.append(("delete", name))
        return REMOVED if self.values.pop(name, None) is not None else ABSENT


# The relay rejects tokens shorter than 32 characters, so a fixture that
# mints one would fail validation before reaching the code under test.
SAVED_TOKEN = "saved-" + "t" * 40
OTHER_TOKEN = "other-" + "o" * 40
GUEST_TOKEN = "guest-" + "g" * 40


def storage_with_identity(vault, token=SAVED_TOKEN, store_token=True):
    """A settings file that already refers to one saved identity.

    ``store_token`` is False for the unavailable-store case: a broken
    vault refuses writes, so seeding the token would fail in the helper
    rather than in the behaviour under test.
    """
    directory = tempfile.mkdtemp()
    Path(directory, "settings.json").write_text(json.dumps({
        "device_id": "device-abc12345",
        "identities": {"device-abc12345": "workspace"},
    }), encoding="utf-8")
    if store_token:
        vault.set("relay:device-abc12345", token)
    return Storage(directory, vault)


class LostCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []

    def emit(self, kind, data):
        self.events.append((kind, data))

    async def test_missing_token_does_not_stop_startup(self):
        """A vanished token is replaced, and the app starts."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        # The token disappears, as a cleared credential store would do.
        del vault.values["relay:device-abc12345"]

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            device_id = storage.settings["device_id"]
            self.assertIn(device_id, runtime.credentials,
                          "the device should have been given a new token")
            self.assertNotEqual(runtime.credentials[device_id]["token"], SAVED_TOKEN)
        finally:
            await runtime.close()

    async def test_the_user_is_told_the_identity_changed(self):
        """Losing an identity silently would look like a broken workspace."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        del vault.values["relay:device-abc12345"]

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            notices = [data for kind, data in self.events if kind == "notice"]
            self.assertTrue(notices, "expected a notice about the new identity")
            self.assertIn("invitation", " ".join(notices))
        finally:
            await runtime.close()

    def test_the_dropped_identity_is_forgotten(self):
        """Settings stop referring to an identity that no longer exists."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        del vault.values["relay:device-abc12345"]

        storage.credentials()

        self.assertEqual(storage.dropped_identities, ["device-abc12345"])
        self.assertNotIn("device-abc12345", storage.settings["identities"])

    def test_intact_identities_are_untouched(self):
        """A healthy store loads exactly as before."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        credentials = storage.credentials()

        self.assertEqual(storage.dropped_identities, [])
        self.assertEqual(credentials["device-abc12345"]["token"], SAVED_TOKEN)

    def test_an_unavailable_store_is_still_fatal(self):
        """A broken store must not be silently treated as empty.

        The app cannot write a replacement either, so carrying on would
        give a device that silently fails on every relay instead of one
        that says what is wrong.
        """
        storage = storage_with_identity(MemoryVault(broken=True), store_token=False)

        with self.assertRaises(RuntimeError) as caught:
            storage.credentials()
        self.assertIn("unavailable", str(caught.exception))

    def test_only_the_missing_identity_is_dropped(self):
        """One bad identity does not discard the good ones."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        storage.settings["identities"]["device-good"] = "team"
        vault.set("relay:device-good", "good-token")
        # Only this one is lost; the other must survive.
        del vault.values["relay:device-abc12345"]

        credentials = storage.credentials()

        self.assertEqual(credentials["device-good"]["token"], "good-token")
        self.assertEqual(storage.dropped_identities, ["device-abc12345"])


class ResetIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        # Identity recovery must never start an installed model server on the test device.
        models = patch("desktop_app.ollama_service.OllamaService.models", return_value=[])
        models.start()
        self.addCleanup(models.stop)

    def emit(self, kind, data):
        self.events.append((kind, data))

    def test_forget_clears_settings_and_vault(self):
        """Forgetting an identity leaves nothing that could restore it."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        removed, undeleted = storage.forget_identities(["device-abc12345"])

        self.assertEqual(removed, ["device-abc12345"])
        self.assertEqual(undeleted, [], "a healthy store owes nothing")
        self.assertEqual(storage.settings["identities"], {})
        self.assertNotIn("relay:device-abc12345", vault.values)

    def test_a_refused_deletion_is_reported_rather_than_assumed(self):
        """The settings are pruned either way, but the caller is told.

        Reporting the token as deleted when the store would not remove it
        is how a superseded credential outlives the reset that retired it.
        """
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        def refuse(name):
            return UNAVAILABLE
        vault.delete = refuse

        removed, undeleted = storage.forget_identities(["device-abc12345"])

        self.assertEqual(removed, [])
        self.assertEqual(undeleted, ["device-abc12345"])
        self.assertEqual(storage.settings["identities"], {})
        self.assertIn("relay:device-abc12345", vault.values)

    def test_a_refused_deletion_is_recorded_before_the_caller_awaits(self):
        """The ledger write belongs here, not after the reset's await.

        The reset replaces the identities and then awaits, so a record made
        after that await can be lost to a cancellation, taking with it the
        only pointer to a superseded token that nothing else names.
        """
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        def refuse(name):
            return UNAVAILABLE
        vault.delete = refuse

        storage.forget_identities(["device-abc12345"])

        records = storage.read_pending_cleanup()
        self.assertEqual([(r["type"], r["source"], r["workspace"]) for r in records],
                         [("credentials", "reset", None)])
        self.assertEqual(records[0]["credentials"], ["relay:device-abc12345"])

    def test_the_record_is_keyed_by_whatever_asked_for_the_removal(self):
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        vault.delete = lambda name: UNAVAILABLE

        storage.forget_identities(["device-abc12345"], source="member")

        self.assertEqual([r["source"] for r in storage.read_pending_cleanup()], ["member"])

    def test_a_healthy_store_writes_no_ledger(self):
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        storage.forget_identities(["device-abc12345"])

        self.assertFalse(storage.ledger_path.exists(),
                         "nothing is owed, so nothing should be left on disk")

    def test_forget_keeps_the_device_id(self):
        """The token identifies the device, so a stable id is kept."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        storage.forget_identities(["device-abc12345"])

        self.assertEqual(storage.settings["device_id"], "device-abc12345")

    def test_forget_only_touches_the_named_identities(self):
        """The device's own entry is spared when it is not named.

        reset_identity writes the replacement under the device id before
        forgetting anything, so a reset that listed every identity would
        delete the token it had just written.
        """
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        storage.settings["identities"]["device-old"] = "team"
        vault.set("relay:device-old", "old-token")
        device_id = storage.settings["device_id"]

        storage.forget_identities(["device-old"])

        self.assertIn(device_id, storage.settings["identities"])
        self.assertIn("relay:" + device_id, vault.values)

    async def test_runtime_reset_gives_a_new_token(self):
        """The runtime path mints a fresh token and tells the user."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            first = runtime.credentials[storage.settings["device_id"]]["token"]
            result = await runtime.reset_identity()
            second = runtime.credentials[storage.settings["device_id"]]["token"]

            self.assertEqual(result["device_id"], "device-abc12345")
            self.assertNotEqual(first, second)
            self.assertTrue([1 for kind, data in self.events
                             if kind == "notice" and "identity" in str(data)])
        finally:
            await runtime.close()

    async def test_the_new_token_survives_the_reset(self):
        """The freshly written token must not be deleted afterwards.

        save_credentials overwrites relay:<device_id> in place, so
        forgetting every identity would remove the replacement too.
        """
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            result = await runtime.reset_identity()
            device_id = result["device_id"]

            self.assertIn("relay:" + device_id, vault.values)
            self.assertEqual(vault.values["relay:" + device_id],
                             runtime.credentials[device_id]["token"])
        finally:
            await runtime.close()

    async def test_the_replacement_is_written_before_anything_is_deleted(self):
        """A refused write must leave the old identity recoverable."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            await runtime.reset_identity()
            device_id = storage.settings["device_id"]

            ops = [(kind, name) for kind, name in vault.operations
                   if name == "relay:" + device_id]
            kinds = [kind for kind, _ in ops]
            self.assertEqual(kinds[-1], "set",
                             "the last write to the device entry must be the set")
            self.assertNotIn("delete", kinds,
                             "the replacement is stored under the same key")
        finally:
            await runtime.close()

    async def test_a_failed_save_keeps_the_old_identity(self):
        """The whole point of writing first: a failure costs nothing."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        device_id = storage.settings["device_id"]
        original = vault.values["relay:" + device_id]
        original_credentials = dict(runtime.credentials)
        generation = runtime.generation
        vault.fail_writes = True
        try:
            with self.assertRaises(Exception):
                await runtime.reset_identity()

            self.assertEqual(vault.values["relay:" + device_id], original)
            self.assertEqual(runtime.credentials, original_credentials)
            self.assertEqual(runtime.generation, generation,
                             "agents must not be stopped before the write succeeds")
        finally:
            vault.fail_writes = False
            await runtime.close()

    async def test_a_failed_save_leaves_the_relay_credentials_alone(self):
        """The relay keeps serving the identity it already knows."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        device_id = storage.settings["device_id"]
        before = dict(runtime.app.state.relay.credentials)
        vault.fail_writes = True
        try:
            with self.assertRaises(Exception):
                await runtime.reset_identity()

            self.assertEqual(runtime.app.state.relay.credentials, before)
            self.assertIn(device_id, runtime.app.state.relay.credentials)
        finally:
            vault.fail_writes = False
            await runtime.close()

    async def test_provider_profiles_get_new_credentials(self):
        """Otherwise every model agent silently fails to restart.

        _restore_profiles only relaunches an agent whose id is present in
        the credential set, so a reset that kept only the device identity
        would quietly disconnect all configured providers.
        """
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        storage.settings["agents"] = [{"id": "writer", "provider": "ollama",
                                       "model": "llama3"}]
        storage.settings["identities"]["writer"] = "workspace"
        vault.set("relay:writer", OTHER_TOKEN)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            before = runtime.credentials["writer"]["token"]
            result = await runtime.reset_identity()

            self.assertIn("writer", runtime.credentials)
            self.assertNotEqual(runtime.credentials["writer"]["token"], before)
            self.assertIn("writer", result["reissued"])
            self.assertEqual(result["skipped"], [])
            # The token written moments earlier must still be in the vault and
            # still be named in settings. Asserting only on runtime.credentials
            # missed the case where forget_identities deleted it right after
            # save_credentials stored it, leaving the agent alive in memory but
            # dead on the next launch.
            self.assertIn("relay:writer", vault.values)
            self.assertEqual(vault.values["relay:writer"],
                             runtime.credentials["writer"]["token"])
            self.assertIn("writer", storage.settings["identities"])
        finally:
            await runtime.close()

    async def test_a_profile_naming_the_device_id_is_not_duplicated(self):
        """The device entry already has a token; a second would overwrite it."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        device_id = storage.settings["device_id"]
        storage.settings["agents"] = [{"id": device_id, "provider": "ollama",
                                       "model": "llama3"}]

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            result = await runtime.reset_identity()

            self.assertEqual(result["reissued"], [])
            self.assertIn("relay:" + device_id, vault.values)
            self.assertEqual(vault.values["relay:" + device_id],
                             runtime.credentials[device_id]["token"])
        finally:
            await runtime.close()

    async def test_a_malformed_profile_is_skipped_and_reported(self):
        """A hand-edited agent name must not block the reset entirely."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        storage.settings["agents"] = [
            {"id": "not a valid id!", "provider": "ollama", "model": "llama3"},
            {"id": "writer", "provider": "ollama", "model": "llama3"},
        ]
        storage.settings["identities"]["writer"] = "workspace"
        vault.set("relay:writer", OTHER_TOKEN)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            result = await runtime.reset_identity()

            self.assertEqual(result["skipped"], ["not a valid id!"])
            self.assertIn("writer", runtime.credentials)
            self.assertTrue([1 for kind, data in self.events
                             if kind == "notice" and "not a valid id!" in str(data)],
                            "the user should be told which agent was skipped")
        finally:
            await runtime.close()

    async def test_a_joined_identity_is_forgotten_on_reset(self):
        """A joined identity goes, and only that one is counted as joined."""
        vault = MemoryVault()
        storage = storage_with_identity(vault)
        storage.settings["agents"] = [{"id": "writer", "provider": "ollama",
                                       "model": "llama3"}]
        storage.settings["identities"]["writer"] = "workspace"
        storage.settings["identities"]["device-guest"] = "team"
        vault.set("relay:writer", OTHER_TOKEN)
        vault.set("relay:device-guest", GUEST_TOKEN)

        runtime = WorkspaceRuntime(storage, self.emit)
        await runtime.start()
        try:
            result = await runtime.reset_identity()

            self.assertNotIn("relay:device-guest", vault.values)
            self.assertNotIn("device-guest", storage.settings["identities"])
            # A reissued profile was replaced, not joined, so counting it
            # here would tell the user a workspace appeared that did not.
            self.assertEqual(result["joined"], 1)
            self.assertIn("relay:writer", vault.values)
            self.assertIn("writer", storage.settings["identities"])
        finally:
            await runtime.close()


if __name__ == "__main__":
    unittest.main()
