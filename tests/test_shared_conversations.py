import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import MagicMock, patch

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage
from tests.test_desktop_runtime import MemoryVault


class SharedConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.events = []
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", MemoryVault()))
        self.guest = DesktopRuntime(Storage(Path(self.directory.name) / "guest", MemoryVault()),
                                    lambda kind, data: self.events.append((kind, data)))
        await self.host.start()
        await self.guest.start()

    async def asyncTearDown(self):
        await self.guest.close()
        await self.host.close()
        self.directory.cleanup()

    async def model(self, handler):
        invitation = await self.host.invite("model", self.host.active_url)
        await self.host._attach("model", invitation["token"], handler)

    async def invitation(self, history=()):
        return await self.host.invite("guest", self.host.active_url, target="model", messages=history)

    async def join(self, invitation):
        await self.guest.join(invitation["url"], invitation["token"],
                              conversation_id=invitation["conversation_id"])

    async def test_only_the_host_can_end_a_shared_conversation_and_a_member_leaves(self):
        """The workspace's own rule, applied to one conversation.

        Whoever started a conversation may end it for everyone. Anyone else
        steps out of it, which keeps the room and its messages for the rest.
        There was no way to do either before, so a conversation could not be
        ended at all: it only went away by being left behind.
        """
        async def model(payload, source):
            return {"text": "AI: " + payload["messages"][-1]["content"], "provider": "test"}
        await self.model(model)
        invitation = await self.invitation([{"role": "user", "content": "Before inviting"}])
        await self.join(invitation)
        room_id = invitation["conversation_id"]

        # A member cannot end it, and says why rather than failing silently.
        with self.assertRaises(RuntimeError) as refusal:
            await self.guest.delete_conversation(room_id)
        self.assertIn("host", str(refusal.exception))
        self.assertIn(room_id, self.host.engine.history_store.load("rooms"), (
            "a refused delete still removed the conversation"))

        # Nor can the host step out of it; that would leave it with no owner.
        with self.assertRaises(RuntimeError):
            await self.host.leave_conversation(room_id)

        # A member leaving keeps the room, for everyone else.
        self.assertTrue(await self.guest.leave_conversation(room_id))
        await self.guest.refresh()
        self.assertEqual([room["id"] for room in
                          [data for kind, data in self.events if kind == "conversations"][-1]], [])
        self.assertIn(room_id, self.host.engine.history_store.load("rooms"), (
            "a member leaving removed the conversation for everyone"))

        # And the host can end it for good.
        self.assertTrue(await self.host.delete_conversation(room_id))
        self.assertNotIn(room_id, self.host.engine.history_store.load("rooms"))
        await self.host.refresh()
        self.assertEqual([data for kind, data in self.events if kind == "conversations"][-1], [])

    async def test_only_a_room_that_is_gone_counts_as_a_shared_conversation_done(self):
        """Only 200 and 404 mean the change was made.

        404 is the room not being here, which is also what a relay predating
        these routes answers for an unregistered path, so an older host lands
        here too and the local copy is forgotten with nothing to lose.

        Everything else is a refusal. 401 says this device may not make the
        change; 501 says the relay declined to. Neither is true of
        delete_workspace, which tolerates both: there a revoked token means the
        membership is already gone, and an operator-managed relay is a decision
        about their own server. On a conversation the room and everyone else's
        messages of it are still there either way, so reading those as success
        forgets something that has not gone anywhere.
        """
        async def model(payload, source):
            return {"text": "AI: " + payload["messages"][-1]["content"], "provider": "test"}
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        room_id = invitation["conversation_id"]

        class Response:
            def __init__(self, status):
                self.status_code = status
            def json(self):
                return {}

        async def call(url, headers=None, timeout=None):
            return Response(status)

        # The refusals first, while the room record is still in place, so what
        # follows is down to them and not to the tolerated delete that comes
        # after. Neither may be read as a done job.
        for status, expected in ((401, "not a member"), (501, "does not support")):
            with self.subTest(status=status):
                engine = self.guest.connected_engine()
                stub = MagicMock()

                async def refuses(url, headers=None, timeout=None, code=status):
                    return Response(code)

                stub.delete = refuses
                stub.post = refuses
                with patch.object(engine, "relay_http", return_value=stub):
                    with self.assertRaises(RuntimeError) as message:
                        await self.guest.delete_conversation(room_id)
                    self.assertIn(expected, str(message.exception))
                    with self.assertRaises(RuntimeError):
                        await self.guest.leave_conversation(room_id)
                self.assertIn(room_id, self.host.engine.history_store.load("rooms"), (
                    f"a {status} was treated as a delete, so the room went from "
                    f"the relay while it still has everyone else's messages of it"))

        await self.guest.refresh()
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertEqual([room["id"] for room in rooms], [room_id], (
            "a refusal left the conversation missing from the relay, so it was "
            "treated as deleted rather than refused"))

        # Only the room being gone.
        for status in (404,):
            with self.subTest(status=status):
                engine = self.guest.connected_engine()
                stub = MagicMock()
                stub.delete = call
                stub.post = call
                with patch.object(engine, "relay_http", return_value=stub):
                    self.assertTrue(await self.guest.delete_conversation(room_id))
                    self.assertTrue(await self.guest.leave_conversation(room_id))

        # A refusal from a relay that does have the endpoint is still a refusal.
        engine = self.guest.connected_engine()
        refusal = Response(403)
        refusal.json = lambda: {"error": "Only the conversation's host can delete it."}
        stub = MagicMock()

        async def refuse(url, headers=None, timeout=None):
            return refusal

        stub.delete = refuse
        with patch.object(engine, "relay_http", return_value=stub):
            with self.assertRaises(RuntimeError) as message:
                await self.guest.delete_conversation(room_id)
        self.assertIn("host", str(message.exception))

    async def test_join_adds_same_history_and_both_devices_extend_one_model_context(self):
        calls = []
        async def model(payload, source):
            calls.append(payload["messages"])
            return {"text": "AI: " + payload["messages"][-1]["content"], "provider": "test"}
        await self.model(model)
        history = [{"role": "user", "content": "Before inviting"},
                   {"role": "assistant", "content": "Existing AI reply"}]
        invitation = await self.invitation(history)
        await self.join(invitation)
        room_id = invitation["conversation_id"]
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertEqual(rooms[0]["id"], room_id)
        self.assertEqual([message["content"] for message in rooms[0]["messages"]],
                         [message["content"] for message in history])
        guest_reply = await self.guest.send_conversation(room_id, "From the guest")
        host_reply = await self.host.send_conversation(room_id, "From the host")
        self.assertFalse(guest_reply["pending"])
        self.assertFalse(host_reply["pending"])
        self.assertEqual(calls[-1], history + [
            {"role": "user", "content": "From the guest"},
            {"role": "assistant", "content": "AI: From the guest"},
            {"role": "user", "content": "From the host"}])
        await self.guest.refresh()
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertEqual(rooms[0], host_reply)
        self.assertEqual(rooms[0]["messages"][-2]["from"], self.host.active_id)
        self.assertNotIn(invitation["token"], json.dumps(rooms))

    async def test_large_image_crosses_http_and_websocket_and_is_saved_for_followups(self):
        calls = []
        async def model(payload, source):
            calls.append(payload["messages"])
            return {"text": "Understood attached image", "provider": "test"}
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        room_id = invitation["conversation_id"]
        image = {"type": "image", "name": "large.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 300000).decode()}
        content = [{"type": "text", "text": "Understand the picture"}, image]
        reply = await self.guest.send_conversation(room_id, content)
        self.assertEqual(reply["messages"][0]["content"], content)
        self.assertEqual(calls[0][0]["content"], content)
        stored = self.host.history_store.load("rooms")[room_id]
        self.assertEqual(stored["messages"][0]["content"], content)
        await self.host.send_conversation(room_id, "Remember that picture?")
        self.assertEqual(calls[-1][0]["content"], content)
        self.assertEqual(calls[-1][-1]["content"], "Remember that picture?")

    async def test_remote_model_metadata_is_authenticated_and_excludes_private_settings(self):
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest.join(invitation["url"], invitation["token"])
        profile = {"id": "guest", "provider": "ollama", "model": "vision-model", "vision": True,
                   "searxng_url": "http://localhost:8888", "system_prompt": "PRIVATE INSTRUCTIONS"}
        await self.guest.publish_profile(profile)
        await self.host.refresh()
        base = f"http://127.0.0.1:{self.host.port}"
        headers = {"Authorization": "Bearer " + self.host.active_token}
        agents = (await self.host.http.get(base + "/agents", headers=headers)).json()["agents"]
        self.assertEqual([agent["id"] for agent in agents if agent["kind"] == "model"], ["guest"])
        guest = next(agent for agent in agents if agent["id"] == "guest")
        self.assertTrue(guest["vision"])
        self.assertEqual(guest["model"], "vision-model")
        self.assertNotIn("PRIVATE", json.dumps(agents))
        self.assertNotIn("searxng", json.dumps(agents))
        public = self.guest.public_profile(profile)
        self.assertEqual((await self.host.http.post(base + "/agent-profile", json=public)).status_code, 401)
        guest_headers = {"Authorization": "Bearer " + invitation["token"]}
        self.assertEqual((await self.host.http.post(base + "/agent-profile", headers=guest_headers,
                                                   json={**public, "id": self.host.active_id})).status_code, 400)
        await self.guest.publish_profile(profile, running=False)
        guest = next(agent for agent in (await self.host.http.get(base + "/agents", headers=headers)).json()["agents"]
                     if agent["id"] == "guest")
        self.assertFalse(guest["online"])

    async def test_uninvited_device_cannot_read_or_send_to_conversation(self):
        async def model(payload, source):
            return {"text": "reply"}
        await self.model(model)
        invitation = await self.invitation()
        outsider = await self.host.invite("outsider", self.host.active_url)
        base = f"http://127.0.0.1:{self.host.port}"
        headers = {"Authorization": "Bearer " + outsider["token"]}
        room_url = base + "/conversations/" + invitation["conversation_id"]
        self.assertEqual((await self.host.http.get(base + "/conversations", headers=headers)).json(),
                         {"conversations": []})
        self.assertEqual((await self.host.http.get(room_url, headers=headers)).status_code, 404)
        self.assertEqual((await self.host.http.post(room_url + "/messages", headers=headers,
                                                   json={"text": "uninvited"})).status_code, 404)
        self.assertEqual((await self.host.http.get(room_url)).status_code, 401)
        identity = self.guest.active_id
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await self.guest.join(outsider["url"], outsider["token"],
                                  conversation_id=invitation["conversation_id"])
        self.assertEqual(self.guest.active_id, identity)
        self.assertFalse(self.guest.remote)

    async def test_simultaneous_message_is_rejected_without_duplicate_model_call(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def model(payload, source):
            calls.append(payload)
            entered.set()
            await release.wait()
            return {"text": "one reply"}
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        room_id = invitation["conversation_id"]
        request = asyncio.create_task(self.guest.send_conversation(room_id, "First"))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await self.guest.refresh()
            self.assertTrue([data for kind, data in self.events if kind == "conversations"][-1][0]["pending"])
            with self.assertRaisesRegex(RuntimeError, "Wait for"):
                await self.host.send_conversation(room_id, "Concurrent")
        finally:
            release.set()
            await request
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.host.app.state.relay.conversations.rooms[room_id]["messages"]), 2)

    async def test_saved_guest_recovers_conversation_after_restarting(self):
        async def model(payload, source):
            return {"text": "saved reply"}
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        await self.guest.send_conversation(invitation["conversation_id"], "Before reconnect")
        storage = self.guest.storage
        await self.guest.close()
        self.events.clear()
        self.guest = DesktopRuntime(storage, lambda kind, data: self.events.append((kind, data)))
        await self.guest.start()
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertTrue(self.guest.remote)
        self.assertEqual(rooms[0]["messages"][-1]["content"], "saved reply")

    async def test_failed_ai_reply_is_visible_to_every_member_and_unblocks_chat(self):
        async def model(payload, source):
            raise RuntimeError("private failure")
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            await self.guest.send_conversation(invitation["conversation_id"], "A request")
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertEqual(rooms[0]["messages"][-1]["role"], "error")
        self.assertFalse(rooms[0]["pending"])
        self.assertNotIn("private failure", json.dumps(rooms))
