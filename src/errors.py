"""Structured error responses for the Connection Server.

Errors are returned to clients as JSON frames on the same WebSocket the
message arrived on. Raw exception text and stack traces are never sent to
clients; the code carries enough information for a client to react, and
the underlying detail stays in the server log.

Frame shape (specification section 21)::

    {
        "type": "error",
        "messageId": "msg-123",
        "code": "CLIENT_NOT_FOUND",
        "message": "Destination client is not connected."
    }
"""

from __future__ import annotations

import json
from typing import Optional


class ErrorCode:
    """Error codes returned to clients.

    Plain constants rather than an Enum: the values are only ever
    serialised to JSON, so an Enum would add machinery without adding
    behaviour.
    """

    INVALID_MESSAGE = "INVALID_MESSAGE"
    CLIENT_NOT_FOUND = "CLIENT_NOT_FOUND"
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    A2A_ERROR = "A2A_ERROR"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RoutingError(Exception):
    """Base class for routing failures that map to a client error code."""

    code = ErrorCode.INTERNAL_ERROR


class InvalidMessageError(RoutingError):
    """The frame could not be parsed as a valid message envelope."""

    code = ErrorCode.INVALID_MESSAGE


class ClientNotFoundError(RoutingError):
    """The destination client is not connected to this server."""

    code = ErrorCode.CLIENT_NOT_FOUND


class ClientDeliveryError(RoutingError):
    """The destination client was found but the frame could not be sent.

    Distinct from :class:`ClientNotFoundError` so the log distinguishes
    "nobody home" from "present but the socket failed mid-write".
    """

    code = ErrorCode.CLIENT_NOT_FOUND


class AgentNotFoundError(RoutingError):
    """The destination agent is not present in the agent registry.

    Every agent destination currently fails with this. The Agent Gateway
    is not built yet, so agent routing is deliberately unimplemented
    rather than silently accepted.
    """

    code = ErrorCode.AGENT_NOT_FOUND


def make_error(
    message_id: Optional[str],
    code: str,
    message: str,
) -> str:
    """Build the JSON error frame for a client.

    ``message_id`` is echoed back so a client can match an error to the
    request that caused it. It is ``None`` when the incoming frame could
    not be parsed far enough to recover an id, and serialises to
    ``null`` in that case.
    """
    return json.dumps(
        {
            "type": "error",
            "messageId": message_id,
            "code": code,
            "message": message,
        }
    )
