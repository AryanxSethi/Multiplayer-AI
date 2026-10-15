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


def test_unknown_client_send_raises_not_found(registry) -> None:
    """Sending to an absent client reports CLIENT_NOT_FOUND."""
    with pytest.raises(ClientNotFoundError):
        asyncio.run(registry.send_to_client("nobody", "payload"))


def test_connection_ids_are_unique(registry) -> None:
    """Each connection gets its own identifier."""
    ids = {registry.register("client-A", websocket=None) for _ in range(5)}
    assert len(ids) == 5
