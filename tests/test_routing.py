"""Unit tests for the routing decision point and structured errors."""

from __future__ import annotations

import json

import pytest

from src.errors import (
    AgentNotFoundError,
    ClientDeliveryError,
    ClientNotFoundError,
    ErrorCode,
    InvalidMessageError,
    make_error,
)
from src.messages.envelope import MessageEnvelope
from src.routing.router import route_message
from tests.conftest import envelope


class StubRegistry:
    """Minimal stand-in for the client registry.

    Records delivered frames so routing can be tested without a live
    WebSocket. Only the one method the router uses is implemented.
    """

    def __init__(self, *client_ids: str) -> None:
        self.delivered: dict[str, list[str]] = {cid: [] for cid in client_ids}

    async def send_to_client(self, client_id: str, frame: str) -> None:
        if client_id not in self.delivered:
            raise ClientNotFoundError(
                f"Destination client '{client_id}' is not connected."
            )
        self.delivered[client_id].append(frame)


@pytest.fixture
def stub() -> StubRegistry:
    return StubRegistry("client-A", "client-B")


# --- routing ------------------------------------------------------------


async def test_routes_to_destination_client(stub: StubRegistry) -> None:
    """A client destination is delivered through the registry.

    Nothing is returned for the sender to receive: the destination holds
    the message, which is the established client-to-client behaviour.
    """
    message = MessageEnvelope.from_wire(envelope())

    reply = await route_message(message, "client-A", stub)

    assert reply is None
    assert len(stub.delivered["client-B"]) == 1


async def test_delivered_frame_is_valid_json(stub: StubRegistry) -> None:
    """The delivered frame parses as JSON in the documented shape."""
    message = MessageEnvelope.from_wire(envelope())

    await route_message(message, "client-A", stub)

    delivered = json.loads(stub.delivered["client-B"][0])
    assert delivered["messageId"] == "msg-001"
    assert delivered["payload"]["text"] == "Hello Client B"


async def test_source_is_always_the_sending_connection(stub: StubRegistry) -> None:
    """A spoofed source is overwritten with the real sender."""
    message = MessageEnvelope.from_wire(envelope(source="client-B"))

    await route_message(message, "client-A", stub)

    delivered = json.loads(stub.delivered["client-B"][0])
    assert delivered["source"] == {"id": "client-A", "type": "client"}


async def test_unknown_client_raises_client_not_found(stub: StubRegistry) -> None:
    """An unknown destination surfaces CLIENT_NOT_FOUND."""
    message = MessageEnvelope.from_wire(envelope(destination="ghost"))

    with pytest.raises(ClientNotFoundError):
        await route_message(message, "client-A", stub)


async def test_agent_destination_raises_agent_not_found(
    stub: StubRegistry,
) -> None:
    """Agent destinations are refused until the gateway is built."""
    message = MessageEnvelope.from_wire(
        envelope(destination="agent-A", destination_type="agent")
    )

    with pytest.raises(AgentNotFoundError):
        await route_message(message, "client-A", stub)


async def test_unknown_destination_type_is_invalid(stub: StubRegistry) -> None:
    """An unrecognised destination type is a message error."""
    message = MessageEnvelope.from_wire(
        envelope(destination="x", destination_type="wormhole")
    )

    with pytest.raises(InvalidMessageError):
        await route_message(message, "client-A", stub)


async def test_missing_destination_type_is_invalid(stub: StubRegistry) -> None:
    """A destination without a type is rejected before any delivery."""
    frame = envelope()
    del frame["destination"]["type"]
    message = MessageEnvelope.from_wire(frame)

    with pytest.raises(InvalidMessageError):
        await route_message(message, "client-A", stub)


async def test_missing_destination_id_is_invalid(stub: StubRegistry) -> None:
    """A destination without an id is rejected."""
    frame = envelope()
    del frame["destination"]["id"]
    message = MessageEnvelope.from_wire(frame)

    with pytest.raises(InvalidMessageError):
        await route_message(message, "client-A", stub)


async def test_nothing_is_delivered_on_failure(stub: StubRegistry) -> None:
    """A rejected message is not delivered anywhere."""
    message = MessageEnvelope.from_wire(envelope(destination="ghost"))

    with pytest.raises(ClientNotFoundError):
        await route_message(message, "client-A", stub)

    assert all(not frames for frames in stub.delivered.values())


# --- error frames -------------------------------------------------------


def test_make_error_matches_documented_shape() -> None:
    """The error frame matches the shape in specification section 21."""
    frame = json.loads(
        make_error("msg-123", ErrorCode.CLIENT_NOT_FOUND, "Destination missing.")
    )

    assert frame == {
        "type": "error",
        "messageId": "msg-123",
        "code": "CLIENT_NOT_FOUND",
        "message": "Destination missing.",
    }


def test_make_error_allows_null_message_id() -> None:
    """An unparseable frame has no recoverable id, serialised as null."""
    frame = json.loads(make_error(None, ErrorCode.INVALID_MESSAGE, "Bad frame."))

    assert frame["messageId"] is None


@pytest.mark.parametrize(
    "code",
    [
        "INVALID_MESSAGE",
        "CLIENT_NOT_FOUND",
        "AGENT_NOT_FOUND",
        "AGENT_UNAVAILABLE",
        "A2A_ERROR",
        "TIMEOUT",
        "INTERNAL_ERROR",
    ],
)
def test_all_documented_error_codes_exist(code: str) -> None:
    """Every code listed in specification section 21 is defined."""
    assert getattr(ErrorCode, code) == code


def test_error_classes_carry_their_code() -> None:
    """Each error class maps to the code clients should see."""
    assert ClientNotFoundError.code == ErrorCode.CLIENT_NOT_FOUND
    assert ClientDeliveryError.code == ErrorCode.CLIENT_NOT_FOUND
    assert InvalidMessageError.code == ErrorCode.INVALID_MESSAGE
    assert AgentNotFoundError.code == ErrorCode.AGENT_NOT_FOUND
