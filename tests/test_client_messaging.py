"""Tests 5 and 6 from specification section 24: client-to-client messaging.

The success path asserts on the *destination* socket. Nothing is sent
back to the sender on success, so the sender's socket is silent and
cannot be used to confirm delivery.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from tests.conftest import envelope


def test_client_to_client_delivers_message(client: TestClient) -> None:
    """Test 5: client-A -> server -> client-B delivers the payload."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        with client.websocket_connect("/ws/client-B") as ws_b:
            ws_a.send_json(envelope())

            received = ws_b.receive_json()
            assert received["payload"]["text"] == "Hello Client B"
            assert received["messageId"] == "msg-001"
            assert received["destination"]["id"] == "client-B"


def test_delivered_frame_keeps_camel_case(client: TestClient) -> None:
    """The delivered frame uses the same field names the client sent.

    Serialising without by_alias would deliver message_id and session_id
    instead, which is not the documented wire format.
    """
    with client.websocket_connect("/ws/client-A") as ws_a:
        with client.websocket_connect("/ws/client-B") as ws_b:
            ws_a.send_json(envelope())
            received = ws_b.receive_json()

    assert "messageId" in received
    assert "sessionId" in received
    assert "message_id" not in received
    assert "session_id" not in received


def test_source_is_rewritten_to_the_real_sender(client: TestClient) -> None:
    """The server identifies the source from the connection, not the payload.

    The specification requires the source be identified. Trusting the
    envelope would let any client impersonate another.
    """
    spoofed = envelope(source="client-B")

    with client.websocket_connect("/ws/client-A") as ws_a:
        with client.websocket_connect("/ws/client-B") as ws_b:
            ws_a.send_json(spoofed)
            received = ws_b.receive_json()

    assert received["source"] == {"id": "client-A", "type": "client"}


def test_reverse_direction_also_works(client: TestClient) -> None:
    """Messaging is symmetric: client-B can send to client-A."""
    with client.websocket_connect("/ws/client-B") as ws_b:
        with client.websocket_connect("/ws/client-A") as ws_a:
            ws_b.send_json(envelope(source="client-B", destination="client-A"))

            received = ws_a.receive_json()

    assert received["destination"]["id"] == "client-A"
    assert received["payload"]["text"] == "Hello Client B"


def test_unknown_client_returns_client_not_found(client: TestClient) -> None:
    """Test 6: an unknown destination returns CLIENT_NOT_FOUND."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_json(envelope(destination="ghost"))

        error = ws_a.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "CLIENT_NOT_FOUND"
    assert error["messageId"] == "msg-001"


def test_disconnected_destination_returns_client_not_found(
    client: TestClient,
) -> None:
    """Specification section 22: a departed client yields an error, not a crash."""
    with client.websocket_connect("/ws/client-B"):
        pass  # client-B connects, then disconnects

    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_json(envelope())

        error = ws_a.receive_json()

    assert error["code"] == "CLIENT_NOT_FOUND"


def test_sender_receives_nothing_before_the_next_error(
    client: TestClient,
) -> None:
    """Success is silent for the sender.

    The first frame the sender sees after a successful delivery is the
    error from the *next* failing send, which shows no ack was inserted
    in between.
    """
    with client.websocket_connect("/ws/client-A") as ws_a:
        with client.websocket_connect("/ws/client-B") as ws_b:
            ws_a.send_json(envelope(message_id="msg-ok"))
            assert ws_b.receive_json()["messageId"] == "msg-ok"

            ws_a.send_json(envelope(message_id="msg-fail", destination="ghost"))
            first_frame = ws_a.receive_json()

    assert first_frame["type"] == "error"
    assert first_frame["messageId"] == "msg-fail"


def test_agent_destination_returns_agent_not_found(client: TestClient) -> None:
    """Without a configured relay, an agent destination fails loudly.

    The error names the environment variables to set, so the cause is
    actionable rather than just reported.
    """
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_json(envelope(destination="agent-A", destination_type="agent"))

        error = ws_a.receive_json()

    assert error["code"] == "AGENT_NOT_FOUND"
    assert "A2A_RELAY_URL" in error["message"]
    assert "A2A_RELAY_TOKEN" in error["message"]


def test_unknown_destination_type_is_invalid(client: TestClient) -> None:
    """An unrecognised destination type is rejected as INVALID_MESSAGE."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_json(envelope(destination="nobody", destination_type="wormhole"))

        error = ws_a.receive_json()

    assert error["code"] == "INVALID_MESSAGE"


def test_missing_destination_fields_are_invalid(client: TestClient) -> None:
    """A destination without a type is rejected."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        frame = envelope()
        del frame["destination"]["type"]
        ws_a.send_json(frame)

        error = ws_a.receive_json()

    assert error["code"] == "INVALID_MESSAGE"


def test_malformed_json_returns_invalid_message(client: TestClient) -> None:
    """Unparseable frames produce INVALID_MESSAGE with a null messageId."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_text("this is not json at all")

        error = ws_a.receive_json()

    assert error["code"] == "INVALID_MESSAGE"
    assert error["messageId"] is None


def test_socket_survives_malformed_frame(client: TestClient) -> None:
    """A malformed frame does not drop the connection."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_text("}{ not json")
        assert ws_a.receive_json()["code"] == "INVALID_MESSAGE"

        ws_a.send_json(envelope(destination="ghost"))
        error = ws_a.receive_json()

    assert error["code"] == "CLIENT_NOT_FOUND"


def test_incomplete_envelope_returns_invalid_message(client: TestClient) -> None:
    """A frame missing required fields is rejected, echoing any messageId."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_json({"messageId": "msg-partial", "type": "message"})

        error = ws_a.receive_json()

    assert error["code"] == "INVALID_MESSAGE"
    assert error["messageId"] == "msg-partial"


def test_json_array_frame_is_invalid(client: TestClient) -> None:
    """A JSON array is not a valid envelope."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_json([1, 2, 3])

        error = ws_a.receive_json()

    assert error["code"] == "INVALID_MESSAGE"


def test_error_never_leaks_a_stack_trace(client: TestClient) -> None:
    """Specification section 21: raw tracebacks are not exposed to clients."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        ws_a.send_text("not json")
        error = ws_a.receive_json()

    rendered = str(error)
    assert "Traceback" not in rendered
    assert "File \"" not in rendered
    assert set(error) == {"type", "messageId", "code", "message"}


def test_three_clients_can_exchange_messages(client: TestClient) -> None:
    """More than two clients coexist and route independently."""
    with client.websocket_connect("/ws/client-A") as ws_a:
        with client.websocket_connect("/ws/client-B") as ws_b:
            with client.websocket_connect("/ws/client-C") as ws_c:
                ws_a.send_json(envelope(destination="client-B"))
                ws_b.send_json(envelope(source="client-B", destination="client-C"))

                assert ws_b.receive_json()["messageId"] == "msg-001"
                assert ws_c.receive_json()["messageId"] == "msg-001"
