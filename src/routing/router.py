"""The single routing decision point for client-facing messages.

The specification asks for one clearly identifiable routing component
rather than routing decisions scattered across unrelated modules, so
every inbound message funnels through :func:`route_message`.

The shape of the decision (section 20)::

    Incoming Message
           |
        Validate
           |
       Read Destination
           |
           +-- destination.type == client --> Client Registry --> WebSocket
           |
           +-- destination.type == agent  --> Agent Gateway --> A2A

Both branches are implemented. The agent branch holds no HTTP or relay
detail of its own (section 29): it hands the payload to the Agent Gateway
and converts the returned payload into the normalised response envelope
from section 16.
"""

from __future__ import annotations

from typing import Any, Optional

from src.errors import AgentNotFoundError, InvalidMessageError
from src.messages.envelope import MessageEnvelope


async def route_message(
    envelope: MessageEnvelope,
    client_id: str,
    connection_manager: Any,
    agent_gateway: Any = None,
) -> Optional[MessageEnvelope]:
    """Route one validated envelope to its destination.

    The source recorded in the delivered frame is always rewritten to the
    id of the connection that sent it. A client-supplied ``source`` is
    not trusted: otherwise client-A could post a message claiming to come
    from client-B (section 9 requires the source be identified).

    Args:
        envelope: The parsed message envelope.
        client_id: Id of the connection that sent this frame.
        connection_manager: Registry used to reach the destination client.
        agent_gateway: Agent Gateway used for agent destinations. When
            None, agent destinations fail with ``AGENT_NOT_FOUND``.

    Returns:
        For a client destination, None: the destination has the message
        and the sender is told nothing, which is the established
        behaviour. For an agent destination, the response envelope to
        deliver back to the originating client (section 16). Returning
        the reply rather than sending it keeps delivery in one place.

    Raises:
        InvalidMessageError: Destination id or type is missing/unknown.
        ClientNotFoundError: Destination client is not connected.
        ClientDeliveryError: Destination client socket rejected the write.
        AgentNotFoundError: An agent was addressed but none is reachable.
        AgentGatewayError: The agent call failed; ``code`` is set.
    """
    destination_type = envelope.destination_type
    destination_id = envelope.destination_id

    if not destination_type or not destination_id:
        raise InvalidMessageError(
            "Message envelope requires destination.id and destination.type."
        )

    # The connection is the authority on who sent this, not the payload.
    envelope.source = {"id": client_id, "type": "client"}

    if destination_type == "client":
        await connection_manager.send_to_client(destination_id, envelope.to_wire())
        return None

    if destination_type == "agent":
        return await _route_to_agent(envelope, client_id, agent_gateway)

    raise InvalidMessageError(
        f"Unknown destination type '{destination_type}'. "
        "Expected 'client' or 'agent'."
    )


async def _route_to_agent(
    envelope: MessageEnvelope,
    client_id: str,
    agent_gateway: Any,
) -> MessageEnvelope:
    """Call an agent and build the response envelope for the requester.

    The correlation fields are copied from the request rather than
    generated, so the client can match the response to the request that
    caused it (section 15).
    """
    if agent_gateway is None:
        raise AgentNotFoundError(
            f"Agent '{envelope.destination_id}' cannot be reached: no agent "
            "relay is configured for this server. Set A2A_RELAY_URL and "
            "A2A_RELAY_TOKEN to enable agent messaging."
        )

    result = await agent_gateway.invoke(envelope.destination_id, envelope.payload)

    return MessageEnvelope(
        messageId=envelope.message_id,
        type="response",
        source={"id": envelope.destination_id, "type": "agent"},
        destination={"id": client_id, "type": "client"},
        sessionId=envelope.session_id,
        timestamp=envelope.timestamp,
        payload=result,
    )
