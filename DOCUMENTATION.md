# Multiplayer AI — Documentation

Multiplayer AI is a platform for running one AI session across several people
and several devices. A session leader invites participants into a shared
workspace, so collaborators can work with the same models and the same
conversation without sharing accounts or belonging to the same organisation.

The project ships three things:

- a **native desktop workspace** built with Qt, where people configure models,
  chat with agents, and manage shared conversations;
- an **authenticated device relay** that lets machines on a LAN or the internet
  reach each other's agents;
- a **client Connection Server** that demonstrates client-to-client message
  routing over WebSocket.

> **Status:** under development. The desktop workspace, device relay, and
> provider adapters are implemented and tested. Session management and client
> authentication for the Connection Server are not. See
> [Scope and limitations](#13-scope-and-known-limitations).

---

## Table of contents

1. [Overview](#1-overview)
2. [What Multiplayer AI does](#2-what-multiplayer-ai-does)
3. [Architecture](#3-architecture)
4. [Technologies used](#4-technologies-used)
5. [Requirements](#5-requirements)
6. [Installation](#6-installation)
7. [Running the project](#7-running-the-project)
8. [Configuration](#8-configuration)
9. [Project structure](#9-project-structure)
10. [Testing and code quality](#10-testing-and-code-quality)
11. [Packaging and distribution](#11-packaging-and-distribution)
12. [Building the documentation](#12-building-the-documentation)
13. [Scope and known limitations](#13-scope-and-known-limitations)
14. [Documentation index](#14-documentation-index)
15. [Contributing](#15-contributing)
16. [License](#16-license)

---

## 1. Overview

The repository contains three independently runnable applications that share a
single Python environment:

| Application | Entry point | Purpose |
|-------------|-------------|---------|
| Desktop app | `python -m desktop_app` | Qt workspace for models, chat, workspaces, and the Cookbook |
| Device relay | `network_a2a.server:app_from_env` | Authenticated relay connecting agents across devices |
| Connection Server | `src.server.app:app` | Client-facing WebSocket server for client-to-client messaging |

They are **separate applications with different message formats**. They are not
wired together, and each guide uses port `8000` by default, so run them on
different ports if you need more than one at a time.

The desktop app is the primary product. It starts its own local relay, creates
a private device identity, and connects automatically, so most users never run
the relay or the Connection Server by hand.

---

## 2. What Multiplayer AI does

### 2.1 Desktop workspace

The desktop app provides a dark, native interface for:

- **Workspaces** — named, independent spaces. Each has its own conversations,
  models, and memberships. Owners can rename workspaces, invite or remove
  members, and delete them.
- **Saved chat** — conversations, including the model context that follow-up
  messages depend on, survive switching workspaces and restarting the app.
- **Invitations** — a workspace can be shared with other devices by
  invitation. Invited devices join the same AI chat with its existing history
  and see subsequent messages and replies.
- **Attachments** — files can be attached to a message and sent to models that
  support them.
- **Chat commands** — commands for common actions, with a dismissible command
  panel.
- **Model purposes and task permissions** — imported models can be given a
  purpose so that specialist tasks are routed to an appropriate model.

### 2.2 Shared conversations across devices

Conversation invitations share one AI chat — including its existing history and
all later messages — across invited devices. Membership and online status are
checked before a request is dispatched to a model.

This is provided by the `network_a2a/` relay and its conversation store, not by
the client Connection Server.

### 2.3 The Cookbook

The Cookbook answers a single question: **which models can this machine
actually run?**

It works from published metadata rather than measurements:

- the model's published parameter count and quantisation;
- the device's detected memory, CPU, and dedicated graphics memory;
- an estimated KV-cache cost derived from layer count and context length.

Every figure is attributed. A parameter count read from a publisher is exact
and presented as such; a KV-cache size is an estimate and carries the terms it
used. The Cookbook deliberately cannot report a model as `VERIFIED`: no
catalogue entry or metadata feed contains evidence that a model ran on this
hardware, and the format reserves `VERIFIED` only so that a future hardware
test matrix has somewhere to record it.

Hardware detection reads the machine once per visit. The first open can take
around a second on machines where querying the GPU is slow; see
[Scope and known limitations](#13-scope-and-known-limitations).

### 2.4 Chat orchestration

Chat uses the first imported model as its default. While that model is offline,
another available imported model takes over. When models have purposes and
permitted tasks, requests can be routed to specialists:

1. the orchestrator plans which models should handle which part of a request;
2. assignments are validated against workspace access, online status, and task
   permissions;
3. results are synthesised into a single reply.

If planning fails, the default model answers the original request directly
rather than failing the turn.

### 2.5 Model providers

Provider adapters support local and hosted models:

| Provider | Key | Base URL override |
|----------|-----|-------------------|
| Ollama (local) | `OLLAMA_API_KEY` (optional) | `OLLAMA_BASE_URL`, `OLLAMA_HOST` |
| Bionic GPT | `BIONIC_API_KEY` | `BIONIC_BASE_URL` |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_BASE_URL` |
| Claude | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` |
| Gemini | `GEMINI_API_KEY` | `GEMINI_BASE_URL` |
| Groq | `GROQ_API_KEY` | `GROQ_BASE_URL` |
| DeepSeek | `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` |
| Mistral | `MISTRAL_API_KEY` | `MISTRAL_BASE_URL` |
| OpenRouter | `OPENROUTER_API_KEY` | `OPENROUTER_BASE_URL` |
| Custom OpenAI-compatible | `A2A_PROVIDER_API_KEY` | `A2A_PROVIDER_BASE_URL` |

Providers that speak the OpenAI chat-completions format share one adapter.
Claude and Gemini have dedicated adapters for their own request formats.

### 2.6 Optional web search

Models can be given web search through a configurable search provider
(SearXNG by default). Search credentials are read from an environment variable
and are never written to the settings file.

### 2.7 Client Connection Server

`src/` implements the first milestone of the client communication layer:

- persistent client connections over WebSocket at `/ws/{client_id}`;
- an in-memory registry of connected clients;
- client-to-client message routing;
- structured error frames and event logging;
- a `/health` endpoint that does not depend on any agent being reachable.

Agent destinations are defined in the protocol but not yet delivered —
sending to `destination.type: "agent"` returns `AGENT_NOT_FOUND`.

---

## 3. Architecture

### 3.1 Components

```
                    DESKTOP DEVICE
  ┌────────────────────────────────────────────────────────────┐
  │  desktop_app/  (PySide6)                                   │
  │    workspace rail · chat · Cookbook · settings             │
  │                        │                                   │
  │                        ▼                                   │
  │  network_a2a/client.py  (outbound WebSocket, reconnect)     │
  └────────────────────────┬───────────────────────────────────┘
                           │ WSS (token-authenticated)
                           ▼
  ┌────────────────────────────────────────────────────────────┐
  │  network_a2a/server.py   (single-process relay)            │
  │    per-agent tokens · group isolation · conversations       │
  │    orchestration · provider adapters                        │
  └────────────────────────┬───────────────────────────────────┘
                           │ HTTPS
                           ▼
                    Model providers
              (Ollama, OpenAI, Anthropic, ...)

  ── separate application ──────────────────────────────────────

  ┌────────────────────────────────────────────────────────────┐
  │  src/  Connection Server (Starlette)                       │
  │    /ws/{client_id}  ·  /health                             │
  │    src/routing/router.py   ← the only routing decision      │
  └────────────────────────────────────────────────────────────┘
```

All device connections to the relay are **outbound WebSockets**, so devices do
not need to accept inbound connections or open firewall ports.

### 3.2 Message flow in the desktop app

1. The app starts a local relay, loads or creates its device identity, and
   connects to the relay with its agent token.
2. A message is sent to the relay over the authenticated WebSocket.
3. The relay checks group membership and online status, then dispatches to the
   selected model through a provider adapter.
4. For shared conversations, the relay records the turn and forwards it to the
   other members of the conversation.
5. Replies return over the same socket and are correlated with the request.

Chat history is stored in SQLite (`history.sqlite3`), and model context sent
with a request is bounded so that a long conversation cannot grow without
limit.

### 3.3 Security boundaries

- **Relay authentication** — every device presents its own token. Tokens are
  compared in constant time, and groups are isolated from one another.
- **Credentials** — API keys and device tokens live in the operating system's
  credential store through `keyring`. They are not written to
  `settings.json`.
- **Transport** — use `wss://` and HTTPS when the relay is reachable over the
  internet. Plain `ws://` and provider HTTP are allowed only behind explicit
  opt-in flags intended for a trusted LAN.
- **Connection Server** — client IDs are self-declared and unverified. The
  server rewrites `source` to the connection's ID so one client cannot
  impersonate another, but it does not authenticate who connected.

---

## 4. Technologies used

| Area | Technology | Used for |
|------|------------|----------|
| Language | Python 3.13 | Entire codebase |
| Desktop UI | PySide6 (Qt 6) | Native window, widgets, theming |
| Web framework | Starlette | WebSocket relay and Connection Server |
| ASGI server | Uvicorn | Serving both Starlette applications |
| WebSocket | `websockets` | Device and client connections |
| Validation | Pydantic | Message envelopes and configuration models |
| HTTP client | httpx | Provider API calls and relay client |
| Agent protocol | `a2a-sdk` | Agent-to-agent request plumbing |
| Persistence | SQLite (`sqlite3`) | Chat history and bounded model context |
| Credentials | `keyring` | OS credential store for tokens and API keys |
| System info | `psutil` | Hardware detection for the Cookbook |
| Configuration | `python-dotenv` | Loading local environment files |
| Packaging | PyInstaller | Windows executable bundle |
| Testing | pytest, pytest-asyncio | Test suite |
| Linting | Ruff | Correctness-focused static checks |
| Documentation build | Qt `QTextDocument` | Converts Markdown guides into `docs.html` |

The project is **not** distributed as a Python package and has no `[project]`
table in `pyproject.toml`. `pyproject.toml` configures Ruff only. Applications
are run from the repository root or as a packaged executable.

---

## 5. Requirements

### 5.1 Operating system

- **Windows 10/11** — the packaged installer and release scripts target
  Windows x64. This is the primary supported platform.
- **Linux and macOS** — the Python code is cross-platform (Qt, `keyring`, and
  path handling for `XDG_CONFIG_HOME`), but packaged builds are not provided.

### 5.2 Python

- **Python 3.13**. The Conda environment pins `python=3.13`, and Ruff targets
  `py313`.

### 5.3 Hardware

Required for the desktop app:

- a display capable of running Qt 6;
- enough RAM for the selected models.

Optional:

- **A discrete GPU** improves the Cookbook's accuracy for GPU-served models.
  Without one, the Cookbook assumes unified memory and treats system memory as
  the pool, which is correct for integrated graphics but conservative for
  machines with a separate card it cannot detect.
- **Ollama** if you want to run local models.

The Cookbook does not benchmark anything. It estimates from published metadata
and cannot confirm that a model will run.

### 5.4 Network

- Port `8000` for the relay and for the Connection Server by default.
- Outbound WebSocket connectivity from each device to the relay.
- A TLS-terminating reverse proxy for internet deployments. `Caddyfile.network`
  and `compose.network.yml` provide a starting point.

### 5.5 Development tools

- Conda (recommended) or `venv` with pip.
- `pytest` and `pytest-asyncio` for the test suite.
- Ruff, installed separately, if you want to run the linter.

---

## 6. Installation

### 6.1 Conda environment (recommended)

From the repository root:

```bash
conda env create -f environment.yml
conda activate Multiplayer-AI
```

`environment.yml` creates an environment named `Multiplayer-AI` with Python
3.13 and the pinned runtime dependencies for the relay, Connection Server, and
provider adapters.

### 6.2 pip only

The application requirements are split by component:

| File | Installs |
|------|----------|
| `requirements-network.txt` | Relay and Connection Server runtime |
| `requirements-desktop.txt` | `requirements-network.txt` plus PySide6, keyring, psutil |
| `requirements-dev.txt` | `pytest` and `pytest-asyncio` |

For the desktop app:

```bash
python -m pip install -r requirements-desktop.txt
```

For a headless relay or agent only:

```bash
python -m pip install -r requirements-network.txt
```

For tests:

```bash
python -m pip install -r requirements-dev.txt
```

### 6.3 Windows installer

Packaged releases are archives containing `MultiplayerAI.exe` and two
installers. From an extracted release folder:

```powershell
# Per-user install into %LOCALAPPDATA%\Programs\MultiplayerAI
.\Install-MultiplayerAI.ps1

# Skip launching after install
.\Install-MultiplayerAI.ps1 -NoLaunch

# Skip Start-menu and desktop shortcuts
.\Install-MultiplayerAI.ps1 -NoShortcuts
```

`Install-MultiplayerAI.cmd` is a double-clickable wrapper around the same
script.

---

## 7. Running the project

### 7.1 Desktop app

```bash
python -m desktop_app
```

Useful options:

```bash
# Use a different preferences directory
python -m desktop_app --data-dir "D:\MultiplayerAI-Data"

# Write a startup diagnostic JSON file and exit without opening a window
python -m desktop_app --check-startup startup.json
```

The app starts its own local relay automatically. See the
[desktop guide](DESKTOP_GUIDE.md) for model setup, web search, attachments, and
shared conversations.

### 7.2 Connection Server

```bash
python -m uvicorn src.server.app:app --host localhost --port 8000
```

Verify it is healthy:

```bash
curl http://localhost:8000/health
```

```json
{"status":"healthy"}
```

The health endpoint does not depend on any agent being reachable.

Try client-to-client messaging with three terminals:

```bash
# Terminal 1: the server
python -m uvicorn src.server.app:app --host localhost --port 8000

# Terminal 2: the receiver
python scripts/demo_client.py client-B client-A "Hi from B"

# Terminal 3: the sender
python scripts/demo_client.py client-A client-B "Hello Client B"
```

The sender prints no acknowledgement on success; the receiver prints:

```
[client-B] <- client-A: Hello Client B
```

That silence is the success signal. Delivery receipts are not implemented, so a
sender cannot distinguish "delivered" from "the server died" by watching its
own socket.

If the server is not on `localhost:8000`, pass `--host`/`--port` or set
`CONNECTION_HOST`/`CONNECTION_PORT`.

### 7.3 Authenticated device relay

The relay requires per-device tokens. Provision them first:

```bash
python -m network_a2a.provision laptop_alice laptop_bob desktop_charlie
```

Run the relay with a single worker and a larger WebSocket frame limit:

```bash
python -m uvicorn network_a2a.server:app_from_env --factory \
  --host 0.0.0.0 --port 8000 --workers 1 \
  --ws-max-size 8454144 --ws-ping-interval 20 --ws-ping-timeout 20
```

`--workers 1` is required: connections, shared chats, and membership are held in
memory.

Then connect a device agent:

```bash
# PowerShell
$env:A2A_TOKEN = 'paste-this-devices-token'
python -m network_a2a --server wss://agents.example.com/connect
```

Send a message to another device:

```bash
python -m network_a2a --server wss://agents.example.com/connect \
  --to laptop_bob --payload '{"text":"Hello Bob"}'
```

`NETWORK_SETUP.md` covers HTTPS/WSS deployment, provisioning, and
troubleshooting.

### 7.4 Headless agent with a model provider

```bash
# List supported providers without connecting
python -m network_a2a --list-providers

# Serve a local Ollama model as this device's agent
python -m network_a2a --server wss://agents.example.com/connect \
  --provider ollama --model llama3.2

# Use a hosted provider
$env:OPENAI_API_KEY = '...'
python -m network_a2a --server wss://agents.example.com/connect \
  --provider openai --model gpt-4.1-mini
```

Other provider options: `--provider-base-url`, `--api-key-env`, `--model`,
`--system-prompt`, `--max-tokens`, `--provider-timeout`,
`--provider-concurrency`, and `--allow-insecure-provider` for a trusted LAN.
See [PROVIDER_ADAPTERS.md](PROVIDER_ADAPTERS.md).

---

## 8. Configuration

### 8.1 Environment variables

| Variable | Used by | Meaning |
|----------|---------|---------|
| `A2A_TOKEN` | Agent client | This device's relay token |
| `A2A_RELAY_URL` | Connection Server | Relay URL for agent calls |
| `A2A_RELAY_TOKEN` | Connection Server | Token used when calling the relay |
| `A2A_CREDENTIALS_FILE` | Relay | Path to the provisioned credentials file |
| `A2A_LOCAL_TOKEN` | Local relay | Token for the app's own local relay |
| `A2A_REQUEST_TIMEOUT` | Relay client | Request timeout in seconds |
| `A2A_GATEWAY_TIMEOUT` | Agent gateway | Timeout for gateway calls |
| `A2A_MAX_PENDING` | Relay | Bound on queued requests |
| `A2A_MODEL` | Provider adapter | Default model ID |
| `A2A_SYSTEM_PROMPT` | Provider adapter | Default system instructions |
| `A2A_PROVIDER_API_KEY` | OpenAI-compatible | Key for a custom endpoint |
| `A2A_PROVIDER_BASE_URL` | OpenAI-compatible | Base URL for a custom endpoint |
| `CONNECTION_HOST` | Demo client | Connection Server host |
| `CONNECTION_PORT` | Demo client | Connection Server port |

Provider-specific variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`,
`OPENROUTER_API_KEY`, `BIONIC_API_KEY`, `OLLAMA_BASE_URL`, and the matching
`*_BASE_URL` overrides) are read by the adapters directly.

### 8.2 Desktop data locations

| Platform | Preferences directory |
|----------|-----------------------|
| Windows | `%LOCALAPPDATA%\MultiplayerAI` |
| Linux / macOS | `$XDG_CONFIG_HOME/multiplayer-ai` (default `~/.config/multiplayer-ai`) |

The directory holds:

- `settings.json` — non-secret preferences;
- `history.sqlite3` — saved conversations and model context;
- workspace and catalogue data.

Tokens and API keys are stored in the operating system credential store, not in
these files. Override the directory with `--data-dir`.

### 8.3 Ports

Both the relay and the Connection Server default to `8000`. If you run both,
change one of them explicitly.

---

## 9. Project structure

| Path | Responsibility |
|------|----------------|
| `desktop_app/__main__.py` | Desktop entry point, single-instance lock, launch screen |
| `desktop_app/window.py` | Workspace rail, chat interface, toast/command UI |
| `desktop_app/runtime.py` | Saved workspaces and command dispatch |
| `desktop_app/workspace_runtime.py` | Per-workspace relays, agents, membership |
| `desktop_app/cookbook.py` | Hardware detection and model-fit arithmetic (no widgets) |
| `desktop_app/cookbook_panel.py` | Cookbook page, refresh flow, scroll handling |
| `desktop_app/cookbook_hub.py` | Catalogue browsing and download |
| `desktop_app/storage.py` | Preferences, credential store, workspace vault |
| `desktop_app/theme.py` | Colours, spacing, and stylesheet generation |
| `desktop_app/widgets.py`, `layout.py`, `icons.py` | Shared UI building blocks |
| `desktop_app/attachments.py` | File attachment handling |
| `desktop_app/markdown.py` | Markdown rendering for messages |
| `desktop_app/ollama_service.py` | Local Ollama lifecycle and model discovery |
| `desktop_app/lan.py` | LAN address discovery and connectivity checks |
| `desktop_app/diagnostics.py` | Crash diagnostics |
| `network_a2a/server.py` | Authenticated single-process relay |
| `network_a2a/client.py` | Reconnecting device client and request correlation |
| `network_a2a/conversations.py` | Shared chat history and membership |
| `network_a2a/orchestration.py` | Model planning, validated assignments, synthesis |
| `network_a2a/persistence.py` | SQLite archives and bounded model context |
| `network_a2a/provision.py` | Per-device token provisioning |
| `network_a2a/chat_commands.py` | Chat command handling |
| `network_a2a/web_search.py` | Search provider integration |
| `network_a2a/adapters/` | Provider handlers (Ollama, OpenAI, Claude, Gemini, compatible) |
| `src/server/app.py` | Connection Server HTTP/WebSocket entry point |
| `src/server/connection_manager.py` | In-memory client registry and delivery |
| `src/routing/router.py` | The single routing decision point |
| `src/messages/envelope.py` | Client-facing message format |
| `src/errors.py` | Error codes and error frames |
| `src/agents/gateway.py` | Boundary for calling an agent relay |
| `scripts/demo_client.py` | Command-line client for manual testing |
| `scripts/build_docs.py` | Builds `docs.html` from the Markdown guides |
| `scripts/grow_catalogue.py` | Catalogue maintenance |
| `tests/` | Test suite (45 modules) |
| `MultiplayerAI.spec` | PyInstaller build definition |
| `package_windows_release.py` | Builds the Windows release archive |
| `environment.yml` | Conda environment definition |
| `requirements-*.txt` | pip requirements by component |
| `pyproject.toml` | Ruff configuration |
| `pytest.ini` | pytest configuration |

Routing decisions for the Connection Server live only in
`src/routing/router.py`; the WebSocket handler owns transport, not routing.

---

## 10. Testing and code quality

### 10.1 Running the tests

```bash
python -m pytest
```

Configuration lives in `pytest.ini`:

- `pythonpath = .` so the suite runs the same way however pytest is invoked;
- `testpaths = tests`;
- `asyncio_mode = auto` for async tests;
- `-q --strict-markers`.

Run a subset:

```bash
python -m pytest tests/test_client_messaging.py -v
```

At the time of writing the suite reports **1,427 passed, 2 skipped** across 45
test modules. Provider HTTP responses are mocked; no test requires a live model
or network access.

### 10.2 Linting

```bash
python -m ruff check .
```

Ruff targets Python 3.13 and deliberately selects only correctness rules
(`E4`, `E7`, `E9`, `F`): unused names, redefinitions, and syntax-level errors.
It is not configured to enforce import order or house style. Ruff is a
developer tool and is not listed in the runtime requirements.

### 10.3 Continuous integration

There is currently **no CI workflow** in the repository. Verification is run
locally. If you add automation, the suite and the linter are the two commands
that need to pass.

---

## 11. Packaging and distribution

### 11.1 Build the executable

```bash
python -m PyInstaller MultiplayerAI.spec
```

The spec bundles the Qt assets, the Uvicorn and WebSocket submodules, and the
Conda runtime DLLs into `dist/MultiplayerAI/`.

### 11.2 Build the release archive

```bash
python package_windows_release.py
```

This produces `dist/MultiplayerAI-Windows-x64.zip` containing the executable
bundle, both installers, `DESKTOP_GUIDE.md`, and `docs.html`. The script
verifies archive integrity before reporting success.

### 11.3 Install

Extract the archive and run `Install-MultiplayerAI.ps1` (or the `.cmd`
wrapper). Files are installed per user under
`%LOCALAPPDATA%\Programs\MultiplayerAI`.

---

## 12. Building the documentation

`docs.html` is generated from the Markdown guides:

```bash
python scripts/build_docs.py
```

The script renders each guide with Qt's Markdown parser, rewrites headings with
stable, guide-prefixed anchors, and rewrites relative links so they work inside
the single offline HTML page. The desktop app bundles `docs.html` and shows it
from its own menu.

To add a guide, add an entry to `GUIDES` in `scripts/build_docs.py` and rebuild.

---

## 13. Scope and known limitations

### 13.1 Connection Server

Not implemented in `src/`:

- session management;
- client authentication — client IDs are self-declared and unverified;
- persistence — the client registry is in memory and resets on restart;
- delivery receipts, so a sender cannot confirm delivery;
- agent destinations — these return `AGENT_NOT_FOUND`.

`AGENT_UNAVAILABLE`, `SESSION_NOT_FOUND`, `A2A_ERROR`, `TIMEOUT`, and
`INTERNAL_ERROR` are defined but not currently reachable.

### 13.2 Relay

- Connections, shared chats, and membership are **in memory**. Run a single
  worker and a single replica.
- Shared chats last while the host relay stays open. There is no durable
  offline queue.
- The relay uses a custom transport. Standard A2A discovery and streaming are
  not implemented.

### 13.3 Desktop app and Cookbook

- The Cookbook estimates from published metadata and cannot verify that a model
  runs. `VERIFIED` is reserved but never produced.
- The first Cookbook open can take around a second, because hardware detection
  queries the system (including the GPU) synchronously.
- GPU detection depends on the platform's tooling being present. When a
  dedicated GPU cannot be detected, system memory is treated as the model pool.

### 13.4 Two servers, one port

The Connection Server and the agent relay are separate applications with
different protocols. They are not integrated. Both default to port `8000`.

---

## 14. Documentation index

| Document | Contents |
|----------|----------|
| [README.md](README.md) | Project overview, protocol, errors, and scope |
| [DESKTOP_GUIDE.md](DESKTOP_GUIDE.md) | Installing and using the desktop app |
| [NETWORK_SETUP.md](NETWORK_SETUP.md) | Provisioning, hosting, HTTPS/WSS, and troubleshooting |
| [PROVIDER_ADAPTERS.md](PROVIDER_ADAPTERS.md) | Provider configuration and examples |
| [ROADMAP.md](ROADMAP.md) | Planned direction |
| [docs.html](docs.html) | Generated offline documentation page |

---

## 15. Contributing

1. Create a branch from `main`.
2. Make the change with tests.
3. Run the suite and the linter:

   ```bash
   python -m pytest
   python -m ruff check .
   ```

4. Open a pull request describing the behaviour changed and how it was
   verified.

Conventions worth following:

- **Routing decisions** belong in `src/routing/router.py`, not in transport
  handlers.
- **Arithmetic over hardware or catalogue data** belongs in modules that do not
  import Qt, so it can be tested without a display.
- **Secrets never go in `settings.json`**; use the credential store.
- **Tests should assert the behaviour**, not merely restate the setup. A test
  that asserts a value it just set passes whether or not the code works.

---

## 16. License

MIT License. Copyright (c) 2026 Rijan Bhattarai. See [LICENSE](LICENSE).
