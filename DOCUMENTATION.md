# Development guide

For app installation, start with [README.md](README.md). For everyday use, see
the [desktop guide](DESKTOP_GUIDE.md). This guide covers development, testing,
the Connection Server, and desktop builds.

- [Development environment](#development-environment)
- [Architecture](#architecture)
- [Run tests](#run-tests)
- [Connection Server](#connection-server)
- [Configuration reference](#configuration-reference)
- [Build the desktop app](#build-the-desktop-app)
- [Update the documentation](#update-the-documentation)
- [Current limits](#current-limits)

## Development environment

Use Python 3.13 or newer and run commands from the repository root. After the
virtual-environment setup in the README, install the test tools:

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

**macOS / Linux:**

```sh
./.venv/bin/python -m pip install -r requirements-dev.txt
```

Alternatively, Conda installs the desktop and test dependencies together:

```sh
conda env create -f environment.yml
conda activate Multiplayer-AI
```

The commands below use `python` from your chosen environment. If your virtual
environment is not activated, replace `python` with
`.\.venv\Scripts\python.exe` on Windows or `./.venv/bin/python` on macOS/Linux.

| Dependency file | Use |
| --- | --- |
| `requirements-desktop.txt` | Desktop app, including networking dependencies |
| `requirements-network.txt` | Headless relay, agent client, and Connection Server |
| `requirements-dev.txt` | Test runner and async test support |
| `environment.yml` | Optional Conda setup using the same requirements files |

Start the desktop app with `python -m desktop_app`. For a headless relay, follow
[network setup](NETWORK_SETUP.md); for model agents, see
[provider adapters](PROVIDER_ADAPTERS.md).

## Architecture

The desktop app starts its own authenticated relay and connects device and model
agents. Other devices reach that relay over WebSocket or authenticated HTTP.
The model adapter runs on the model's device and calls its configured provider.

```text
Desktop app / device client --> authenticated relay --> model agent --> provider
Connection Server --> Agent Gateway --HTTP invoke--> authenticated relay
```

The Connection Server is a separate app. Its gateway integration is implemented:
configure `A2A_RELAY_URL` and `A2A_RELAY_TOKEN` to route agent requests through
the relay. The Connection Server and relay use different WebSocket formats. Run
them on separate ports if using both; the examples otherwise default to `8000`.

| Path | Responsibility |
| --- | --- |
| `desktop_app/__main__.py` | Desktop entry point and single-instance lock |
| `desktop_app/window.py` | Native interface and chat pages |
| `desktop_app/runtime.py`, `workspace_runtime.py` | Workspace switching, relays, agents, and membership |
| `desktop_app/storage.py` | Preferences and secure credential storage |
| `desktop_app/cookbook*.py` | Model catalogue, fit estimates, downloads, and local model controls |
| `desktop_app/attachments.py` | Image and PDF preparation |
| `desktop_app/ollama_service.py` | Local Ollama lifecycle and discovery |
| `network_a2a/server.py`, `client.py` | Authenticated relay and reconnecting client |
| `network_a2a/conversations.py`, `persistence.py` | Shared chats, SQLite archives, and bounded model context |
| `network_a2a/orchestration.py` | Model planning, validated delegation, and synthesis |
| `network_a2a/adapters/` | Model provider handlers |
| `src/server/` | Client connections, frame handling, and delivery |
| `src/routing/router.py` | Client and agent routing decisions |
| `src/agents/gateway.py` | Relay invocation and error translation |
| `src/messages/envelope.py`, `src/errors.py` | Client protocol and error frames |

The interface uses PySide6/Qt. Networking uses Starlette, Uvicorn, WebSockets,
and HTTPX. Pydantic validates client envelopes; SQLite stores chat archives;
keyring accesses the OS credential store; psutil samples hardware and resources.

## Run tests

```sh
python -m pytest
```

Run a focused group while working:

```sh
python -m pytest tests/test_client_messaging.py -v
python -m pytest tests/test_network.py tests/test_adapters.py
```

Tests cover client routing, relay authentication and reconnection, model adapters,
desktop workspaces, saved history, attachments, Cookbook, and Qt interface flows.
Provider calls use mocks or local test endpoints; tests do not make paid cloud
calls. Use pytest to run the whole suite, including pytest-style tests.

To run UI tests without displaying windows, set `QT_QPA_PLATFORM=offscreen`
before invoking pytest. For example, in PowerShell:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest
```

Optional correctness linting uses the configuration in `pyproject.toml`:

```sh
python -m pip install ruff
python -m ruff check .
```

## Connection Server

This development server routes client-to-client messages and can invoke relay
agents. It is not needed to launch the desktop app.

### Start and check the server

```sh
python -m uvicorn src.server.app:app --host localhost --port 8000
```

Open `http://localhost:8000/health`. The response is `{"status":"healthy"}` and
does not depend on an agent being reachable.

### Try messaging

Keep the server running. In a second terminal, start the receiver:

```sh
python scripts/demo_client.py client-B client-A "Hi from B"
```

Its initial send reports `CLIENT_NOT_FOUND` until client-A connects. Leave it
running. In a third terminal, start the sender:

```sh
python scripts/demo_client.py client-A client-B "Hello Client B"
```

The receiver prints `[client-B] <- client-A: Hello Client B`. Successful
client-to-client sends have no acknowledgement; check the receiver for delivery.
Stop each process with Ctrl+C. For another server address, pass `--host` and
`--port` to the demo client.

### Message format

Clients connect to `/ws/{client_id}` and send camelCase JSON envelopes:

```json
{
  "messageId": "msg-123",
  "type": "message",
  "source": {"id": "client-A", "type": "client"},
  "destination": {"id": "client-B", "type": "client"},
  "sessionId": "session-001",
  "timestamp": "2026-10-02T10:00:00Z",
  "payload": {"text": "Hello"}
}
```

The server rewrites `source` to the sending connection's identity.
`destination.type` is `client` for socket delivery or `agent` for gateway
invocation. Agent replies return to the requesting client with the same
`messageId`. Client identities are self-declared and unauthenticated.

Failures return an error frame to the sender:

```json
{
  "type": "error",
  "messageId": "msg-123",
  "code": "CLIENT_NOT_FOUND",
  "message": "Destination client 'ghost' is not connected."
}
```

| Code | Meaning |
| --- | --- |
| `INVALID_MESSAGE` | Invalid JSON, envelope, or agent ID |
| `CLIENT_NOT_FOUND` | Destination client is not connected |
| `AGENT_NOT_FOUND` | No usable agent relay is configured |
| `AGENT_UNAVAILABLE` | Relay unreachable, or agent unknown or outside the caller's group |
| `TIMEOUT` | Gateway request timed out |
| `A2A_ERROR` | Relay rejected credentials or returned an unreadable response |
| `INTERNAL_ERROR` | Unexpected server failure; details stay in server logs |

Unparseable frames may have a null `messageId`. Malformed messages do not close
the connection, and stack traces are not sent to clients.

## Configuration reference

| Variable | Used by | Meaning |
| --- | --- | --- |
| `A2A_TOKEN` | Device client | This device's relay token |
| `A2A_CREDENTIALS_FILE` | Relay | Required path to provisioned credentials JSON |
| `A2A_REQUEST_TIMEOUT` | Relay | Response timeout, default 60 seconds |
| `A2A_MAX_PENDING` | Relay | Maximum pending requests, default 256 |
| `A2A_RELAY_URL` | Connection Server | HTTP(S) relay root for agent calls |
| `A2A_RELAY_TOKEN` | Connection Server | Caller token in the target agents' group |
| `A2A_GATEWAY_TIMEOUT` | Connection Server | Gateway timeout, default 65 seconds |
| `A2A_LOCAL_TOKEN` | Local A2A bridge | Optional Bearer token for the fixed local HTTP endpoint |
| `A2A_MODEL`, `A2A_SYSTEM_PROMPT` | Model adapter | Model ID and system instructions |
| `CONNECTION_HOST`, `CONNECTION_PORT` | Demo client | Server address, defaults `localhost:8000` |

The gateway accepts HTTPS roots and loopback HTTP roots. `A2A_RELAY_URL` is an
HTTP root such as `http://127.0.0.1:8001`, not the device client's WebSocket URL.
Missing or unusable gateway settings leave client-to-client messaging available.

Provider keys and base URLs are listed in [provider adapters](PROVIDER_ADAPTERS.md).
Desktop data paths and recovery are in the [desktop guide](DESKTOP_GUIDE.md#saved-data-and-recovery).

## Build the desktop app

Use an environment with desktop dependencies installed. Build on the operating
system you intend to distribute to. For a Windows bundle:

```sh
python -m pip install pyinstaller
python scripts/build_docs.py
python -m PyInstaller --noconfirm MultiplayerAI.spec
```

The output is `dist/MultiplayerAI/`. Open `MultiplayerAI.exe` directly. When
copying or archiving it, include the **entire folder**, including `_internal`.
The bundle includes Python, app assets, and offline help. It does not require
Python to be installed on the destination device.

`MultiplayerAI.spec` and `desktop_launcher.py` are build inputs. `build/` and
`dist/` are generated and ignored by Git. Rebuild them when needed.

## Update the documentation

Edit the Markdown guide for the task, then rebuild offline help:

```sh
python scripts/build_docs.py
```

The builder uses Qt from the desktop dependencies. It combines the five guides
into `docs.html`, rewrites guide links to internal anchors, and runs without a
visible window. Keep the generated page in sync: the desktop **?** button and
packaged app use it. To add a guide, update `GUIDES` in `scripts/build_docs.py`.

## Current limits

- The Connection Server has no client authentication, session management,
  persistent registry, or client delivery receipts. Its synchronous agent calls
  return correlated replies; asynchronous agent push is not implemented.
- The relay uses a custom transport, without standard A2A discovery or streaming.
  It can bridge synchronous JSON requests to an existing local A2A HTTP endpoint.
- Run the relay with **one worker and one replica**. Connections and in-flight
  requests are in memory, with no durable offline queue or automatic replay.
  Desktop workspaces separately persist their history and membership.
- Shared workspaces need their host online for new requests and synchronization.
  WSS encrypts device-to-relay traffic; the relay can read routed payloads.
- Local model fit estimates and automatic delegation depend on device resources,
  model metadata, and model behavior. They do not guarantee model capabilities.
