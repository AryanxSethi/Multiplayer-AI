# Multiplayer AI

A native desktop app for chatting with AI models and sharing conversations across
devices. Connect local or hosted models, create workspaces, and invite people to
work in the same chat without sharing your provider credentials.

The project is under development. The desktop app, authenticated device relay,
and model adapters are available. The separate Connection Server in `src/` is a
development component with no client authentication or session management.

## Get started

You need **Python 3.13 or newer**. Download or clone this repository, then open a
terminal in its root folder, where `requirements-desktop.txt` lives.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m desktop_app
```

**macOS / Linux:**

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-desktop.txt
./.venv/bin/python -m desktop_app
```

On Linux, unlock a Secret Service keyring before launching. The app saves secrets
in your operating system's credential store: Windows Credential Manager, macOS
Keychain, or Linux Secret Service.

To open the app again, run the last command. Setup creates the local relay and
device identity automatically; you do not need to start a server separately.
For Conda or development setup, see the [development guide](DOCUMENTATION.md#development-environment).

## Send your first message

1. Open **Providers** and choose your provider.
2. Enter its API key and select a model. For local models, install
   [Ollama](https://ollama.com/download) first, then use **Cookbook** to download
   and run a model.
3. Click **Connect agent**. Chat opens automatically; send a message.
4. Use **Invite a device** to bring someone into the workspace chat with its
   existing history. They open directly into that chat after joining.
   **Invite to conversation** shares a separate model conversation.

The first imported model becomes the default. You can add more models and assign
specialist roles later. See the [desktop guide](DESKTOP_GUIDE.md) for those steps.

## Choose a guide

| I want to... | Read |
| --- | --- |
| Use chat, local models, attachments, or shared workspaces | [Desktop guide](DESKTOP_GUIDE.md) |
| Configure a provider or run a model agent from a terminal | [Provider adapters](PROVIDER_ADAPTERS.md) |
| Host a relay or connect devices over a LAN or the internet | [Network setup](NETWORK_SETUP.md) |
| Run tests, understand the code, or build a desktop bundle | [Development guide](DOCUMENTATION.md) |
| Read the guides offline | [Offline documentation](docs.html), also available from **?** in the app |

## Repository layout

| Path | Purpose |
| --- | --- |
| `desktop_app/` | Qt desktop interface, workspaces, local models, and saved settings |
| `network_a2a/` | Authenticated relay, device client, model adapters, and shared chats |
| `src/` | Client Connection Server and its agent gateway |
| `scripts/` | Documentation builder, demo client, and model catalogue tools |
| `tests/` | Automated tests |
| `requirements-*.txt` | Desktop, networking, and test dependencies |

The desktop app uses the relay in `network_a2a/`. The Connection Server in `src/`
has a separate message format and is not required to use the desktop app.

Licensed under [MIT](LICENSE).
