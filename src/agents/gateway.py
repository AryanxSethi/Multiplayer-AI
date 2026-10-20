"""The Agent Gateway: the only component that talks to the agent relay.

The specification draws the boundary this module implements::

    Message Router
           |
           v
     Agent Gateway
           |
           v
        A2A / relay
           |
           v
         Agent

The router decides *that* a message is for an agent. This module locates
the endpoint, performs the call, and translates the relay's replies into
the error codes the client protocol defines. No client-envelope handling
and no WebSocket logic belongs here.

Transport
---------
The relay exposes ``POST /agents/{agent}/invoke``, which accepts an
arbitrary JSON payload and returns an arbitrary JSON result. The Gateway
uses that rather than speaking the relay's peer WebSocket protocol: the
peer protocol is for devices hosting an agent, whereas the Gateway is
itself just a caller.

Correlation
-----------
An HTTP invoke is synchronous, so the response is correlated to the
request by the call itself. The specification warns against assuming
agent responses are always synchronous. That seam is left open on
purpose: :meth:`AgentGateway.invoke` is async and returns the agent's
payload, so an asynchronous push path can be added beside it later
without changing the router.
"""

from __future__ import annotations

import logging
import math
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import httpx

from src.errors import ErrorCode, RoutingError

logger = logging.getLogger("agent_gateway")

# Hosts where plaintext http cannot leave the machine. The relay token is
# sent as a bearer credential on every invoke, so an http:// URL pointing
# anywhere else would hand it to the network in the clear.
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")

# The relay rejects agent ids outside this pattern, so reject them here
# too and report a client error rather than provoking a 403 that would
# be indistinguishable from a group-isolation failure.
AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

# The relay's own request timeout defaults to 60s. Allow a small margin so
# the relay's 504 wins the race and reports the real reason, rather than
# our client timeout firing first and masking it.
DEFAULT_TIMEOUT = 65.0


class AgentGatewayError(RoutingError):
    """An agent call failed. ``code`` is the client-facing error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AgentGateway:
    """Calls agents on the relay and normalises failures.

    Args:
        relay_url: Base URL of the relay, for example
            ``http://localhost:9100``.
        token: This gateway's own relay token. The relay authenticates the
            caller with it and applies group isolation, so it must belong
            to the same group as the agents being addressed.
        timeout: Seconds to wait for the relay to answer.
    """

    def __init__(
        self,
        relay_url: str,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not relay_url or not token:
            raise ValueError("AgentGateway needs a relay URL and a token")
        self.relay_url = relay_url.rstrip("/")
        self.timeout = timeout
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client: Optional[httpx.AsyncClient] = None

    @classmethod
    def from_env(cls) -> Optional["AgentGateway"]:
        """Build a gateway from the environment, or None if unconfigured.

        Deliberately *not* ``A2A_TOKEN``: that variable already means
        "this process's own agent identity" to ``python -m network_a2a``,
        so reusing it here would silently authenticate the Connection
        Server as an agent whenever both ran from one shell. The relay
        itself is configured through ``A2A_CREDENTIALS_FILE``.

        The URL is checked before the gateway is built. ``invoke`` sends
        this token as a bearer credential on every call, so an http://
        URL aimed anywhere but loopback would transmit it in plaintext.
        https:// is always allowed; http:// is only allowed on loopback,
        with no override, because there is no legitimate reason for a
        relay holding a shared token to be reached in the clear.

        Returns:
            A configured gateway, or None when the relay is not usable.
            None rather than an exception, so an operator who points this
            at the wrong scheme still gets a working client-to-client
            server and a warning in the log explaining the agent half.
        """
        relay_url = os.environ.get("A2A_RELAY_URL")
        token = os.environ.get("A2A_RELAY_TOKEN")
        if not relay_url or not token:
            return None
        if not cls._usable_url(relay_url):
            return None
        raw_timeout = os.getenv("A2A_GATEWAY_TIMEOUT")
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
            # float() accepts "nan" and "inf", and a non-positive value is
            # not a usable deadline either, so the range is checked rather
            # than just the conversion. Otherwise a typo here reaches httpx
            # as a NaN timeout instead of falling back.
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout must be a positive finite number")
        except ValueError:
            logger.warning(
                "AGENT_GATEWAY bad_timeout value=%r using=%s", raw_timeout, DEFAULT_TIMEOUT
            )
            timeout = DEFAULT_TIMEOUT
        return cls(relay_url, token, timeout)

    @staticmethod
    def _usable_url(relay_url: str) -> bool:
        """Whether this relay URL may carry the gateway token."""
        parsed = urlsplit(relay_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            logger.warning(
                "AGENT_GATEWAY status=disabled reason=bad_relay_url url=%r "
                "hint=use an https:// relay URL, or http:// on localhost",
                relay_url,
            )
            return False
        if parsed.username or parsed.password:
            logger.warning(
                "AGENT_GATEWAY status=disabled reason=credentials_in_url url=%r "
                "hint=put the token in A2A_RELAY_TOKEN, not in the URL",
                relay_url,
            )
            return False
        if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
            logger.warning(
                "AGENT_GATEWAY status=disabled reason=plaintext_relay url=%r "
                "hint=the relay token would cross the network in the clear; use https://",
                relay_url,
            )
            return False
        return True

    def _http(self) -> httpx.AsyncClient:
        """Lazily create one client and reuse it.

        A fresh client per request would discard connection pooling on
        every message, which matters for a long-lived server.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        """Release the pooled connections."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def invoke(self, agent_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a payload to an agent and return its response payload.

        The payload is forwarded unchanged. The relay passes it straight
        to the target agent's handler, and the provider adapters already
        accept the ``{"text": ...}`` shape the client protocol uses.
        Validating it here would couple this layer to provider internals.

        Raises:
            AgentGatewayError: With the client-facing code to report.
        """
        if not AGENT_ID_PATTERN.fullmatch(agent_id):
            raise AgentGatewayError(
                ErrorCode.INVALID_MESSAGE,
                "Agent id must be 1 to 64 letters, digits, underscores or hyphens.",
            )

        url = f"{self.relay_url}/agents/{agent_id}/invoke"
        try:
            response = await self._http().post(url, json=payload, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise AgentGatewayError(
                ErrorCode.TIMEOUT,
                f"Agent '{agent_id}' did not respond in time.",
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentGatewayError(
                ErrorCode.AGENT_UNAVAILABLE,
                f"Agent '{agent_id}' is unavailable: the relay could not be reached.",
            ) from exc

        if response.status_code == 200:
            try:
                result = response.json()
            except ValueError as exc:
                raise AgentGatewayError(
                    ErrorCode.A2A_ERROR,
                    f"Agent '{agent_id}' returned an unreadable response.",
                ) from exc
            if not isinstance(result, dict):
                raise AgentGatewayError(
                    ErrorCode.A2A_ERROR,
                    f"Agent '{agent_id}' returned an unexpected response.",
                )
            return result

        raise self._error_for(agent_id, response)

    def _error_for(self, agent_id: str, response: httpx.Response) -> AgentGatewayError:
        """Translate a relay error response into a client-facing error.

        The relay signals failure with a status code and an ``{"error": ...}``
        body. Its 403 covers both an unknown agent and one outside the
        caller's group, and it deliberately does not distinguish them. The
        relay's own wording for 403 ("Target is unavailable or outside your
        group") is therefore never passed through: echoing it would tell a
        client that the agent id was recognised but forbidden, which is
        exactly the enumeration the relay refuses to allow.
        """
        if response.status_code == 401:
            # The gateway's own token is wrong: a server misconfiguration,
            # not something the client did or can fix. The relay's detail
            # is not forwarded because it is not written for clients.
            return AgentGatewayError(
                ErrorCode.A2A_ERROR,
                "The agent relay rejected this server's credentials.",
            )

        if response.status_code == 403:
            return AgentGatewayError(
                ErrorCode.AGENT_UNAVAILABLE,
                f"Agent '{agent_id}' is unavailable.",
            )

        if response.status_code == 429:
            return AgentGatewayError(
                ErrorCode.AGENT_UNAVAILABLE,
                f"Agent '{agent_id}' is unavailable: the relay is at "
                "capacity, retry later.",
            )

        if response.status_code == 503:
            # Offline is not sensitive, so the relay's reason is useful here.
            return AgentGatewayError(
                ErrorCode.AGENT_UNAVAILABLE,
                f"Agent '{agent_id}' is unavailable{self._detail(response)}.",
            )

        if response.status_code in (502, 504):
            return AgentGatewayError(
                ErrorCode.TIMEOUT,
                f"Agent '{agent_id}' did not respond in time.",
            )

        return AgentGatewayError(
            ErrorCode.A2A_ERROR,
            f"Agent '{agent_id}' could not be reached (relay returned "
            f"HTTP {response.status_code}).",
        )

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        """The relay's error text in parentheses, when it sent one."""
        try:
            detail = response.json().get("error")
        except ValueError:
            return ""
        return f" ({detail})" if isinstance(detail, str) and detail else ""
