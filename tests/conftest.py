"""Shared fixtures.

The Connection Server keeps one module-level client registry, so tests
would otherwise see clients registered by earlier tests. Each test gets a
fresh registry by swapping the module attribute for a new instance, which
keeps the production classes free of test-only reset methods.
"""

from __future__ import annotations

# Before anything can construct a QApplication, and before any test module
# is imported. Eight modules each set this for themselves, which meant the
# variable was right only if every one of them happened to be imported
# before the first Qt object was built -- a precondition nothing enforced
# and nothing said. One place, set first.
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Qt's Windows offscreen plugin needs an explicit font directory. Without it,
# labels render as missing-glyph boxes and layout tests measure those boxes.
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts"))

import pytest
from starlette.testclient import TestClient

import src.server.app as server_app
from src.server.connection_manager import ConnectionManager


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> ConnectionManager:
    """Give each test an empty client registry."""
    registry = ConnectionManager()
    monkeypatch.setattr(server_app, "connection_manager", registry)
    return registry


@pytest.fixture
def client() -> TestClient:
    """A test client bound to the ASGI app, no port binding required."""
    with TestClient(server_app.app) as test_client:
        yield test_client


@pytest.fixture
def registry() -> ConnectionManager:
    """Access to the per-test client registry.

    Named ``registry`` rather than ``fresh_registry`` so tests read
    clearly; the instance is identical either way.
    """
    return server_app.connection_manager


def envelope(
    *,
    message_id: str = "msg-001",
    source: str = "client-A",
    destination: str = "client-B",
    destination_type: str = "client",
    session_id: str = "session-001",
    text: str = "Hello Client B",
) -> dict:
    """Build a well-formed client message envelope."""
    return {
        "messageId": message_id,
        "type": "message",
        "source": {"id": source, "type": "client"},
        "destination": {"id": destination, "type": destination_type},
        "sessionId": session_id,
        "timestamp": "2026-10-02T10:00:00Z",
        "payload": {"text": text},
    }
