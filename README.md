# Multiplayer AI

Multiplayer AI is a platform where authorized users by the session leader
can invite people to work on the same session without sharing accounts or
people from the same organization.

For the complete reference — what the project does, the technologies it uses,
its requirements, installation, configuration, architecture, and test setup —
see [DOCUMENTATION.md](DOCUMENTATION.md).

## Status: under development

The first milestone is the **client communication layer**: a Connection
Server that lets one connected client deliver a message to another.

Implemented and tested:

- Persistent client connections over WebSocket
- In-memory client registry
- Client-to-client message routing
- Structured error responses and event logging

The client Connection Server can call agents through the Agent Gateway when
`A2A_RELAY_URL` and `A2A_RELAY_TOKEN` configure a usable relay. Session
management and client authentication are not implemented. The separate
authenticated agent relay and provider
adapters are available in `network_a2a/`; see below and [Scope](#scope).

## Multi-device agent networking

The native Multiplayer AI desktop app starts its local relay, creates a private
device identity, and connects automatically. Configure model providers, invite
devices, join shared relays, and chat with agents from its dark desktop workspace.
Named workspaces have separate conversations, models, and memberships. Owners
can rename them, invite or remove members, and delete them. Saved chat history
and follow-up model context survive switching workspaces and restarting the app.
See the [desktop guide](DESKTOP_GUIDE.md) to get started.

The authenticated relay and reconnecting device client in `network_a2a/`
connect multiple laptops/desktops over LAN or the internet. They route requests
between local agents and can forward requests to a local A2A server.
Desktop conversation invitations share one AI chat, including its existing
history and subsequent messages and replies, across invited devices.

See [setup and deployment instructions](NETWORK_SETUP.md) for credentials,
client commands, HTTPS/WSS deployment, and current limits. This relay uses a
custom transport; standard A2A discovery and streaming are not implemented.

Provider adapters support Ollama, Bionic GPT, OpenAI, Claude, Gemini, Groq,
DeepSeek, Mistral, OpenRouter, and custom OpenAI-compatible endpoints. See
[provider setup and examples](PROVIDER_ADAPTERS.md).

Chat uses the first imported model as its default, with another available imported
model taking over while it is offline. Optional model purposes and permitted tasks
enable specialist routing. If planning fails, the default answers the original
request directly. Workspace access, online status, and task permissions are checked
before dispatch. See the [chat guide](DESKTOP_GUIDE.md#chat-and-model-orchestration).

The client Connection Server and agent relay are separate applications with
different WebSocket message formats. The Connection Server reaches agents
through its Agent Gateway when `A2A_RELAY_URL` and `A2A_RELAY_TOKEN` configure
a usable relay. Run them on different ports if both are needed; each guide
uses port 8000 by default.

## Architecture

The client Connection Server targets the following architecture. Its Agent
Gateway connection is planned:

```
   CLIENT DOMAIN                      AGENT LAYER
   Client A ──┐                                  │
              │ WebSocket                        │
              ▼                                  │
      ┌──────────────────┐      A2A      ┌──────────────┐
      │ Connection Server│ ───────────▶  │   Agent A    │
      │  (this repo)     │               └──────┬───────┘
      └──────────────────┘                      │ A2A
              ▲                           ┌──────▼───────┐
              │                           │   Agent B    │
            Client B                      └──────────────┘
```

The diagram describes the client Connection Server roadmap. The separate
`network_a2a/` relay and provider adapters are implemented, but integration
with the client Connection Server is not built yet — see [Scope](#scope).

### Layout

| Path | Responsibility |
|------|----------------|
| `src/server/app.py` | HTTP/WebSocket entry point, frame handling, logging |
| `src/server/connection_manager.py` | In-memory client registry and delivery |
| `src/routing/router.py` | The single routing decision point |
| `src/messages/envelope.py` | The client-facing message format |
| `src/errors.py` | Error codes and the error frame format |
| `scripts/demo_client.py` | Command-line client for manual testing |
| `network_a2a/server.py` | Authenticated multi-device agent relay |
| `network_a2a/conversations.py` | Shared AI chat history and conversation membership |
| `network_a2a/orchestration.py` | Model planning, validated assignments, and synthesis |
| `network_a2a/persistence.py` | SQLite chat archives and bounded model context |
| `desktop_app/runtime.py` | Saved workspace catalog and switching |
| `desktop_app/workspace_runtime.py` | Per-workspace relays, agents, and membership |
| `desktop_app/window.py` | Native workspace rail and saved chat interface |
| `network_a2a/client.py` | Reconnecting agent client and request correlation |
| `network_a2a/adapters/` | Model provider handlers |
| `network_a2a/__main__.py` | Agent client CLI and local A2A bridge |

Routing decisions live only in `src/routing/router.py`. The WebSocket
handler does not decide where a message goes.

## Setup

The project uses an existing Conda environment. From the repository root:

```bash
conda env create -f environment.yml
conda activate Multiplayer-AI
```

If the environment already exists, install just the new test dependencies:

```bash
python -m pip install pytest-asyncio websockets
```

## Running

Start the Connection Server:

```bash
python -m uvicorn src.server.app:app --host localhost --port 8000
```

Check it is healthy:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"healthy"}
```

The health endpoint does not depend on any agent being reachable.

## Trying client-to-client messaging

Open three terminals.

**Terminal 1** — the server (from above).

**Terminal 2** — client-B, the receiver:

```bash
python scripts/demo_client.py client-B client-A "Hi from B"
```

**Terminal 3** — client-A, the sender:

```bash
python scripts/demo_client.py client-A client-B "Hello Client B"
```

Client-B prints the received message:

```
[client-B] <- client-A: Hello Client B
```

The demo client listens until you stop it with `Ctrl+C`. If the server is
not on `localhost:8000`, pass `--host` and `--port`, or set
`CONNECTION_HOST` and `CONNECTION_PORT`:

```bash
python scripts/demo_client.py client-A client-B "Hello" --port 8010
```

### Why the sender prints nothing

On a successful send the server returns **no acknowledgement** — the
destination is the only place a delivered message is observable. Client-A
stays silent after `sent (no acknowledgement is returned on success)`.
That silence *is* the success signal; nothing is missing.

A consequence worth knowing: a sender cannot distinguish "delivered" from
"the server died" by watching its own socket. Delivery receipts are not
implemented.

## Message format

Client-to-client messages use a single envelope. Field names are camelCase.

```json
{
  "messageId": "msg-123",
  "type": "message",
  "source": { "id": "client-A", "type": "client" },
  "destination": { "id": "client-B", "type": "client" },
  "sessionId": "session-001",
  "timestamp": "2026-10-02T10:00:00Z",
  "payload": { "text": "Hello" }
}
```

`source` is ignored on receipt. The server rewrites it to the id of the
connection that sent the frame, so one client cannot impersonate another.

`destination.type` selects the route:

- `client` — delivered to that client's WebSocket
- `agent` — routed through the Agent Gateway when a usable relay is configured;
  otherwise returns `AGENT_NOT_FOUND`

## Errors

Failures are returned to the *sender* as a JSON frame:

```json
{
  "type": "error",
  "messageId": "msg-123",
  "code": "CLIENT_NOT_FOUND",
  "message": "Destination client 'ghost' is not connected."
}
```

| Code | Meaning |
|------|---------|
| `INVALID_MESSAGE` | Frame is not JSON, the envelope is malformed, or the agent id is invalid |
| `CLIENT_NOT_FOUND` | Destination client is not connected |
| `AGENT_NOT_FOUND` | No agent relay is configured for this server |
| `AGENT_UNAVAILABLE` | The relay is unreachable, or the agent is unknown or outside the caller's group |
| `TIMEOUT` | The agent did not answer before `A2A_GATEWAY_TIMEOUT` |
| `A2A_ERROR` | The relay rejected the server's credentials or returned an unreadable response |
| `INTERNAL_ERROR` | An unexpected server failure; details stay in the server log |

`SESSION_NOT_FOUND` is defined but not yet reachable.

`messageId` is `null` when the frame could not be parsed far enough to
recover an id. Stack traces are never sent to clients.

A malformed frame does **not** close the connection: the server answers
with `INVALID_MESSAGE` and keeps reading.

## Tests

```bash
python -m pytest
```

Client Connection Server tests cover the six acceptance scenarios (server
starts, health responds, client connects, two clients stay connected,
A → B delivers, unknown destination errors), plus the envelope and routing
contracts. Additional
relay and adapter tests cover multi-device messaging, reconnection, provider
request formats, and safe failures. Provider HTTP responses are mocked.

Run a subset:

```bash
python -m pytest tests/test_client_messaging.py -v
```

Known warning: Starlette's `TestClient` reports that `httpx` is deprecated
in favour of `httpx2`. Cosmetic, and left alone for now.

## Scope

The following remain unimplemented in the client Connection Server (`src/`):

- Response correlation between a client request and a later agent reply
- Session management
- Authentication — client ids are self-declared and unverified
- Persistence; the registry is in-memory and resets on restart

The separate `network_a2a/` relay already provides per-agent authentication,
group isolation, request/response correlation, shared desktop conversations,
and reconnecting clients. Connections and shared chats are kept in memory and
require a single worker and replica. Shared chats last while the host relay
stays open; there is no durable offline queue.

One client Connection Server behaviour worth knowing about:

- **`connection_id`** is server-internal and never exposed to clients.

## Design notes

The reasoning behind each boundary is recorded in the module docstrings
that implement it, together with the acceptance criteria the code is
written to satisfy.
