# Multiplayer AI desktop

Multiplayer AI now has a native Qt desktop application. The previous web UI has
been removed. The networking APIs and command-line agent tools remain available.

## Launch the Windows app

Download `MultiplayerAI-Windows-x64.zip` from the release and extract **all**
files. Double-click **Install-MultiplayerAI.cmd**. It installs the bundled app
for your Windows account, adds Desktop and Start menu shortcuts, and opens it.
No Python installation, dependency commands, or administrator access is needed.
Keep the two installer scripts beside the `MultiplayerAI` folder until installation
finishes. Close the app and run the installer from a new release to update it;
your settings and saved credentials are preserved. Installation needs no download.

The installer is unsigned. Only run a release you trust. You can also use the app
directly without installing:

Open `dist/MultiplayerAI/MultiplayerAI.exe`. Keep the executable and its
`_internal` folder together when copying the application to another Windows
device. The app runs without a separately installed Python environment.

For development or other supported desktop operating systems:

```sh
python -m pip install -r requirements-desktop.txt
python -m desktop_app
```

On Linux you also need an unlocked Secret Service keyring. Windows uses Windows
Credential Manager, and macOS uses Keychain. The app requires a working secure
credential store; it does not fall back to writing secrets to a plaintext file.

## What happens automatically

Click **?** in the left rail to open the bundled documentation page in your
browser. Choose the desktop, model-provider, networking, development, or roadmap
guide for your use case. The guides work offline.

On launch, the app:

1. Creates or restores this device's private identity.
2. Starts an authenticated local relay on an available port, reusing its saved
   port on subsequent launches.
3. Connects a device agent so you can exchange real connectivity messages
   immediately, without configuring a model.
4. Restores model agents marked **Start automatically**.
5. Restores the selected workspace, its saved conversations, and any draft.
   Rejoins a selected remote workspace; if its host is unavailable, saved history
   remains readable and the app retries. You can switch to an owned workspace
   while waiting.

Closing the app disconnects its agents and shuts down its local relay. There is
no background service left running after the window closes. The app supports a
single instance per preferences directory.

## Create and manage workspaces

Click **+** in the left rail and choose **Create a workspace**. Give it a name;
the app opens a separate workspace with its own chats, models, members, and
invitations. Click a workspace's initials in the rail to switch to it. Your owned
workspaces keep their relays and models running while you view another workspace,
so their members can continue working. All owned relays stop when the app closes.

Open **Workspace settings** to rename a workspace and see its members and online
status. Owners can invite members and click **Remove** to revoke a member's
invitation, close its connection, and remove access to shared chats. Removing a
local model also removes its configuration and saved credentials. Members can
view the list; the owner controls the workspace name and membership.

**Delete workspace** removes the owned workspace's saved chats, configuration,
and credentials on this device and disconnects its members. Other workspaces
remain available; deleting the last workspace creates a fresh one. In a joined
workspace, **Leave workspace** removes its local history and saved invitation.
When the host is reachable, leaving also revokes that invitation on the host.
If the host is offline or its membership is managed separately by an operator,
the invitation is forgotten locally; the owner can revoke it on the host.

## Connect a model

Open **Providers** or click **Connect a model**. Choose a provider, configure
the API root if needed, and enter its API key. Available models load into the
**Model** dropdown automatically. Select a model or enter its ID directly. Click
**Connect agent**; the app launches the adapter in its own networking thread.
There is no generated terminal command or separate agent process to start.

For Ollama, start the Ollama application and install your chosen model first.
Its dropdown refreshes while the import form is open and clears when Ollama
cannot be reached. Removed models are also dropped from the choices.
**Find models** refreshes installed Ollama models or available models from the
selected provider, including Claude and Gemini. Paginated catalogs are collected
in full; Gemini's list includes models that support chat generation. Discovery
uses the [Claude Models API](https://platform.claude.com/docs/en/api/models/list)
and [Gemini Models API](https://ai.google.dev/api/models) for those providers.
You can always enter the model ID directly.
For Bionic GPT or a custom compatible endpoint, enter your deployment's API root.

Import errors outline the affected field and form in red, open **Agents**, and
scroll to that field. Editing the field clears its error highlight.

The **Providers** page lists imported models. Click **Edit model** to change the
model, instructions, image support, or internet access. Models running on another
device are edited on that device. Model names stay fixed so existing chats and
invitations keep their identity.

## Chat and model orchestration

Chat works as soon as a model connects. It replies through the default
model directly until you configure automatic delegation. You can send requests
such as "Make a hello world program" without entering a purpose, selecting task
types, or enabling delegation. The first imported model is the automatic default,
even when a later model starts first. An empty saved default is repaired automatically.
Greetings such as "hello", "hey there", and "good morning" always go directly
to one connected imported model, even when delegation is enabled. They require
neither JSON planning nor a separate chat permission. If the default is offline,
another available imported model handles both conversation and work requests.
Greeting-prefixed requests such as
"Hello, debug this function" still use the coordinator to plan specialist work.

Each imported model has its own purpose and permitted tasks, regardless of its
provider or model ID. In **Connect a model** or **Edit model**, describe what you
want it used for, select tasks such as **Coding**, **Debugging**, **Reasoning /
problem solving**, **Research**, or **Writing**, and enable **Allow automatic delegation
to this model**. For example, permit coding and debugging on your chosen specialist.
A conversational model needs no task permissions.
Selected options show a tick. Existing profiles remain outside
automatic delegation until you configure them. Direct model chats remain available.

Connecting a model opens **Chat** automatically. The first imported
model becomes the default; additional models join the same chat
without replacing its history or default. Saved models also open Chat
on startup when no conversation was previously selected.
New connections get an unused agent name, such as `ollama-agent-2`, so importing
another model from the same provider preserves the first model.

In **Conversations**, you can change **Default model** and click **Use model**.
The workspace owner sets this optional preference; invited members use the same
default. The choice is saved per workspace. Only active models appear in this
dropdown; when none are available it shows **No active models**. Ollama models
are available only while their server can be reached and the model is installed.
Stopping Ollama hides its models; starting it again restores running agents
automatically. A saved default remains saved while unavailable.
**Automatic · first available model** restores the original default and uses
an available model while that default is offline. Specialist routing works best with a model that
follows JSON planning instructions reliably. Basic chat does not require planning.

Automatic routing uses only models imported by the workspace owner or explicitly
selected by the owner as coordinator. These actions approve the model to receive
chat context across the workspace, including retained text and attachments.
An invited device's published purpose and delegation preferences do not grant
this access. Other invited models remain available through explicitly targeted
chats; joining the workspace or a conversation alone does not approve automatic routing.

Write requests naturally: "Code a parser", "Debug this function", or "Solve this"
followed by code. The coordinator reads the conversation and your model-purpose descriptions
to select a worker, or up to three ordered tasks for a compound request. Later
workers receive earlier results; the coordinator combines multiple results into
one answer. It can ask for clarification when no model fits. The relay checks
online status, workspace access, and permitted task types before dispatching.
Selection and task classification come from the coordinator, so their quality
depends on the model you choose. Standalone greetings use the direct chat route;
specialist work is planned from the full conversation, without model-name rules.

Greetings, thanks, and ordinary conversation can be answered by the coordinator
without assigning a specialist. When no specialist is available, the default
answers directly. Unreadable planning JSON gets one repair attempt. If that fails,
or the planning request fails or times out, an available default model receives
the original history and attachments without planning instructions. Image history
requires a model with image support. Invalid assignments also fall back without
dispatching the rejected tasks. Worker requests are not replayed after failures.

Chat shows the chosen models and the coordinator's brief selection explanation.
Each reply names the model that produced its text: the worker for a single
assignment, or the coordinator when it combines several results or asks for
clarification. This identity is saved with each reply and shared with invited
devices. Regular answers have no filled background; code retains its highlighting.
Individual model chats hide default-model controls to leave more room for messages.
The sidebar contains workspace navigation. Open an individual model's chat from
**Agents**, and switch saved or shared chats with the compact conversation picker
on the chat page. The picker stays hidden when there is only one chat.
In a narrow window, **Menu** opens navigation. A failed request opens its conversation
and shows its error beside the messages; input and attachment errors remain
beside the affected chat.

History, code, and attachments reach the assigned workers; image-bearing history requires a
worker with image support enabled during delegation, or a vision-capable selected
model in basic chat. During delegation a text coordinator receives image names
rather than image pixels. **Invite to conversation** also shares a chat and its
assignments. Purpose descriptions are shared with the workspace; the coordinator
receives those descriptions and conversation text. Provider keys, API roots, and
private model instructions stay on each model's device.

A request can make several provider calls and has a four-minute overall limit.
Invalid plans, unavailable models, and timeouts are reported without automatically
replaying the request. These model agents answer through provider APIs; this flow
does not give them filesystem access or execute the code they return. The shared
model profiles use the app's authenticated relay metadata, not standard A2A discovery.

## Model internet access

To enable internet access, select **Allow this model to search the web** and
choose a **Search provider**. For the simplest setup, choose **Ollama web search**,
create a key in [your Ollama account](https://ollama.com/settings/keys), and paste
it into **Search API key**. You do not need to run a search server. This search
service works with any connected model, including local Ollama models and models
from other providers. The search key is stored separately from the model's key
in your OS credential store; leave it blank when editing to keep the saved key.
Ollama requires an account key even when your local model does not need one.
See the [Ollama web search API](https://docs.ollama.com/capabilities/web-search)
for account requirements and API details.

Choose **Automatic** or **Always** for **Search mode**. Automatic mode lets the
model request a search for current or uncertain facts;
it cannot guarantee detection of everything absent from its training data.
Always mode searches the question before each answer. The app supplies up to
five result snippets and source links to the model. It does not fetch full pages.
Use **Test web search** to check the service before saving. Missing setup fields,
rejected keys, request limits, and connection problems include an explanation
and a next step. A connection that returns no results is reported separately.

If you already run a search server, choose **SearXNG**, enter its server address,
and use **Test web search**. This address is separate from your model's API root;
for example, a local SearXNG server might use `http://localhost:8888`, while
Ollama uses `http://127.0.0.1:11434`. Existing SearXNG profiles keep their search
settings. The SearXNG server must enable JSON search in `settings.yml`, as described in the
[SearXNG Search API](https://docs.searxng.org/dev/search_api.html):

```yaml
search:
  formats:
    - html
    - json
```

Many public SearXNG servers disable JSON access. If a server blocks search, ask
its owner to enable API access or choose Ollama web search.

Internet access is off until you enable it for that model. Search queries go to
your selected search service; the search key goes only to Ollama's search API,
and model API keys go only to the model provider. The app supplies search snippets
and links to the model; it does not give the model unrestricted internet access.

## Attach images and PDFs

In a model's conversation, click **Attach images / PDFs** and select your files.
The app reads them locally and shows the prepared files above the composer.
Add a question and send, or send the files alone to request an analysis. **Remove**
discards an attachment before sending. Files work in direct and shared chats,
and their content remains available in saved conversation history for follow-ups.
The selected model's provider receives the prepared content when you send it;
members invited to a shared chat can see its attachments.

Text PDFs are extracted for any model. Enable **image support** with a
vision-capable model to send images or scanned PDFs. Vision mode includes PDF
page images for diagrams as well as extracted text. For PDFs above 16 pages,
only extracted text is included; split the PDF to include page images. Scanned
PDFs need at most 16 pages per file. Password-protected PDFs must be unlocked.

Attach up to eight files, each up to 20 MiB, within an 8 MiB prepared request
and about 190 KB of extracted text. PDFs can have up to 200 pages. Large documents
must be split. Images are resized to fit 1600 pixels and converted to JPEG;
animated GIFs use their first frame. No OCR or image capability is added to a
text-only model by enabling a checkbox.

Tokens and API keys are stored in the OS credential store. Nonsecret settings
(model IDs, endpoints, instructions, and autostart choices) live in:

- Windows: `%LOCALAPPDATA%\MultiplayerAI\settings.json`
- Linux: `$XDG_CONFIG_HOME/multiplayer-ai/settings.json` or
  `~/.config/multiplayer-ai/settings.json`
- macOS: `~/.config/multiplayer-ai/settings.json`

Use `--data-dir PATH` to select a different preferences directory.
The same directory contains `workspaces.json` and the default workspace's
`history.sqlite3`. Additional workspaces keep separate settings and history
under `workspaces/<workspace-id>/`. Chat history is saved on disk; tokens and
provider keys remain in the OS credential store with workspace-specific keys.
If the app closes unexpectedly, `desktop-crash.log` in the preferences directory
records Python exceptions, Qt messages, and native crash details for diagnosis.

## Recover a lost device identity

The token for an identity lives in the OS credential store, while the list of
identities lives in `settings.json`. If the token is removed but the entry stays
behind — a cleared keyring, a store that was locked or reset, or settings
restored from a backup on another machine — the app replaces that identity
instead of refusing to start. It starts normally, tells you that the identity
changed, and keeps every other preference, including your theme.

Because the old token is gone, workspaces that were joined with that identity
stop recognising the device and need a fresh invitation. Other identities on the
same device are unaffected.

To discard an identity deliberately, for instance after suspecting the token was
exposed, use **Settings → Reset local identity**. This replaces every identity on
the device with a new token, stops and restarts connected agents, and leaves
joined workspaces requiring new invitations. The action asks for confirmation
first.

An *unavailable* credential store is still reported as an error rather than being
treated as empty, because a replacement token could not be saved either. Unlock
the store and relaunch.

## Invite another device

In your local workspace, click **Invite a device**. Give the other device a new
identity, then specify a relay address it can reach. Each invitation contains a
different private token. Copy it and send it privately to the intended user.

For devices on a trusted LAN, leave **Share this relay on my local network**
enabled. Under **Host network**, choose the Wi-Fi or hotspot adapter connected
to the other device. The suggested address favors active physical adapters over
VPN and virtual adapters; you can choose another adapter or enter an address
manually. Creating the invitation makes the
relay listen on the LAN; the host firewall must allow the displayed port.
Sharing remains enabled across app launches after you opt in. A `ws://` LAN
connection is plaintext, so use WSS for untrusted networks.
Create a new invitation after changing networks, because the host's IP address
may change. LAN discovery, messaging, and synchronization connect directly,
without using the system HTTP or WebSocket proxy. Public WSS relays and model
providers continue to support proxy settings.

If joining times out, keep the host open and check the invitation's address and
port. On a Windows guest, run this in PowerShell using the host address and port:

```powershell
Test-NetConnection -ComputerName 192.168.43.12 -Port 54321
```

`TcpTestSucceeded: False` means the guest cannot reach that listener. Check the
host's adapter address and allow Multiplayer AI through the host firewall on
the appropriate network profile. Keep the firewall enabled. On a managed campus
network, ask IT whether connections between devices and the relay port are
allowed.

The same Wi-Fi name does not guarantee that devices can reach each other.
Campus and guest networks may isolate devices or put them on separate VLANs,
including devices connected through different access points. Some mobile
hotspots also isolate clients. See Cisco's
[peer-to-peer blocking documentation](https://www.cisco.com/c/en/us/td/docs/wireless/controller/9800/17-16/config-guide/b_wl_17_16_cg/peer-to-peer-client-support.html).
Use a trusted network that permits device-to-device traffic, a suitable private
network/VPN, or the WSS option below when direct connections are blocked.

For internet connections, use a reachable TLS-protected relay:

- Join the publicly hosted relay described in [NETWORK_SETUP.md](NETWORK_SETUP.md)
  using its WSS URL and a device token supplied by its operator; or
- Put an HTTPS/WebSocket reverse proxy in front of your desktop's relay and use
  its public WSS address in the invitation.

The app automatically sets up its local services, credentials, and connections.
It does not provision a cloud server, configure router port forwarding, change
firewall rules, or bypass carrier NAT. Use a public relay or a suitable private
network/VPN when devices cannot reach one another directly. The host app must
stay open if its relay is serving the workspace.

## Join a workspace

Click **+** in the left workspace rail and choose **Join a workspace**. Paste the invitation, or enter the WSS
relay address and the device's token. The app validates the invitation, connects
the device, and shows the agents in its group. This workspace is remembered for
the next launch. Use the workspace's initials to return to it, or click **M** to
open your first owned workspace. One shared workspace at a time: while this device is a
member of another one, the join card says which and why, and the button stays off until
that membership ends. To end it, switch to the joined workspace using its initials in the
rail: the Leave workspace control sits on the card for the workspace you are currently
in, so it only appears once you have switched. Leaving it there removes the membership and
lets you join another.
Reconnecting to the workspace you are already in still works at any time.
While joining, the button shows **Connecting.** Success opens an online peer's
conversation; if nobody else is online, the app shows the agent list instead.
A timeout or unreachable host produces a visible error. LAN invitations require
a reachable host address on your network and an allowed host firewall port.
Use a different invitation for each laptop: an identity already connected on
another device cannot be reused.

In a remote workspace, configuring a model uses your invited identity. Hosts
can create multiple local model identities; a remote device needs additional
invited tokens to expose additional identities. Provider agents in your local
workspace continue running while you use a remote workspace.

## Work with your team

**Overview** shows real agent counts and activity from the current visit.
Counts include model agents, excluding users and connectivity devices. Click
the workspace agent count or the **agents** button in a chat to see each model's
name, provider, model ID, and online status.
**Agents** supports search, editing local model configuration, and stopping local
model agents. **Conversations** sends requests to connected agents and includes
history in follow-ups to provider agents. Unconfigured device agents reply with
connectivity echoes rather than AI-generated text.
Model replies display Markdown headings, bold and italic text, lists, links,
tables, and code blocks. You can select and copy their rendered text; the
original message stays intact for conversation history and follow-up requests.
Requests from another device appear in its conversation on the receiving desktop,
with an unread count in the agent list and the local agent's response below.
Select the sending device to read incoming messages and send a request back.
To share the **same AI conversation**, open the model's chat and click **Invite
to conversation**. Create a new invitation for the other device, then paste it
into **+ → Join a workspace** on that device. The shared chat appears on both devices with the
existing messages. Either participant can send the next message; everyone sees
the AI reply and the model receives their combined conversation history.
An ordinary **Invite a device** invitation connects the workspace without sharing
a chat. Shared chats are visible only to their invited devices.

The dark theme uses black backgrounds and white text, with a workspace rail and
navigation sidebar. **Settings → Theme** also offers Light, 39, and Follow system.
**Reduce animations** in Settings disables page fades. **Resources** shows local
CPU, memory, and disk usage while that page is open.

Direct chats, shared chats, and their model context are saved as messages arrive
and restored after switching workspaces or reopening the app. Shared history and
membership persist on the host; guests also keep a local copy for offline
reading. Existing invitations remain valid across a host restart unless revoked.
Keep the host app open to send new requests and synchronize a shared workspace.

The complete chat archive remains saved. Follow-up requests send up to 100 recent
user and assistant messages within about 190 KB of text and, for attachments,
an 8 MiB prepared request; older messages
remain readable but are outside that model context window. Large individual
shared replies can be shortened to fit the relay. Requests interrupted by an
app shutdown are marked unsuccessful and never automatically replayed.

## Build a Windows distribution

From an environment with the desktop dependencies installed:

```sh
python -m pip install pyinstaller
python -m PyInstaller --noconfirm MultiplayerAI.spec
python package_windows_release.py
```

The build creates `dist/MultiplayerAI/`. This is a local, unsigned distribution;
build on each operating system you want to distribute to, and sign releases
before public distribution. The checked build in this workspace targets Windows.
The packaging script creates `dist/MultiplayerAI-Windows-x64.zip` with the
complete app, installer scripts, and this guide. Upload that ZIP as a release asset.
The Windows build omits unused Qt QML, virtual keyboard, and translation
components. It retains PDF extraction, supported image formats, the native interface, animations,
networking, credentials, and all supported provider adapters. Source code,
tests, build tools, and local settings are not included in the release ZIP.

## Verify

```sh
python -m pytest
```

Desktop runtime tests cover automatic relay startup, secure-token separation,
provider routing, two-desktop messaging, saved chat context after reopening,
workspace creation/switching/deletion, member revocation, invalid
invitation recovery, TLS validation, and orderly shutdown. Native UI flows are
also checked with Qt's offscreen platform. Provider calls in verification use
mock endpoints; no paid cloud calls are made.
