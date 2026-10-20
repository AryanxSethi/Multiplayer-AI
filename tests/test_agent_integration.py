"""End-to-end: client -> Connection Server -> Agent Gateway -> relay -> agent.

This exercises the chain in specification sections 12 to 16 with real
components: a real relay on a real socket, a real ``AgentClient`` holding
an agent, and a real client WebSocket on the Connection Server. Nothing
here is mocked, so it proves the two applications actually fit together
rather than that each merely looks right in isolation.

The relay's own behaviour is covered by ``tests/test_network.py``; this
file is only about the client-facing integration.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Callable, Iterator, List

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

import src.server.app as server_app
from network_a2a.client import AgentClient
from network_a2a.server import create_app
from src.agents.gateway import AgentGateway
from tests.conftest import envelope

# The relay requires unique tokens of at least 32 characters, so build
# them rather than counting characters by hand.
GATEWAY_TOKEN = "tok-gateway-" + "x" * 40


def _token(agent_id: str) -> str:
    return f"tok-{agent_id}-" + "x" * 40


CREDENTIALS = {
    "gateway": {"token": GATEWAY_TOKEN, "group": "team"},
    "agent-A": {"token": _token("agent-A"), "group": "team"},
    "agent-B": {"token": _token("agent-B"), "group": "team"},
    "agent-C": {"token": _token("agent-C"), "group": "team"},
    # Reserved for the offline case: no test ever connects it, so that
    # test does not depend on another test's teardown having completed.
    "agent-D": {"token": _token("agent-D"), "group": "team"},
    # Never connected by any test, and in another group besides.
    "outsider": {"token": _token("outsider"), "group": "other"},
}


@pytest.fixture(scope="module")
def relay() -> dict:
    """A real relay listening on an ephemeral port."""
    app = create_app(CREDENTIALS, timeout=5)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws_max_size=262144))

    async def serve():
        await server.serve(sockets=[sock])

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("relay failed to start")
        time.sleep(0.02)

    yield {"http": f"http://127.0.0.1:{port}", "ws": f"ws://127.0.0.1:{port}/connect"}

    server.should_exit = True
    thread.join(10)
    sock.close()


def _online_agents(relay) -> set:
    """Agent ids the relay currently considers connected."""
    with httpx.Client(timeout=5) as http:
        result = http.get(
            f"{relay['http']}/agents",
            headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
        )
    return {a["id"] for a in result.json()["agents"] if a["online"]}


def wait_for_agent(relay, agent_id: str, timeout: float = 10.0) -> None:
    """Block until the relay reports ``agent_id`` connected.

    Polled synchronously on purpose: the TestClient calls in these tests
    block the calling thread, so anything that yields the test's event
    loop cannot be relied on while a request is in flight.
    """
    deadline = time.monotonic() + timeout
    while agent_id not in _online_agents(relay) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert agent_id in _online_agents(relay), f"{agent_id} never came online"


@pytest.fixture
def start_agent(relay) -> Iterator[Callable[[str], None]]:
    """Start real agents on their own threads, one per test.

    Each test uses a distinct agent id. The relay refuses a second
    connection for an id that is still registered (close code 1008), so
    reusing one id across tests would make each test depend on the
    previous one's teardown having fully disconnected.

    The agent runs on its own thread and event loop because
    ``TestClient`` is synchronous and blocks its caller: an agent sharing
    the test's loop could never read the relay's request, since the loop
    that must deliver the response is the one blocked waiting for it.
    Running it separately also mirrors the real deployment, where the
    agent is a separate process.
    """
    started: List[dict] = []

    def start(agent_id: str) -> None:
        async def handler(payload, source):
            return {
                "text": f"{agent_id} handled: {payload.get('text')}",
                "provider": "test-agent",
                "model": "echo-1",
            }

        holder = {
            "agent": AgentClient(
                relay["ws"], CREDENTIALS[agent_id]["token"], handler, allow_insecure=True
            )
        }

        def run() -> None:
            loop = asyncio.new_event_loop()
            holder["loop"] = loop

            async def main() -> None:
                holder["task"] = asyncio.current_task()
                await holder["agent"].run()

            try:
                loop.run_until_complete(main())
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass

        holder["thread"] = threading.Thread(target=run, daemon=True)
        holder["thread"].start()
        started.append(holder)
        wait_for_agent(relay, agent_id)

    yield start

    for holder in started:
        loop, task = holder.get("loop"), holder.get("task")
        socket = holder["agent"].socket
        # Close the socket explicitly before cancelling the run loop.
        # Cancelling AgentClient.run() alone leaves the connection open:
        # the relay keeps the agent registered as a peer, and a later
        # request to it would be delivered to a dead socket and time out
        # instead of reporting the agent offline.
        if loop is not None and socket is not None:
            asyncio.run_coroutine_threadsafe(socket.close(), loop)
            time.sleep(0.2)
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        holder["thread"].join(5)


@pytest.fixture
def gateway(relay, monkeypatch) -> Iterator[AgentGateway]:
    """A gateway pointed at the real relay, installed on the server."""
    instance = AgentGateway(relay["http"], GATEWAY_TOKEN)
    monkeypatch.setattr(server_app, "agent_gateway", instance)
    yield instance
    # Closed from the loop the TestClient created, which is where the
    # pooled httpx client lives.
    with TestClient(server_app.app):
        pass


def send_to_agent(client: TestClient, agent_id: str, **overrides) -> dict:
    """Send one client-to-agent envelope and read the single reply."""
    frame = envelope(destination=agent_id, destination_type="agent", **overrides)
    with client.websocket_connect("/ws/client-A") as ws:
        ws.send_json(frame)
        return ws.receive_json()


# --- the full chain ------------------------------------------------------


async def test_client_reaches_a_real_agent(relay, start_agent, gateway) -> None:
    """client-A -> server -> gateway -> relay -> agent-A, and back."""
    start_agent("agent-A")

    with TestClient(server_app.app) as client:
        response = send_to_agent(
            client, "agent-A", message_id="msg-001", text="Hello agent"
        )

    assert response["type"] == "response"
    assert response["payload"]["text"] == "agent-A handled: Hello agent"
    assert response["payload"]["provider"] == "test-agent"
    assert response["source"] == {"id": "agent-A", "type": "agent"}
    assert response["destination"] == {"id": "client-A", "type": "client"}


async def test_response_correlates_to_the_request(
    relay, start_agent, gateway
) -> None:
    """The response echoes messageId and sessionId so a client can match it.

    The specification requires the association between a client request
    and the agent response that it caused.
    """
    start_agent("agent-B")

    with TestClient(server_app.app) as client:
        response = send_to_agent(
            client,
            "agent-B",
            message_id="msg-correlation-42",
            session_id="session-xyz",
            text="correlate me",
        )

    assert response["messageId"] == "msg-correlation-42"
    assert response["sessionId"] == "session-xyz"


async def test_client_to_client_is_unaffected_by_the_gateway(
    relay, start_agent, gateway
) -> None:
    """Adding agent support does not change client-to-client behaviour."""
    start_agent("agent-C")

    with TestClient(server_app.app) as client:
        with client.websocket_connect("/ws/client-A") as ws_a:
            with client.websocket_connect("/ws/client-B") as ws_b:
                ws_a.send_json(envelope())
                received = ws_b.receive_json()

    assert received["type"] == "message"
    assert received["payload"]["text"] == "Hello Client B"


# --- failure paths through the real relay --------------------------------


async def test_offline_agent_reports_unavailable(relay, gateway) -> None:
    """An agent that never connected yields AGENT_UNAVAILABLE."""
    with TestClient(server_app.app) as client:
        response = send_to_agent(client, "agent-D")

    assert response["type"] == "error"
    assert response["code"] == "AGENT_UNAVAILABLE"


async def test_agent_in_another_group_is_unavailable(
    relay, start_agent, gateway
) -> None:
    """Group isolation is enforced, and not disclosed to the client."""
    start_agent("agent-A")

    with TestClient(server_app.app) as client:
        response = send_to_agent(client, "outsider")

    assert response["code"] == "AGENT_UNAVAILABLE"
    # The relay's wording would confirm the id exists but is forbidden,
    # which is the enumeration it refuses to allow.
    assert "group" not in response["message"].lower()


async def test_unknown_agent_id_is_unavailable(relay, gateway) -> None:
    """A well-formed but unknown agent is reported the same way.

    Indistinguishable from the cross-group case on purpose.
    """
    with TestClient(server_app.app) as client:
        response = send_to_agent(client, "agent-does-not-exist")

    assert response["code"] == "AGENT_UNAVAILABLE"


async def test_malformed_agent_id_never_reaches_the_relay(
    relay, gateway
) -> None:
    """A bad agent id is rejected by the gateway, not forwarded."""
    with TestClient(server_app.app) as client:
        response = send_to_agent(client, "not a valid id")

    assert response["code"] == "INVALID_MESSAGE"


async def test_no_gateway_configured_reports_not_found(monkeypatch) -> None:
    """Without a relay the server still runs and explains what to set."""
    monkeypatch.setattr(server_app, "agent_gateway", None)

    with TestClient(server_app.app) as client:
        response = send_to_agent(client, "agent-A")

    assert response["code"] == "AGENT_NOT_FOUND"
    assert "A2A_RELAY_URL" in response["message"]
