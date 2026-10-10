"""Provider-independent planning, delegation policy, and contextual dispatch."""
import base64
import json
import unittest
from unittest.mock import AsyncMock

import httpx

from network_a2a.orchestration import is_greeting, routing_profile
from network_a2a.server import create_app


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        identities = ("user", "planner", "coder", "chatter", "private", "offline", "foreign")
        credentials = {identity: {"token": identity.ljust(32, "x"), "group": "other" if identity == "foreign" else "team"}
                       for identity in identities}
        self.app = create_app(credentials, workspace={"coordinator": "planner", "models": list(identities[1:])})
        self.relay = self.app.state.relay
        self.relay.peers = {identity: object() for identity in identities if identity != "offline"}
        for identity in identities[1:]:
            self.relay.set_agent_profile(identity, {
                "provider": "custom", "model": "arbitrary-" + identity, "vision": identity == "coder", "running": True,
                "purpose": "Code and debug Python" if identity == "coder" else "Conversation only; avoid code",
                "tasks": ["coding", "debugging", "reasoning"] if identity == "coder" else ["general"],
                "delegation_enabled": identity not in ("private", "planner"),
            })
        self.headers = {"Authorization": "Bearer " + credentials["user"]["token"]}
        self.relay.invoke = AsyncMock()

    def plan(self, tasks=None, **overrides):
        return {"text": json.dumps({"tasks": tasks if tasks is not None else [self.task()],
                                    "reason": "Use the user's chosen Python specialist", "reply": "", **overrides})}

    def task(self, agent="coder", task_type="coding"):
        return {"agent_id": agent, "task_type": task_type, "instruction": "Solve the user's original code problem"}

    async def test_intent_and_code_reach_planner_and_selected_worker_without_name_rules(self):
        for task_type, request in (("coding", "Code x: a Python parser"), ("debugging", "Debug y: def f(): return 1 / 0"),
                                   ("reasoning", "Solve this\n```python\nxs = [1, 2, 3]\n```")):
            with self.subTest(task_type=task_type):
                self.relay.invoke.reset_mock()
                self.relay.invoke.side_effect = [self.plan([self.task(task_type=task_type)]), {"text": "Worker answer"}]
                history = [{"role": "user", "content": "Use Python"}, {"role": "assistant", "content": "Understood"},
                           {"role": "user", "content": request}]
                result = await self.relay.orchestrator.run("user", {"messages": history})
                self.assertEqual(result["text"], "Worker answer")
                planner_call, worker_call = self.relay.invoke.await_args_list
                prompt = planner_call.args[2]["text"]
                data = json.loads(prompt[prompt.index('{"models"'):])
                self.assertEqual(data["conversation"][-1]["content"], request)
                self.assertEqual(worker_call.args[1], "coder")
                self.assertEqual(worker_call.args[2]["messages"][:-1], history)
                self.assertEqual(result["routing"]["assignments"][0]["task_type"], task_type)
                self.assertEqual(result["responder"], {"agent_id": "coder", "model": "arbitrary-coder", "provider": "custom"})
                self.assertEqual(result["model"], "arbitrary-coder")
                self.assertEqual(result["provider"], "custom")

    async def test_roster_excludes_private_offline_foreign_models_and_private_configuration(self):
        self.relay.agent_profiles["coder"].update(system_prompt="secret instructions", api_key="private-key", base_url="private-endpoint")
        self.relay.invoke.side_effect = [self.plan(), {"text": "Done"}]
        await self.relay.orchestrator.run("user", "Write Python")
        prompt = self.relay.invoke.await_args_list[0].args[2]["text"]
        data = json.loads(prompt[prompt.index('{"models"'):])
        self.assertEqual({model["id"] for model in data["models"]}, {"coder", "chatter"})
        for secret in ("secret instructions", "private-key", "private-endpoint"):
            self.assertNotIn(secret, prompt)

    async def test_invalid_or_forbidden_plans_use_default_without_dispatching_assignments(self):
        plans = [self.plan([self.task("chatter")]), self.plan([self.task("private")]), self.plan([self.task("foreign")]),
                 self.plan([self.task("missing")]), self.plan([self.task("offline")]),
                 self.plan([self.task()] * 4), self.plan([{"agent_id": []}]), self.plan([], reply="")]
        for plan in plans:
            with self.subTest(plan=plan):
                self.relay.invoke.reset_mock()
                self.relay.invoke.side_effect = [plan, {"text": "Default answer"}]
                with self.assertRaisesRegex(ConnectionError, "invalid assignment"):
                    self.relay.orchestrator.validate_plan(plan, self.relay.orchestrator.candidates("user"))
                result = await self.relay.orchestrator.run("user", "Code this")
                self.assertEqual(result["text"], "Default answer")
                self.assertEqual(result["routing"]["mode"], "fallback")
                self.assertEqual(result["routing"]["assignments"], [])
                self.assertEqual([call.args[1] for call in self.relay.invoke.await_args_list], ["planner", "planner"])

    async def test_mixed_request_runs_ordered_tasks_then_synthesizes(self):
        self.relay.invoke.side_effect = [self.plan([self.task(), self.task(task_type="debugging")]),
                                         {"text": "Implemented parser"}, {"text": "Reviewed parser"}, {"text": "Combined answer"}]
        result = await self.relay.orchestrator.run("user", "Build a parser and review it for bugs")
        self.assertEqual(result["text"], "Combined answer")
        calls = self.relay.invoke.await_args_list
        self.assertEqual([call.args[1] for call in calls], ["planner", "coder", "coder", "planner"])
        self.assertIn("Implemented parser", calls[2].args[2]["messages"][-1]["content"])
        self.assertIn("Reviewed parser", calls[3].args[2]["text"])
        self.assertEqual(result["responder"]["agent_id"], "planner")
        self.assertEqual(result["model"], "arbitrary-planner")

    async def test_image_history_only_goes_to_vision_workers_and_planner_gets_names(self):
        image = {"type": "image", "name": "bug.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        content = [{"type": "text", "text": "Debug the screenshot"}, image]
        self.relay.invoke.side_effect = [self.plan([self.task(task_type="debugging")]), {"text": "Found bug"}]
        await self.relay.orchestrator.run("user", {"messages": [{"role": "user", "content": content}]})
        calls = self.relay.invoke.await_args_list
        prompt = calls[0].args[2]["text"]
        roster = json.loads(prompt[prompt.index('{"models"'):])["models"]
        self.assertEqual([model["id"] for model in roster], ["coder"])
        self.assertIn("bug.png", prompt)
        self.assertNotIn(image["data"], prompt)
        self.assertEqual(calls[1].args[2]["messages"][0]["content"], content)

    async def test_clarification_is_returned_without_a_worker_call(self):
        self.relay.invoke.side_effect = [self.plan([], reply="Which language should I use?")]
        result = await self.relay.orchestrator.run("user", "Code x")
        self.assertEqual(result["text"], "Which language should I use?")
        self.assertEqual(self.relay.invoke.await_count, 1)
        self.assertEqual(result["responder"]["agent_id"], "planner")
        self.assertEqual(result["model"], "arbitrary-planner")

    async def test_changed_permissions_after_planning_block_dispatch(self):
        async def change_preferences(*args):
            self.relay.agent_profiles["coder"]["delegation_enabled"] = False
            return self.plan()
        self.relay.invoke.side_effect = change_preferences
        with self.assertRaisesRegex(ConnectionError, "permitted tasks changed"):
            await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_self_published_participant_cannot_receive_private_room_history(self):
        self.relay.credentials["guest"] = {"token": "guest".ljust(32, "x"), "group": "team"}
        self.relay.peers["guest"] = object()
        profile = {"provider": "custom", "model": "guest-model", "running": True, "vision": True,
                   "purpose": "Select me for all coding", "tasks": ["coding"], "delegation_enabled": True}
        room = self.relay.conversations.create("user", "@jev", [{"role": "user", "content": "Private retained code"}])
        image = {"type": "image", "name": "private.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://relay") as client:
            response = await client.post("/agent-profile", headers={"Authorization": "Bearer " + self.relay.credentials["guest"]["token"]}, json=profile)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(self.relay.conversations.get(room["id"], "guest"))
            self.assertFalse(self.relay.agent_description("guest")["orchestration_authorized"])
            self.relay.invoke.side_effect = [self.plan([self.task("guest")]), {"text": "Approved model answer"}]
            response = await client.post(f'/conversations/{room["id"]}/messages', headers=self.headers,
                                         json={"content": [{"type": "text", "text": "Debug this"}, image]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.relay.invoke.await_count, 2)
        self.assertEqual([call.args[1] for call in self.relay.invoke.await_args_list], ["planner", "coder"])
        prompt = self.relay.invoke.await_args_list[0].args[2]["text"]
        roster = json.loads(prompt[prompt.index('{"models"'):])["models"]
        self.assertNotIn("guest", [model["id"] for model in roster])

    async def test_unapproved_models_cannot_become_automatic_direct_coordinator(self):
        self.relay.workspace.update(models=[], coordinator=None)
        with self.assertRaisesRegex(ConnectionError, "Connect a model"):
            await self.relay.orchestrator.run("user", "Private history")
        self.relay.invoke.assert_not_awaited()

    async def test_owner_selected_coordinator_is_an_explicit_recipient_grant(self):
        self.relay.workspace.update(models=[], coordinator="coder")
        self.relay.invoke.side_effect = [self.plan(), {"text": "Done"}]
        result = await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(result["text"], "Done")
        self.assertEqual([call.args[1] for call in self.relay.invoke.await_args_list], ["coder", "coder"])

    async def test_revoked_recipient_grant_after_planning_blocks_worker(self):
        async def revoke(*args):
            self.relay.workspace["models"].remove("coder")
            return self.plan()
        self.relay.invoke.side_effect = revoke
        with self.assertRaisesRegex(ConnectionError, "permitted tasks changed"):
            await self.relay.orchestrator.run("user", "Private history")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_revoked_coordinator_grant_blocks_synthesis(self):
        self.relay.workspace["models"].remove("planner")
        replies = [self.plan([self.task(), self.task(task_type="debugging")]), {"text": "Code"}, {"text": "Review"}]
        async def revoke(*args):
            reply = replies.pop(0)
            if not replies:
                self.relay.workspace["coordinator"] = None
            return reply
        self.relay.invoke.side_effect = revoke
        with self.assertRaisesRegex(PermissionError, "no longer authorized"):
            await self.relay.orchestrator.run("user", "Private history")
        self.assertEqual(self.relay.invoke.await_count, 3)

    async def test_foreign_coordinator_never_calls_models(self):
        self.relay.workspace["coordinator"] = "foreign"
        with self.assertRaises(ConnectionError):
            await self.relay.orchestrator.run("user", "Code this")
        self.relay.invoke.assert_not_awaited()

    async def test_offline_default_uses_first_available_imported_model_for_work(self):
        self.relay.workspace["coordinator"] = "offline"
        self.relay.invoke.side_effect = [self.plan(), {"text": "Done"}]
        result = await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(result["routing"]["coordinator"], "planner")
        self.assertEqual(self.relay.workspace["coordinator"], "offline")
        self.assertEqual([call.args[1] for call in self.relay.invoke.await_args_list], ["planner", "coder"])

    async def test_import_order_defines_default_independently_of_credentials(self):
        self.relay.workspace.update(coordinator=None, models=["private", "planner", "coder"])
        for profile in self.relay.agent_profiles.values():
            profile["delegation_enabled"] = False
        self.relay.invoke.side_effect = [{"text": "Default answer"}]
        result = await self.relay.orchestrator.run("user", "Write a parser")
        self.assertEqual(result["responder"]["agent_id"], "private")
        self.assertEqual(result["routing"]["mode"], "direct")

    async def test_unconfigured_coordinator_uses_first_connected_model(self):
        self.relay.workspace["coordinator"] = None
        self.relay.invoke.side_effect = [self.plan(), {"text": "Done"}]
        result = await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(result["routing"]["coordinator"], "planner")

    async def test_plain_general_chat_needs_no_delegation_and_preserves_history(self):
        for profile in self.relay.agent_profiles.values():
            profile["delegation_enabled"] = False
        self.relay.workspace["coordinator"] = None
        history = [{"role": "user", "content": "Make a hello world program"},
                   {"role": "assistant", "content": 'print("Hello, world!")'},
                   {"role": "user", "content": "Now use a function"}]
        self.relay.invoke.side_effect = [{"text": "def hello(): ...", "provider": "custom", "usage": {"output_tokens": 9}}]
        result = await self.relay.orchestrator.run("user", {"messages": history})
        self.relay.invoke.assert_awaited_once_with("user", "planner", {"messages": history}, None)
        self.assertEqual(result["routing"]["mode"], "direct")
        self.assertEqual(result["usage"], {"output_tokens": 9})
        self.assertEqual(result["responder"], {"agent_id": "planner", "model": "arbitrary-planner", "provider": "custom"})

    async def test_answer_preserves_model_identity_and_metadata_returned_by_provider(self):
        self.relay.invoke.side_effect = [self.plan(), {"text": "Worker answer", "provider": "actual-provider",
                                                    "model": "actual-model-version", "usage": {"output_tokens": 9}}]
        result = await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(result["responder"], {"agent_id": "coder", "model": "actual-model-version", "provider": "actual-provider"})
        self.assertEqual(result["routing"]["responder"], result["responder"])
        self.assertEqual(result["usage"], {"output_tokens": 9})

    async def test_offline_delegates_leave_conversation_available_without_dispatching_work(self):
        for identity, profile in self.relay.agent_profiles.items():
            profile["delegation_enabled"] = identity == "offline"
        self.relay.invoke.side_effect = [{"text": "Here is the code."}]
        result = await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(result["routing"]["assignments"], [])
        self.assertEqual(result["routing"]["mode"], "direct")
        self.assertEqual(result["text"], "Here is the code.")
        self.assertEqual(self.relay.invoke.await_count, 1)
        self.assertEqual(self.relay.invoke.await_args.args[1], "planner")
        self.assertEqual(self.relay.invoke.await_args.args[2], {"messages": [{"role": "user", "content": "Code this"}]})

    async def test_greetings_go_directly_to_one_model_without_planning_or_general_permission(self):
        self.relay.agent_profiles["planner"].update(purpose="Coding only", tasks=["coding"], delegation_enabled=True)
        for request in ("hello", "HELLO!!! 👋", "hi", "Hi there", "Hey, how are you?", "Good morning!",
                        "namaste", "नमस्ते", "Bonjour!", "Hola!", "Olá!", "Hello, world!",
                        "Good morning, everyone", "Hello there, how are you doing today?", "G’day mate!",
                        "Hey 👋\nHow’s it going?",
                        "Thanks, how are you doing?"):
            with self.subTest(request=request):
                self.relay.invoke.reset_mock()
                self.relay.invoke.side_effect = [{"text": "Hello! How can I help?"}]
                result = await self.relay.orchestrator.run("user", request)
                self.assertEqual(result["text"], "Hello! How can I help?")
                self.assertEqual(result["routing"]["mode"], "conversation")
                self.assertEqual(result["routing"]["assignments"], [])
                self.assertEqual(result["responder"]["agent_id"], "planner")
                self.relay.invoke.assert_awaited_once_with("user", "planner", {
                    "messages": [{"role": "user", "content": request}]}, None)

    async def test_greeting_uses_only_imported_specialist_even_without_a_coordinator(self):
        self.relay.workspace.update(models=["coder"], coordinator=None)
        self.relay.invoke.side_effect = [{"text": "Hi!", "usage": {"output_tokens": 2}}]
        result = await self.relay.orchestrator.run("user", "hello")
        self.relay.invoke.assert_awaited_once_with("user", "coder", {
            "messages": [{"role": "user", "content": "hello"}]}, None)
        self.assertEqual(result["usage"], {"output_tokens": 2})
        self.assertEqual(result["responder"]["model"], "arbitrary-coder")

    async def test_greeting_falls_back_from_offline_coordinator_to_one_connected_model(self):
        self.relay.workspace["coordinator"] = "offline"
        self.relay.invoke.side_effect = [{"text": "Hi!"}]
        result = await self.relay.orchestrator.run("user", "Hey there!")
        self.assertEqual(result["responder"]["agent_id"], "planner")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_greeting_never_sends_history_to_unapproved_or_foreign_models(self):
        self.relay.workspace.update(models=["foreign", "offline"], coordinator=None)
        with self.assertRaisesRegex(ConnectionError, "Connect or start"):
            await self.relay.orchestrator.run("user", "hello")
        self.relay.invoke.assert_not_awaited()
        self.relay.workspace["models"] = []
        with self.assertRaisesRegex(ConnectionError, "Connect a model"):
            await self.relay.orchestrator.run("user", "hello")
        self.relay.invoke.assert_not_awaited()

    async def test_greeting_preserves_history_and_uses_a_vision_model_for_retained_images(self):
        image = {"type": "image", "name": "bug.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        history = [{"role": "user", "content": [{"type": "text", "text": "Debug this"}, image]},
                   {"role": "assistant", "content": "Fixed"}, {"role": "user", "content": "hello"}]
        self.relay.invoke.side_effect = [{"text": "Welcome back!"}]
        plans = []
        result = await self.relay.orchestrator.run("user", {"messages": history}, "room", plans.append)
        self.relay.invoke.assert_awaited_once_with("user", "coder", {"messages": history}, "room")
        self.assertEqual(result["responder"]["agent_id"], "coder")
        self.assertEqual(plans[0]["mode"], "conversation")
        self.assertEqual(plans[0]["assignments"], [])
        self.relay.agent_profiles["coder"]["vision"] = False
        self.relay.invoke.reset_mock()
        with self.assertRaisesRegex(ConnectionError, "image support"):
            await self.relay.orchestrator.run("user", {"messages": history})
        self.relay.invoke.assert_not_awaited()

    async def test_greeting_rechecks_recipient_grant_after_plan_callback(self):
        self.relay.workspace.update(models=["coder"], coordinator=None)
        def revoke(routing):
            self.relay.workspace["models"] = []
        with self.assertRaisesRegex(PermissionError, "no longer authorized"):
            await self.relay.orchestrator.run("user", "hello", on_plan=revoke)
        self.relay.invoke.assert_not_awaited()

    async def test_greeting_prefixed_work_and_contextual_followups_still_use_planning(self):
        for request in ("Hello, debug this function", "Make a hello world program", "Now use a function"):
            with self.subTest(request=request):
                self.relay.invoke.reset_mock()
                self.relay.invoke.side_effect = [self.plan(), {"text": "Worker answer"}]
                result = await self.relay.orchestrator.run("user", request)
                self.assertEqual(result["text"], "Worker answer")
                self.assertEqual(result["routing"]["mode"], "delegated")
                self.assertEqual([call.args[1] for call in self.relay.invoke.await_args_list], ["planner", "coder"])

    async def test_contextual_conversation_can_be_answered_by_coordinator(self):
        self.relay.invoke.side_effect = [self.plan([], reason="Ordinary conversation", reply="How can I help?")]
        result = await self.relay.orchestrator.run("user", "I am not sure where to start")
        self.assertEqual(result["text"], "How can I help?")
        self.assertEqual(result["routing"]["mode"], "conversation")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_malformed_conversation_plan_is_repaired_once_without_worker_calls(self):
        self.relay.invoke.side_effect = [{"text": "Hello!"}, self.plan([], reply="Hello! How can I help?")]
        result = await self.relay.orchestrator.run("user", "I am not sure where to start")
        self.assertEqual(result["text"], "Hello! How can I help?")
        self.assertEqual([call.args[1] for call in self.relay.invoke.await_args_list], ["planner", "planner"])
        self.assertIn("previous reply did not match the required JSON object", self.relay.invoke.await_args.args[2]["text"])

    async def test_planning_accepts_plain_json_fences_and_windows_newlines(self):
        plan = self.plan([], reply="Hello!")["text"]
        for fence in ("```\n", "```json\r\n", "```JSON\n"):
            with self.subTest(fence=fence):
                self.relay.invoke.side_effect = [{"text": fence + plan + "\n```"}]
                result = await self.relay.orchestrator.run("user", "I am not sure where to start")
                self.assertEqual(result["text"], "Hello!")

    async def test_conversational_json_with_the_wrong_schema_is_repaired(self):
        self.relay.invoke.side_effect = [{"text": '{"answer":"Hello!"}'}, self.plan([], reply="Hello!")]
        result = await self.relay.orchestrator.run("user", "I am not sure where to start")
        self.assertEqual(result["text"], "Hello!")
        self.assertEqual(self.relay.invoke.await_count, 2)

    async def test_unreadable_planning_falls_back_with_original_history_and_model_metadata(self):
        history = [{"role": "user", "content": "Write a Python parser"},
                   {"role": "assistant", "content": "Here is an initial parser"},
                   {"role": "user", "content": "Now fix empty inputs"}]
        self.relay.invoke.side_effect = [{"text": "Not JSON"}, {"text": "Still not JSON"},
                                         {"text": "Fixed parser", "model": "default-version", "usage": {"output_tokens": 9}}]
        plans = []
        result = await self.relay.orchestrator.run("user", {"messages": history}, "room", plans.append)
        self.assertEqual(result["text"], "Fixed parser")
        self.assertEqual(result["routing"]["mode"], "fallback")
        self.assertEqual(result["routing"]["assignments"], [])
        self.assertEqual(result["responder"]["agent_id"], "planner")
        self.assertEqual(result["model"], "default-version")
        self.assertEqual(result["usage"], {"output_tokens": 9})
        self.assertEqual(plans[0]["mode"], "fallback")
        self.relay.invoke.assert_awaited_with("user", "planner", {"messages": history}, "room")

    async def test_unavailable_structured_planning_uses_normal_default_reply(self):
        for failure in (ConnectionError("Structured output unsupported"), TimeoutError()):
            with self.subTest(failure=failure):
                self.relay.invoke.reset_mock()
                self.relay.invoke.side_effect = [failure, {"text": "Direct answer"}]
                result = await self.relay.orchestrator.run("user", "Write a parser")
                self.assertEqual(result["routing"]["mode"], "fallback")
                self.assertEqual(result["text"], "Direct answer")
                self.assertEqual(self.relay.invoke.await_count, 2)
                self.assertNotIn("response_schema", self.relay.invoke.await_args.args[2])

    async def test_fallback_image_history_goes_only_to_available_vision_model(self):
        image = {"type": "image", "name": "bug.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        history = [{"role": "user", "content": [{"type": "text", "text": "Debug this"}, image]}]
        self.relay.invoke.side_effect = [{"text": "No plan"}, {"text": "Still no plan"}, {"text": "Found the bug"}]
        result = await self.relay.orchestrator.run("user", {"messages": history})
        self.assertEqual(result["routing"]["mode"], "fallback")
        self.assertEqual(result["responder"]["agent_id"], "coder")
        self.relay.invoke.assert_awaited_with("user", "coder", {"messages": history}, None)

    async def test_fallback_rechecks_recipient_grant_after_planning_failure(self):
        async def revoke(*args):
            self.relay.workspace.update(models=[], coordinator=None)
            raise ConnectionError("Planning failed")
        self.relay.invoke.side_effect = revoke
        with self.assertRaisesRegex(ConnectionError, "Connect a model"):
            await self.relay.orchestrator.run("user", "Private code")
        self.assertEqual(self.relay.invoke.await_count, 1)

    async def test_worker_failure_does_not_replay_request_with_default_model(self):
        self.relay.invoke.side_effect = [self.plan(), ConnectionError("Worker disconnected")]
        with self.assertRaisesRegex(ConnectionError, "Worker disconnected"):
            await self.relay.orchestrator.run("user", "Code this")
        self.assertEqual(self.relay.invoke.await_count, 2)

    async def test_basic_general_chat_preserves_images_and_requires_vision(self):
        for profile in self.relay.agent_profiles.values():
            profile["delegation_enabled"] = False
        self.relay.agent_profiles["coder"]["vision"] = False
        image = {"type": "image", "name": "bug.png", "mime_type": "image/png",
                 "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test").decode()}
        content = [{"type": "text", "text": "Read this screenshot"}, image]
        payload = {"messages": [{"role": "user", "content": content}]}
        with self.assertRaisesRegex(ConnectionError, "image support"):
            await self.relay.orchestrator.run("user", payload)
        self.relay.invoke.assert_not_awaited()
        self.relay.agent_profiles["planner"]["vision"] = True
        self.relay.invoke.side_effect = [{"text": "I can see it"}]
        await self.relay.orchestrator.run("user", payload)
        self.assertEqual(self.relay.invoke.await_args.args[2], payload)

    async def test_endpoint_requires_auth_and_reports_invalid_input_and_planning_fallback(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://relay") as client:
            self.assertEqual((await client.post("/orchestrate", json={"text": "Code this"})).status_code, 401)
            self.assertEqual((await client.post("/orchestrate", headers=self.headers, json={"provider": "override"})).status_code, 400)
            self.relay.invoke.side_effect = [{"text": "bad plan"}, {"text": "still not JSON"}, {"text": "Default answer"}]
            response = await client.post("/orchestrate", headers=self.headers, json={"text": "Code this"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["text"], "Default answer")
            self.assertEqual(response.json()["routing"]["mode"], "fallback")
            self.assertEqual(self.relay.invoke.await_count, 3)

    async def test_profile_endpoint_accepts_legacy_and_new_preferences_and_rejects_invalid_types(self):
        legacy = {"provider": "custom", "model": "any-model", "vision": False, "running": True}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://relay") as client:
            response = await client.post("/agent-profile", headers=self.headers, json=legacy)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(self.relay.agent_description("user")["delegation_enabled"])
            preferences = {"purpose": "Conversation only", "tasks": ["general"], "delegation_enabled": True}
            response = await client.post("/agent-profile", headers=self.headers, json={**legacy, **preferences})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.relay.agent_description("user")["tasks"], ["general"])
            for invalid in ({"tasks": [["general"]]}, {"delegation_enabled": "true"}, {"purpose": "x" * 1001}, {"tasks": []}):
                response = await client.post("/agent-profile", headers=self.headers, json={**legacy, **preferences, **invalid})
                self.assertEqual(response.status_code, 400)


class RoutingProfileTests(unittest.TestCase):
    def test_greeting_recognition_requires_the_whole_message_to_be_conversational(self):
        for request in ("hello world program", "Hello, debug this", "hey write a poem", "Good morning, solve this",
                        "hello\n```python\nprint(1)\n```", "Continue", "", "hi " * 100):
            with self.subTest(request=request):
                self.assertFalse(is_greeting(request))

    def test_a_greeting_carrying_an_image_is_planned_rather_than_answered_directly(self):
        """The image guard, which decides whether delegation is skipped at all.

        ``is_greeting`` returns False for anything holding an image, because a
        picture is work to be done rather than a greeting to be acknowledged.
        Sending it straight to one model would skip planning and give the
        vision model a message the user never asked it to handle alone, so the
        branch is pinned here rather than left to the rest of the suite.
        """
        picture = [{"type": "image", "name": "hello.png", "mime_type": "image/png",
                    "data": base64.b64encode(b"\x89PNG\r\n\x1a\ntest").decode()}]
        for request in ([{"type": "text", "text": "hello"}] + picture,
                        [{"type": "text", "text": "What is in this image?"}] + picture,
                        picture):
            with self.subTest(request=request):
                self.assertFalse(is_greeting(request))

    def test_the_length_ceiling_is_a_boundary_and_not_a_rough_guess(self):
        """240 characters is where a greeting stops being one.

        ``is_greeting`` refuses anything longer outright rather than matching a
        prefix of it. A short padded greeting is therefore still a greeting,
        and the first character past the ceiling is not. Tested on both sides
        because an off-by-one here would either truncate a long request or
        push planning onto short ones, and neither shows up as an exception.
        """
        at_ceiling = "hi".ljust(240, " ")
        over_ceiling = "hi".ljust(241, " ")
        self.assertTrue(is_greeting(at_ceiling), "a padded greeting at the ceiling stopped being one")
        self.assertFalse(is_greeting(over_ceiling), "a message past the ceiling was still taken for a greeting")

    def test_compatibility_normalization_folds_the_forms_that_only_differ_in_shape(self):
        """NFKC folds fullwidth and compatibility characters before matching.

        A greeting typed with a fullwidth Latin letter, or with compatibility
        forms such as ``\ufb00`` for "ff", is the same greeting. Without the
        normalization step those go down the planning path, which costs a
        model round trip for the user's hello and changes which model answers.
        """
        for request in ("ｈｅｌｌｏ", "ＨＥＬＬＯ！", "how＇s it going"):
            with self.subTest(request=request):
                self.assertTrue(is_greeting(request), f"{request!r} was not recognized as a greeting")

    def test_existing_profiles_are_opted_out_and_preferences_need_a_purpose_and_tasks(self):
        self.assertEqual(routing_profile({}), {"purpose": "", "tasks": [], "delegation_enabled": False})
        for profile in ({"delegation_enabled": True}, {"delegation_enabled": True, "purpose": "Code"},
                        {"tasks": ["coding", "coding"]}, {"tasks": "coding"}):
            with self.assertRaises(ValueError):
                routing_profile(profile)
