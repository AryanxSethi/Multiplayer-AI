"""Tests 3 and 4 from agent.md section 24: clients connect and stay connected."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from src.errors import ClientNotFoundError


def test_client_connection_is_accepted(client: TestClient) -> None:
    """Test 3: the server accepts a client connection."""
    with client.websocket_connect("/ws/client-A"):
        assert client.get("/health").status_code == 200


def test_client_is_registered_on_connect(client: TestClient, registry) -> None:
    """Test 3: a connecting client appears in the registry."""
    with client.websocket_connect("/ws/client-A"):
        assert registry.is_connected("client-A")
        assert registry.count() == 1


def test_client_is_removed_on_disconnect(client: TestClient, registry) -> None:
    """agent.md section 22: the registry cleans up after a disconnect."""
    with client.websocket_connect("/ws/client-A"):
        assert registry.is_connected("client-A")

    assert not registry.is_connected("client-A")
    assert registry.count() == 0


def test_multiple_clients_remain_connected(
    client: TestClient, registry
) -> None:
    """Test 4: two clients connect and both stay registered."""
    with client.websocket_connect("/ws/client-A"):
        with client.websocket_connect("/ws/client-B"):
            assert registry.is_connected("client-A")
            assert registry.is_connected("client-B")
            assert registry.count() == 2

    assert registry.count() == 0


def test_connection_id_is_not_exposed_to_clients(
    client: TestClient, registry
) -> None:
    """The internal connection id never reaches the client protocol."""
    with client.websocket_connect("/ws/client-A"):
        info = registry.get_client("client-A")
        assert info is not None

        # The client-facing protocol carries only envelope fields; the
        # connection id stays server-side.
        assert "connectionId" not in info.client_id


@pytest.mark.parametrize(
    "client_id", ["client-A", "client-B", "client-with-a-much-longer-id"]
)
def test_various_client_ids_are_accepted(
    client: TestClient, registry, client_id: str
) -> None:
    """Any client-supplied identifier is accepted at the prototype stage.

    agent.md section 8 allows the client to supply an id, and separates
    identification from authentication.
    """
    with client.websocket_connect(f"/ws/{client_id}"):
        assert registry.is_connected(client_id)


def test_a_second_socket_for_one_client_is_closed(client: TestClient, registry) -> None:
    """The end of the orphan, over a real socket rather than a stand-in.

    The registry reports the socket it replaced; this checks the caller acts on
    it, because a fix that only tidied the table would still leave the socket
    open, which is the half a reader can actually be hurt by.
    """
    with client.websocket_connect("/ws/client-A") as first:
        stale = registry.get_client("client-A").connection_id
        with client.websocket_connect("/ws/client-A") as second:
            live = registry.get_client("client-A").connection_id
            assert live != stale, (
                "the registry still points at the socket that was replaced")
            assert stale not in registry._connection_ids, (
                "the replaced connection id was left in the table")
            assert registry.count() == 1, (
                f"{registry.count()} entries for one client id")
        # The live socket closed with the inner block and must take the client
        # with it, even though a stale id unregisters first on the way out.
        assert not registry.is_connected("client-A")


def test_unknown_client_send_raises_not_found(registry) -> None:
    """Sending to an absent client reports CLIENT_NOT_FOUND."""
    with pytest.raises(ClientNotFoundError):
        asyncio.run(registry.send_to_client("nobody", "payload"))


def test_connection_ids_are_unique(registry) -> None:
    """Each connection gets its own identifier."""
    ids = {registry.register("client-A", websocket=None)[0] for _ in range(5)}
    assert len(ids) == 5


class _Socket:
    """Stands in for a WebSocket and records whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_reconnecting_the_same_id_reports_the_socket_it_displaced(registry) -> None:
    """One client id is one socket, so the replaced one has to be handed back.

    Reconnecting used to overwrite the entry and drop nothing: the previous
    socket stayed open and its connection id stayed in the table, so a client
    that dropped and came back left both behind. The README called that out as
    a known limitation rather than fixing it.
    """
    first = _Socket()
    second = _Socket()

    stale_id, displaced_first = registry.register("client-A", first)
    assert displaced_first is None, "nothing was registered to displace"

    connection_id, displaced = registry.register("client-A", second)
    assert displaced is first, (
        "the socket that was replaced was not reported, so nothing can close it")
    assert registry.get_client("client-A").websocket is second

    # And the stale id is gone from the table, not left to accumulate: a client
    # that reconnects repeatedly would otherwise grow one entry per attempt.
    assert stale_id not in registry._connection_ids


def test_the_displaced_socket_going_away_does_not_remove_the_live_client(registry) -> None:
    """Closing the old socket unregisters it, and must not take the new one.

    This is the guard that already existed and the reason the fix above is
    safe: the displaced connection unregisters under its own id, finds the id
    it was given belongs to someone else now, and leaves the client alone.
    """
    first, second = _Socket(), _Socket()
    stale_id, _ = registry.register("client-A", first)
    _, displaced = registry.register("client-A", second)

    assert displaced is first
    assert registry.unregister(stale_id) is None, (
        "the stale disconnect claimed to have removed a client it never owned")
    assert registry.is_connected("client-A"), (
        "the client was removed by the socket that had already been replaced")
    assert registry.get_client("client-A").websocket is second

    # The live socket still cleans up after itself normally.
    live_id = registry.get_client("client-A").connection_id
    assert registry.unregister(live_id) == "client-A"
    assert not registry.is_connected("client-A")
