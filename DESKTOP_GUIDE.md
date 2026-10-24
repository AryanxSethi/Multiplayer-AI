# Desktop guide

[Start here](README.md) for installation. This guide covers using the app once
it opens. Click **?** in the workspace rail to read these guides offline.

- [Connect a model](#connect-a-model)
- [Chat and model orchestration](#chat-and-model-orchestration)
- [Cookbook: find, download, and run local models](#cookbook-find-download-and-run-local-models)
- [Create and manage workspaces](#create-and-manage-workspaces)
- [Invite another device](#invite-another-device)
- [Share an AI conversation](#share-an-ai-conversation)
- [Model internet access](#model-internet-access)
- [Attach images and PDFs](#attach-images-and-pdfs)
- [Saved data and recovery](#saved-data-and-recovery)
- [Troubleshooting](#troubleshooting)

## Connect a model

1. Open **Providers** or click **Connect a model**.
2. Choose a provider and enter its API key. Local Ollama usually needs no key.
   For Bionic GPT or a custom compatible server, enter your deployment's API root.
3. Select a model from the **Model** dropdown, or enter its exact ID.
   **Find models** refreshes the list.
4. Click **Connect agent**. The app opens Chat and runs the adapter automatically.

For Ollama, install Ollama and download the model first. You can do the download
from [Cookbook](#cookbook-find-download-and-run-local-models). The app starts a
stopped local Ollama server when needed; remote servers must already be running.

The first imported model becomes the default. Additional imports keep the same
chat history and get distinct agent names. **Start automatically** restores a
model connection when you reopen the app.

Use **Providers → Edit model** to change the model, instructions, image support,
or internet access. Model names stay fixed to preserve existing chats. Edit a
remote model on the device that runs it. See [provider adapters](PROVIDER_ADAPTERS.md)
for supported providers and endpoint settings.

## Chat and model orchestration

Send a message in **Conversations** to use the default model. Basic chat needs
no purpose, role, or delegation settings. If the saved default is offline,
another available imported model answers. Follow-up messages include recent
conversation history.

Use the conversation picker to switch saved chats. Start a message with
`@model-name` to choose which model answers **in the current conversation**.
It receives the recent history and attachments, and its reply stays in the same
thread. This also works in shared chats without changing their default model.
A bare model mention waits for you to add a message. Mentioning a user opens
their direct conversation; inline mentions stay as message text. You can still
open a separate model chat explicitly from **Agents**.

### Useful commands

| Command | Action |
| --- | --- |
| `/help` | Show all commands; works before a model is connected |
| `/agents` | List models |
| `/users` | List device identities |
| `/status` | Show the current Head and specialist tasks |
| `/set @agent-name as Head` | Choose the default model and coordinator |
| `/set head auto` | Restore automatic default selection |
| `/set @agent-name as Coder` | Assign a specialist role and enable delegation |
| `/set @agent-name as Chat` | Clear that model's specialist role |

Other specialist roles are `Debugger`, `Researcher`, `Writer`, and `Reasoner`.
Workspace owners assign roles; invited members use those settings. Each
workspace saves its own choices.

Type `/` for command suggestions or `@` for agents and users. Accept a suggestion
with the arrow keys and Enter or Tab, then send the completed command. Use
Shift+Enter for a new line and `//` to send a literal leading slash. Command
results stay out of model prompts and shared conversation history.

### Optional specialist delegation

Assign a role with `/set`, or edit a model's purpose and permitted tasks and
enable **Allow automatic delegation to this model**. These are your routing
preferences, not a test of the model's abilities.

The Head can select a specialist or plan up to three ordered tasks, then combine
their results. It checks workspace access, online status, task permissions, and
image support before dispatch. Ordinary greetings use one model directly. If
planning fails or no specialist is available, an available default model answers
the original request. Failed worker requests are not automatically replayed.

Automatic routing uses models imported by the workspace owner or explicitly
chosen by the owner as coordinator. An invited model's published role alone does
not grant access to workspace chat context. Other invited models remain
available through direct chats. Replies show which model produced them.

Assigned models receive the relevant history and attachments. Purpose
descriptions are shared with the workspace; provider keys, API roots, and private
model instructions stay on the model's device. A request can make several
provider calls and has a four-minute overall limit. Models answer through APIs;
this does not give them filesystem access or execute returned code.

## Cookbook: find, download, and run local models

Open **Cookbook** from the top bar. It estimates which model builds fit your
device's RAM, GPU memory, and free storage. Search or filter the catalogue, then
choose a context length and quantization. Fit verdicts are estimates rather than
benchmarks. **All models** shows the full catalogue if nothing is recommended.

1. Install [Ollama](https://ollama.com/download) on this device.
2. Click **Refresh local models** to list installed and running models.
3. Choose a build and click **Download**. Progress appears under **Local models**.
   **Cancel download** stops the request; **Retry** reuses Ollama's partial files.
4. Click **Run** to load the model with the selected context. **Stop** unloads it
   without deleting its downloaded files.
5. Click **Use in chat**, then **Connect agent** to add it to this workspace.

Downloads continue while you switch pages or workspaces. Closing the app cancels
pending requests; downloaded files stay in Ollama's model store. Files are shared
across this device, while agent connections belong to their workspace.

The page opens from a bundled or saved catalogue. **Refresh from Hugging Face**
updates metadata; **Download** fetches model files through Ollama. Mirror settings
apply to metadata refreshes. Native download and Run controls support local
Ollama GGUF builds; connect other engines through **Providers**.

## Create and manage workspaces

Click **+ → Create a workspace**, enter a name, and switch workspaces using their
initials in the rail. Each has its own chats, models, members, and invitations.
Owned workspaces keep their relays and models running while you view another
workspace. Closing the app stops them.

**Workspace settings** shows members and online status. Owners can rename the
workspace, invite members, or **Remove** a member to revoke access and disconnect
it. Removing a local model also removes its configuration and saved credentials.

**Delete workspace** removes that owned workspace's local chats, configuration,
and credentials and disconnects its members. Deleting the last owned workspace
creates a fresh one. **Leave workspace** removes a joined workspace's local
history and saved invitation. If the host is reachable and manages membership,
leaving also revokes the invitation there; otherwise ask its owner to revoke it.

## Invite another device

On the host:

1. Open your owned workspace and click **Invite a device**.
2. Create a new identity for the other device.
3. For a trusted LAN, enable **Share this relay on my local network** and select
   the Wi-Fi or hotspot adapter under **Host network**.
4. Use an address the other device can reach and share the invitation privately.
   Each invitation contains a separate private token.
5. Keep the host app open and allow the displayed port through its firewall.

On the invited device:

1. Click **+ → Join a workspace**.
2. Paste the invitation, or enter the relay address and device token.
3. Join and wait for the connection. The app opens the workspace chat with its
   existing messages, attachments, and replies. The workspace is remembered for
   next time.

Normal workspace invitations share the same persistent chat. The host's **Chat**
entry opens that thread too, so everyone sees subsequent messages and AI replies.
Additional devices join the same history instead of creating separate chats.

Use a different invitation for each device. An identity already connected on one
device cannot be reused on another. One joined workspace is supported at a time;
switch to it and use **Leave workspace** before joining another.

LAN sharing stays enabled after you opt in. Create a fresh invitation if the
host's network address changes. Plain `ws://` LAN connections are unencrypted;
use a reachable `wss://` relay for internet or untrusted networks. See
[network setup](NETWORK_SETUP.md) for hosting with TLS. The app does not configure
cloud hosting, router forwarding, or firewall rules.

## Share an AI conversation

**Invite a device** shares the workspace's main chat and its existing history
automatically. To invite someone to a separate model conversation:

1. Open the model's chat and click **Invite to conversation**.
2. Create a new invitation for the other device.
3. On that device, paste it into **+ → Join a workspace**.

Both devices see the existing messages and subsequent replies. Either participant
can send the next message, and the model receives their combined history. Shared
chats are visible to their invited devices, including their attachments.

Shared history and membership persist on the host; guests keep a local copy for
offline reading. Invitations survive host restarts unless revoked. The host must
be running to send new requests and synchronize messages.

## Model internet access

Internet access is off by default. To enable it, edit a model and select
**Allow this model to search the web**, then:

1. Choose **Ollama web search** and enter a key from your
   [Ollama account](https://ollama.com/settings/keys). This works with any model
   provider and requires a separate search key, even for a local model.
2. Choose **Automatic** to let the model request searches, or **Always** to search
   before each answer. Automatic mode depends on the model's judgment.
3. Click **Test web search**, then save.

Alternatively, choose **SearXNG** and enter your search server address, separate
from the model's API root. Its `settings.yml` must enable JSON search:

```yaml
search:
  formats:
    - html
    - json
```

The app supplies up to five result snippets and source links, not full pages.
Queries go to the selected search service. Search keys and model keys are stored
separately in the OS credential store; leave a key blank when editing to keep it.

## Attach images and PDFs

Click **Attach images / PDFs** in a chat, select your files, and send a question
or the files alone. **Remove** discards a prepared attachment before sending.
Files are prepared locally; sending shares their content with the selected
provider and, in shared chats, invited members. Saved history retains attachments
for follow-ups.

Text PDFs work with any model. Images and scanned PDFs need a vision-capable
model with **image support** enabled. The checkbox does not add vision to a
text-only model.

| Limit | Value |
| --- | --- |
| Files per message | 8 |
| Size per source file | 20 MiB |
| Prepared request | 8 MiB, with about 190 KiB of text |
| PDF length | 200 pages |
| PDF page images | Included only for PDFs of 16 pages or fewer |
| Image preparation | Resized to fit 1600 pixels and converted to JPEG; first frame for GIFs |

Split large files. PDFs longer than 16 pages include extracted text only; scanned
PDFs must be split into at most 16 pages. Unlock password-protected files first.

## Saved data and recovery

The app restores your identity, selected workspace, chats, drafts, and models
marked **Start automatically**. Settings and chat archives live in:

| Platform | Preferences directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\MultiplayerAI` |
| Linux / macOS | `$XDG_CONFIG_HOME/multiplayer-ai`, defaulting to `~/.config/multiplayer-ai` |

Use `python -m desktop_app --data-dir PATH` to choose another directory. It
contains `settings.json`, `workspaces.json`, and `history.sqlite3`; additional
workspaces store separate data under `workspaces/<workspace-id>/`. Tokens and
provider keys stay in the OS credential store.

The complete chat archive is saved. Model context includes up to 100 recent
messages within about 190 KiB of text and the 8 MiB prepared-request limit. Older
messages remain readable. Interrupted requests are never automatically replayed.

If an identity's token is missing, the app replaces that identity and preserves
other preferences. Rejoin affected remote workspaces with fresh invitations.
**Settings → Reset local identity** deliberately replaces all local identities
and also requires fresh invitations for joined workspaces.

**Settings** includes theme and animation preferences. **Resources** shows local
CPU, memory, and disk usage.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| App cannot open the credential store | Unlock your OS keyring and relaunch; a secure credential store is required. |
| Local Ollama model is offline | Check that Ollama and the model are installed. Refresh local models or send directly to that model to retry startup. |
| Provider import fails | Check the highlighted field, API key, exact model ID, and API root. |
| Joining times out | Keep the host open; check its selected adapter, address, port, and firewall. |
| Identity is already connected | Create a separate invitation for this device. |
| App closes unexpectedly | Inspect `desktop-crash.log` in the preferences directory. |

For a LAN connection check on Windows, substitute the host's actual address and
port:

```powershell
Test-NetConnection -ComputerName 192.168.43.12 -Port 54321
```

`TcpTestSucceeded: False` means the listener is unreachable. Guest Wi-Fi, campus
networks, and some hotspots isolate devices even when they share a network name.
Use a network that permits peer traffic or a reachable WSS relay. Keep the
firewall enabled and allow the app on the appropriate network profile.
