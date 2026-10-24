# Connect agents across laptops, desktops, and other devices

For the desktop invitation workflow, see [Invite another device](DESKTOP_GUIDE.md#invite-another-device).
This guide is for running a separate relay or command-line agents.

- [Install](#1-install-on-the-relay-and-every-device)
- [Create identities](#2-create-identities-on-the-relay)
- [Run on a LAN](#3-run-locally-or-on-a-trusted-lan)
- [Deploy with TLS](#4-deploy-for-internet-access)
- [Connect agents](#5-connect-agents-on-different-devices)
- [Reliability and capacity](#reliability-and-capacity)

This relay accepts many simultaneous device connections. Each device opens an
outbound WebSocket to one reachable server. Devices can be on different networks
behind NAT; only the relay needs to accept inbound traffic. Multiple agents on
one device work too: give each agent a separate identity and process.

`network_a2a/` provides a custom authenticated transport that can forward
JSON-RPC payloads to an existing local A2A server.
The relay itself is **not a standard A2A endpoint** and
does not advertise an Agent Card. Use its device client to reach local A2A servers.
Standard A2A discovery and streaming are not implemented by this relay.

## 1. Install on the relay and every device

Use Python 3.13 or newer. From this repository directory on Windows:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-network.txt
```

On Linux/macOS, use `.venv/bin/python` in place of `.venv\Scripts\python`.
Devices need Python and this repository, or a custom client implementing the
WebSocket frames documented below.

## 2. Create identities on the relay

```powershell
.venv\Scripts\python -m network_a2a.provision laptop_alice laptop_bob desktop_charlie
```

This creates `credentials.json` with three independent random tokens. The file
is excluded from Git. Keep the full file only on the relay. Privately provide
each device its own token. Add further agents with unique tokens of at least
32 characters. The file has this structure (placeholders are not usable tokens):

```json
{
  "laptop_alice": {"token": "ALICE_PRIVATE_TOKEN", "group": "team"},
  "laptop_bob": {"token": "BOB_PRIVATE_TOKEN", "group": "team"},
  "desktop_charlie": {"token": "CHARLIE_PRIVATE_TOKEN", "group": "team"}
}
```

Agents can see and invoke only identities with the same group. Possession of a
token authorizes that identity; the headless relay has no separate login or
invite UI. Group members can invoke each other's agents, so connect only handlers you intend
to share. Local credentials/accounts remain on their own device. Rotate or
revoke tokens by updating the file and restarting the relay; restart disconnects
all devices. Protect the file with OS permissions and never put tokens in URLs.

## 3. Run locally or on a trusted LAN

```powershell
$env:A2A_CREDENTIALS_FILE = "$PWD\credentials.json"
.venv\Scripts\python -m uvicorn network_a2a.server:app_from_env --factory --host 0.0.0.0 --port 8000 --workers 1 --ws-max-size 8454144 --ws-ping-interval 20 --ws-ping-timeout 20
```

For local testing, clients use `ws://127.0.0.1:8000/connect`. For a trusted LAN,
use the relay machine's LAN IP, allow inbound TCP 8000 in its firewall, and add
`--allow-insecure` to the client command. Plaintext LAN mode exposes traffic and
tokens to the network; use the TLS deployment below for internet connections.

## 4. Deploy for internet access

Use an always-on VPS or other public server with Docker and Compose. Copy the
repository and private `credentials.json` there. Point a DNS name such as
`agents.example.com` at the server's public IP. Allow inbound TCP ports 80 and
443 through the server firewall and hosting provider firewall. Ensure outbound
HTTPS works for certificate issuance. Do not publish port 8000 separately.

On that server (Linux shell):

```sh
export A2A_DOMAIN=agents.example.com
docker compose -f compose.network.yml up -d --build
docker compose -f compose.network.yml logs --tail=100
```

Caddy terminates TLS and forwards WebSocket traffic to the relay. Once DNS and
certificate issuance succeed, devices use `wss://agents.example.com/connect`.
The public health endpoint is `https://agents.example.com/health`.
For image and PDF attachments, use the current relay code and the WebSocket
size limit above. A separate reverse proxy must also accept request bodies of
at least 8 MiB. Older relays have a 256 KiB limit and accept only text in shared chats.

Use the native desktop app's **Join workspace** dialog to connect to this relay
with `wss://agents.example.com/connect` and your device token. The public server
is API-only; it does not serve a browser dashboard. See [the desktop guide](DESKTOP_GUIDE.md).

A home-hosted relay needs router forwarding and a reachable public IP; carrier
NAT may prevent that. A VPS avoids that dependency. Device networks must permit
outbound TLS/WebSocket connections to port 443. No implementation can guarantee
connectivity through a network that blocks that traffic.

## 5. Connect agents on different devices

On Bob's laptop, set Bob's token and run a persistent echo agent:

```powershell
$env:A2A_TOKEN = 'paste-bobs-private-token'
.venv\Scripts\python -m network_a2a --server wss://agents.example.com/connect
```

On Charlie's desktop, run the same command with Charlie's token. On Alice's
laptop, send a request to either identity using Alice's token:

```powershell
$env:A2A_TOKEN = 'paste-alices-private-token'
.venv\Scripts\python -m network_a2a --server wss://agents.example.com/connect --to laptop_bob --payload '{"text":"Hello Bob"}'
.venv\Scripts\python -m network_a2a --server wss://agents.example.com/connect --to desktop_charlie --payload '{"text":"Hello Charlie"}'
```

The token determines the device identity. Do not start two connections using
the same identity: the relay rejects duplicates. Stop a persistent process before
using its token for a separate CLI connection. HTTP requests below can reuse a
connected identity's token without opening another WebSocket.

To run an AI model instead of the echo handler, use `--provider` and `--model`.
See [provider adapters](PROVIDER_ADAPTERS.md) for Ollama, Bionic GPT, cloud APIs,
and compatible local model servers.

### Attach an existing local A2A agent

Start your existing local agent on Bob's machine, then run:

```powershell
.venv\Scripts\python -m network_a2a --server wss://agents.example.com/connect --local-a2a-url http://127.0.0.1:9999/
```

The client forwards incoming JSON bodies to this fixed local HTTP endpoint and
returns its JSON response. Send the JSON-RPC request appropriate to your agent's
A2A version; the relay preserves the inner JSON-RPC request and response IDs.
Set `A2A_LOCAL_TOKEN` if that endpoint needs Bearer authentication. The client
does not forward relay tokens or give peers the ability to select local URLs.
Use synchronous JSON responses; SSE and streaming are not supported here.

### Integrate a custom agent in Python

```python
import asyncio
import os
from network_a2a.client import AgentClient

async def respond(payload, sender):
    # Call your local model/agent here. Keep blocking work off the event loop.
    return {"text": f"Received from {sender}: {payload}"}

async def main():
    agent = AgentClient("wss://agents.example.com/connect", os.environ["A2A_TOKEN"], respond)
    runner = asyncio.create_task(agent.run())
    try:
        await agent.ready.wait()
        print(await agent.request("laptop_bob", {"text": "Collaborate on this task"}))
        await runner
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)

asyncio.run(main())
```

### Discover and invoke over HTTPS

```powershell
$headers = @{ Authorization = "Bearer $env:A2A_TOKEN" }
Invoke-RestMethod https://agents.example.com/agents -Headers $headers
Invoke-RestMethod https://agents.example.com/agents/laptop_bob/invoke -Method Post -Headers $headers -ContentType application/json -Body '{"text":"Hello via HTTPS"}'
```

## Reliability and capacity

The client uses heartbeat pings and automatic reconnect with backoff for network
failures. Unauthorized or duplicate identities stop instead of retrying forever.
Requests to offline agents fail immediately. A disconnected or timed-out request
is not replayed: its work may already have executed. Applications should provide
idempotency keys before retrying operations with side effects.

This implementation keeps connections and in-flight requests in memory. Run
**one worker and one relay replica**. There is no two-device limit; practical
capacity depends on server resources. Defaults: 256 pending requests globally,
32 concurrent requests per connection, 8 MiB + 64 KiB frames/bodies, and a 60-second
relay response timeout. Each sender has a burst of 30 and refill of 5 requests
per second. Set `A2A_MAX_PENDING` or `A2A_REQUEST_TIMEOUT` (1..300 seconds) on the
relay as needed. Increase client and local HTTP timeouts in code for longer work.
There is no durable offline queue, persistent task store, or multi-replica routing.
Use an external broker and shared storage before horizontally scaling.

Transport is encrypted from device to relay with WSS; the relay can read routed
payloads. There is no end-to-end encryption between agents.

## Verify

Install `requirements-dev.txt` in the same environment, then run the relay and
adapter tests:

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests/test_network.py tests/test_adapters.py
```

Tests start a real local relay and exercise three concurrent devices, HTTP
invocation, authorization, group isolation, forged responses, duplicate IDs,
invalid frames, timeouts, disconnects, automatic reconnection, the local JSON-RPC
bridge, and payload limits.

See the [development guide](DOCUMENTATION.md#run-tests) to run the full suite.
