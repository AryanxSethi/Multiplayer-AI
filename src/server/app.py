"""Connection Server: the client-facing entry point.

Serves two things:

* ``GET /health`` — a liveness probe that deliberately does not depend on
  any agent being reachable (agent.md section 6).
* ``WebSocket /ws/{client_id}`` — the persistent client connection that
  carries all client-to-client messaging (section 7).

Routing lives in :mod:`src.routing.router`; this module owns transport
only: accept frames, hand them to the router, and report failures back to
the sender as structured errors.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator, Dict

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.agents.gateway import AgentGateway, AgentGatewayError
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
from src.server.connection_manager import ConnectionManager

# Configure before creating the logger, otherwise the INFO events listed in
# agent.md section 23 are discarded: Python's default root level is WARNING
# and no handler is attached, so logger.info() would emit nothing at all.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("connection_server")

# One registry per process. The prototype runs a single process, so there
# is nothing to share or coordinate.
connection_manager = ConnectionManager()

# The Agent Gateway, present only when a relay is configured. When it is
# None an agent destination fails with AGENT_NOT_FOUND rather than the
# server refusing to start, so the client-to-client path keeps working on
# its own.
agent_gateway = AgentGateway.from_env()

# Named as a constant so the hint is assertable. The variable must be
# A2A_RELAY_TOKEN, which is the one AgentGateway.from_env reads; naming
# A2A_TOKEN here sent operators to the variable that means the opposite
# thing, namely a process's own agent identity.
GATEWAY_DISABLED_HINT = "set A2A_RELAY_URL and A2A_RELAY_TOKEN"

if agent_gateway is None:
    logger.info(
        "AGENT_GATEWAY status=disabled reason=missing_config "
        "hint=" + GATEWAY_DISABLED_HINT
    )


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    """Release the gateway's pooled connections on shutdown.

    Starlette's ``on_shutdown`` constructor argument has been removed, so
    cleanup is expressed as a lifespan handler instead.
    """
    yield
    if agent_gateway is not None:
        await agent_gateway.aclose()

async def health(request: Request) -> JSONResponse:
    """Liveness probe. Reports process health only, never agent health."""
    return JSONResponse({"status": "healthy"})


async def root(request: Request) -> JSONResponse:
    """Describe the available endpoints."""
    return JSONResponse(
        {
            "service": "Connection Server",
            "endpoints": {
                "health": "GET /health",
                "client_socket": "WebSocket /ws/{client_id}",
            },
        }
    )


async def client_socket(websocket: WebSocket) -> None:
    """Hold a client's persistent connection and relay its messages.

    The client id comes from the path, read off the socket rather than
    passed as an argument: unlike Starlette's HTTP ``Route``, a
    ``WebSocketRoute`` invokes its endpoint with the socket alone, and
    path parameters are read from ``path_params``.

    The client id is taken as given. There is no authentication yet:
    identification and authentication are separate concerns
    (agent.md section 8), and conflating them would make retrofitting
    auth harder.

    Delivery failures are reported to the *sender*; on success the sender
    receives nothing, so the destination is the only place a delivered
    message is observable.
    """
    client_id = websocket.path_params["client_id"]

    await websocket.accept()
    connection_id, displaced = connection_manager.register(client_id, websocket)
    if displaced is not None:
        # One client id is one socket, so the one this replaced has to go.
        # Closing is where it belongs -- the connection is in hand here and the
        # registry cannot await -- and it used to be left open, with its
        # connection id left behind too, on every reconnect.
        #
        # Suppressed because closing a socket the framework is already tearing
        # down can raise, and it is out of the registry either way: there is
        # nothing to report and nothing left to clean up.
        with suppress(Exception):
            await displaced.close()
        logger.info("CLIENT_DISPLACED clientId=%s connectionId=%s", client_id, connection_id)
    logger.info(
        "CLIENT_CONNECTED clientId=%s connectionId=%s", client_id, connection_id
    )

    try:
        while True:
            frame = await _receive_frame(websocket)
            if frame is None:
                continue
            await _handle_frame(websocket, client_id, frame)
    except WebSocketDisconnect:
        pass
    finally:
        removed = connection_manager.unregister(connection_id)
        logger.info(
            "CLIENT_DISCONNECTED clientId=%s connectionId=%s removed=%s",
            client_id,
            connection_id,
            removed is not None,
        )


async def _receive_frame(websocket: WebSocket) -> Dict[str, Any] | None:
    """Read and parse one JSON frame.

    A malformed frame is answered with ``INVALID_MESSAGE`` and the loop
    continues. The failure has already consumed the frame, so returning
    None here is safe and keeps the socket usable.

    Returns:
        The decoded frame, or None if it could not be parsed.
    """
    try:
        raw = await websocket.receive_json()
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        logger.warning("MESSAGE_RECEIVED status=rejected reason=unparseable: %s", exc)
        await websocket.send_text(
            make_error(
                None,
                ErrorCode.INVALID_MESSAGE,
                "Frame is not valid JSON.",
            )
        )
        return None

    if not isinstance(raw, dict):
        await websocket.send_text(
            make_error(
                None,
                ErrorCode.INVALID_MESSAGE,
                "Frame must be a JSON object.",
            )
        )
        return None
    return raw


async def _handle_frame(
    websocket: WebSocket, client_id: str, frame: Dict[str, Any]
) -> None:
    """Parse one envelope and route it, reporting failures to the sender."""
    try:
        envelope = MessageEnvelope.from_wire(frame)
    except Exception as exc:
        # Validation problems are a client fault, so the detail stays
        # short and no traceback is exposed.
        logger.warning(
            "MESSAGE_RECEIVED clientId=%s status=rejected reason=invalid_envelope: %s",
            client_id,
            exc,
        )
        await websocket.send_text(
            make_error(
                frame.get("messageId"),
                ErrorCode.INVALID_MESSAGE,
                "Message envelope is missing required fields or has invalid types.",
            )
        )
        return

    logger.info(
        "MESSAGE_RECEIVED messageId=%s clientId=%s sessionId=%s destination=%s:%s",
        envelope.message_id,
        client_id,
        envelope.session_id,
        envelope.destination_type,
        envelope.destination_id,
    )

    try:
        reply = await route_message(
            envelope, client_id, connection_manager, agent_gateway
        )
    except (
        ClientNotFoundError,
        ClientDeliveryError,
        InvalidMessageError,
        AgentNotFoundError,
        AgentGatewayError,
    ) as exc:
        logger.warning(
            "MESSAGE_ROUTED messageId=%s clientId=%s destination=%s:%s "
            "code=%s status=failed",
            envelope.message_id,
            client_id,
            envelope.destination_type,
            envelope.destination_id,
            exc.code,
        )
        await websocket.send_text(
            make_error(envelope.message_id, exc.code, str(exc))
        )
        return
    except Exception:
        logger.exception(
            "ERROR messageId=%s clientId=%s code=%s",
            envelope.message_id,
            client_id,
            ErrorCode.INTERNAL_ERROR,
        )
        await websocket.send_text(
            make_error(
                envelope.message_id,
                ErrorCode.INTERNAL_ERROR,
                "Internal server error.",
            )
        )
        return

    logger.info(
        "MESSAGE_ROUTED messageId=%s clientId=%s destination=%s:%s status=delivered",
        envelope.message_id,
        client_id,
        envelope.destination_type,
        envelope.destination_id,
    )

    if reply is not None:
        # An agent destination answers, so the reply goes back to the
        # client that asked. Client-to-client sends still return nothing:
        # the destination already holds the message.
        logger.info(
            "MESSAGE_DELIVERED messageId=%s clientId=%s from=agent",
            envelope.message_id,
            client_id,
        )
        await websocket.send_text(reply.to_wire())


routes = [
    Route("/health", health),
    Route("/", root),
    WebSocketRoute("/ws/{client_id}", client_socket),
]

app = Starlette(routes=routes, lifespan=lifespan)
