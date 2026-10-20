"""Tests 1 and 2 from specification section 24: the server starts and reports health."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute

import src.server.app as server_app


def test_server_application_exists() -> None:
    """Test 1: the server application is importable and configured."""
    assert isinstance(server_app.app, Starlette)


def test_health_endpoint_is_registered() -> None:
    """Test 1: the health route is mounted."""
    paths = {route.path for route in server_app.app.routes}
    assert "/health" in paths


def test_health_endpoint_returns_healthy(client) -> None:
    """Test 2: GET /health returns 200 and reports healthy."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_does_not_depend_on_agents(client) -> None:
    """Test 2: health stays healthy with no clients and no agents.

    The specification requires the health check not to depend on the AI
    agent, so it must not report on client or agent state.
    """
    response = client.get("/health")

    body = response.json()
    assert body == {"status": "healthy"}
    assert "agents" not in body
    assert "clients" not in body


def test_websocket_route_is_registered() -> None:
    """The client WebSocket endpoint is mounted at the documented path."""
    ws_routes = [
        route for route in server_app.app.routes if isinstance(route, WebSocketRoute)
    ]
    assert any(route.path == "/ws/{client_id}" for route in ws_routes)


def test_root_describes_endpoints(client) -> None:
    """The root endpoint advertises how to reach the server."""
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["endpoints"]["health"] == "GET /health"


def test_http_routes_are_not_needed_for_messaging() -> None:
    """No POST /message route: the client protocol is WebSocket only."""
    post_routes = [
        route
        for route in server_app.app.routes
        if isinstance(route, Route) and "POST" in (route.methods or set())
    ]
    assert post_routes == []


def test_disabled_gateway_hint_names_the_variables_from_env_actually_reads() -> None:
    """The hint must point at the variables AgentGateway.from_env reads.

    It used to say A2A_TOKEN, which means a process's own agent identity
    rather than this server's relay token, so following the hint left the
    agent half still disabled. Asserted on the constant because the log
    line is emitted once at import time.
    """
    hint = server_app.GATEWAY_DISABLED_HINT

    assert "A2A_RELAY_URL" in hint
    assert "A2A_RELAY_TOKEN" in hint
    assert "A2A_TOKEN" not in hint.replace("A2A_RELAY_TOKEN", "")
