"""In-memory registry of connected clients.

The Connection Server keeps a live WebSocket per connected client and
tracks who is present. This is deliberately in-memory only: agent.md
section 7 says not to add a database until something actually needs one.

The registry owns delivery to a client socket. Routing decides *where* a
message goes; the registry is the only place that writes to a WebSocket.
"""

from __future__ import annotations

import time
import uuid
from typing import Dict, Optional

from starlette.websockets import WebSocket

from src.errors import ClientDeliveryError, ClientNotFoundError


class ClientInfo:
    """A single connected client.

    Attributes:
        client_id: Stable identifier chosen by the client on connect.
        connection_id: Server-assigned id for this particular socket.
            Regenerated on every connect and never exposed to clients.
        websocket: The live socket, used to deliver messages.
        connected_at: Unix timestamp of the connection, for diagnostics.
        status: Connection status. ``connected`` or ``disconnected``.
    """

    def __init__(self, client_id: str, websocket: WebSocket) -> None:
        self.client_id = client_id
        self.connection_id = str(uuid.uuid4())
        self.websocket = websocket
        self.connected_at = time.time()
        self.status = "connected"

    def __repr__(self) -> str:
        return (
            f"ClientInfo(client_id={self.client_id!r}, "
            f"status={self.status!r}, connected_at={self.connected_at!r})"
        )


class ConnectionManager:
    """Tracks connected clients and delivers frames to them.

    A plain in-memory dict is sufficient here. There is one registry per
    process, and the prototype runs a single process, so there is nothing
    to coordinate.
    """

    def __init__(self) -> None:
        self._clients: Dict[str, ClientInfo] = {}
        self._connection_ids: Dict[str, str] = {}

    def register(
        self, client_id: str, websocket: WebSocket
    ) -> tuple[str, Optional[WebSocket]]:
        """Register a newly connected client.

        One client id is one socket. Reconnecting with an id already present
        replaces the previous entry, and the socket it replaced is returned so
        the caller can close it: it used to be left open and its connection id
        left in the table, so a client that dropped and came back stranded both.

        The socket is not closed from here. Closing is async and this is not,
        and the caller has the connection in hand at the moment it is worth
        closing. Its connection id is dropped now rather than when it finally
        closes, because that is what makes the return above the only record of
        it -- unregister() can no longer find it, which is correct: by then the
        id belongs to nobody.

        Returns:
            The new connection id, needed later to unregister, and the socket
            this one displaced, or None if nothing was registered.
        """
        previous = self._clients.get(client_id)
        if previous is not None:
            self._connection_ids.pop(previous.connection_id, None)
        info = ClientInfo(client_id, websocket)
        self._clients[client_id] = info
        self._connection_ids[info.connection_id] = client_id
        return info.connection_id, (previous.websocket if previous else None)

    def unregister(self, connection_id: str) -> Optional[str]:
        """Remove a client by connection id.

        Guarded against removing a *newer* connection that reused the
        same client id: if the id no longer maps to this connection, the
        entry is left alone.

        Returns:
            The removed client id, or None if it was already gone.
        """
        client_id = self._connection_ids.pop(connection_id, None)
        if client_id is None:
            return None
        current = self._clients.get(client_id)
        if current is not None and current.connection_id == connection_id:
            del self._clients[client_id]
            return client_id
        return None

    def get_client(self, client_id: str) -> Optional[ClientInfo]:
        """Look up a connected client, or None if absent."""
        return self._clients.get(client_id)

    def is_connected(self, client_id: str) -> bool:
        """Whether a client id currently has a live socket."""
        return client_id in self._clients

    def count(self) -> int:
        """Number of connected clients."""
        return len(self._clients)

    async def send_to_client(self, client_id: str, frame: str) -> None:
        """Deliver a serialised frame to a connected client.

        Raises:
            ClientNotFoundError: No such client is connected.
            ClientDeliveryError: The client is registered but the write
                failed, typically because the socket closed between the
                lookup and the send.
        """
        client = self._clients.get(client_id)
        if client is None:
            raise ClientNotFoundError(
                f"Destination client '{client_id}' is not connected."
            )
        try:
            await client.websocket.send_text(frame)
        except Exception as exc:
            client.status = "disconnected"
            raise ClientDeliveryError(
                f"Failed to deliver message to client '{client_id}': {exc}"
            ) from exc
