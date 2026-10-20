"""The wire format exchanged between clients and the Connection Server.

A single envelope carries every client-facing message. The field names on
the wire are camelCase, matching the examples in specification sections 10 and
34; internally the model uses snake_case, so the aliases below are what
bridge the two.

Wire format::

    {
        "messageId": "msg-123",
        "type": "message",
        "source": {"id": "client-A", "type": "client"},
        "destination": {"id": "client-B", "type": "client"},
        "sessionId": "session-001",
        "timestamp": "2026-10-02T10:00:00Z",
        "payload": {"text": "Hello"}
    }
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field


class MessageEnvelope(BaseModel):
    """A single message as it travels between clients and the server.

    ``populate_by_name`` allows construction with either the wire name
    (``messageId``) or the field name (``message_id``). ``from_wire``
    and :meth:`to_wire` are the intended entry points, since they also
    handle the camelCase direction; the alias configuration only exists
    so the two spellings both parse.
    """

    model_config = ConfigDict(populate_by_name=True)

    message_id: str = Field(alias="messageId")
    type: str
    source: Dict[str, str]
    destination: Dict[str, str]
    session_id: str = Field(alias="sessionId")
    timestamp: str
    payload: Dict[str, Any]

    @classmethod
    def from_wire(cls, raw: Dict[str, Any]) -> "MessageEnvelope":
        """Parse a decoded JSON frame received from a client."""
        return cls(**raw)

    def to_wire(self) -> str:
        """Serialise to the camelCase JSON form clients expect.

        ``by_alias`` matters: without it the delivered frame would come
        back to the client with snake_case keys, which is not the format
        the client sent.
        """
        return self.model_dump_json(by_alias=True)

    @property
    def destination_type(self) -> str:
        """The destination kind: ``client`` or ``agent``."""
        return self.destination.get("type", "")

    @property
    def destination_id(self) -> str:
        """The destination identifier."""
        return self.destination.get("id", "")

    @classmethod
    def create(
        cls,
        message_type: str,
        source_id: str,
        destination_id: str,
        source_type: str,
        destination_type: str,
        session_id: str,
        payload: Dict[str, Any],
    ) -> "MessageEnvelope":
        """Build an envelope, generating an id and timestamp.

        Both source and destination kinds are explicit because a relayed
        message carries agent identities as readily as client ones.
        """
        return cls(
            messageId=str(uuid.uuid4()),
            type=message_type,
            source={"id": source_id, "type": source_type},
            destination={"id": destination_id, "type": destination_type},
            sessionId=session_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )
