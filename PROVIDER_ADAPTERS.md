# Provider adapters

For app setup, follow [Connect a model](DESKTOP_GUIDE.md#connect-a-model).
The commands here run persistent model agents from a terminal. First complete
[network setup](NETWORK_SETUP.md) and obtain a separate relay token per agent.
Run these commands from the repository root using that Python environment.

- [Supported providers](#supported-providers)
- [Ollama](#ollama-on-a-laptop)
- [Bionic GPT](#bionic-gpt)
- [Cloud providers](#cloud-providers)
- [Custom servers](#custom-compatible-servers)
- [Send requests](#send-requests-from-another-device)
- [Settings and failures](#local-settings-and-failures)
- [Use from Python](#use-from-python)

Run a provider-backed agent on any device connected to the relay. The relay
routes messages; the device calls its configured model. API keys stay on that
device and are sent only to the configured provider. No provider SDK is needed:
the adapters use `httpx` from `requirements-network.txt`.

## Supported providers

| CLI provider | API | API key environment variable | Base URL environment variable |
| --- | --- | --- | --- |
| `ollama` | Native `/api/chat` | `OLLAMA_API_KEY` (optional locally) | `OLLAMA_BASE_URL` or `OLLAMA_HOST` |
| `bionic` | Bionic GPT chat completions | `BIONIC_API_KEY` | `BIONIC_BASE_URL` (required) |
| `openai` | OpenAI Responses | `OPENAI_API_KEY` | `OPENAI_BASE_URL` |
| `anthropic` | Claude Messages | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` |
| `gemini` | Gemini generateContent | `GEMINI_API_KEY` | `GEMINI_BASE_URL` |
| `groq` | Chat completions | `GROQ_API_KEY` | `GROQ_BASE_URL` |
| `deepseek` | Chat completions | `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` |
| `mistral` | Chat completions | `MISTRAL_API_KEY` | `MISTRAL_BASE_URL` |
| `openrouter` | Chat completions | `OPENROUTER_API_KEY` | `OPENROUTER_BASE_URL` |
| `openai-compatible` | Custom chat completions server | `A2A_PROVIDER_API_KEY` (optional) | `A2A_PROVIDER_BASE_URL` (required) |

Cloud presets already have their standard API roots configured. Ollama defaults
to `http://127.0.0.1:11434`. Bionic means **Bionic GPT**; supply the API root of
your deployment, including `/v1`. All models are selected explicitly with
`--model` or `A2A_MODEL`, so the code does not choose a potentially unavailable
model for your account.

Inspect available presets:

```powershell
python -m network_a2a --list-providers
```

Use your installed environment's Python, such as `.venv\Scripts\python` or
the activated `Multiplayer-AI` Conda environment, for the commands below.

## Ollama on a laptop

You can configure providers directly in the native desktop app. Choose
**Providers**, select a model, and click **Connect agent**. The app starts the
adapter automatically; no terminal command is needed. See [DESKTOP_GUIDE.md](DESKTOP_GUIDE.md).

Start Ollama and pull the model you want (for example `ollama pull llama3.2`).
Set this device's relay token, then start its agent:

```powershell
$env:A2A_TOKEN = 'this-devices-relay-token'
python -m network_a2a --server wss://agents.example.com/connect --provider ollama --model llama3.2
```

The laptop keeps the process running to handle requests. For an Ollama instance
on a trusted LAN, set `OLLAMA_BASE_URL` to its HTTP root and explicitly add
`--allow-insecure-provider`. For Ollama Cloud, set the documented HTTPS API root
in `OLLAMA_BASE_URL` and its key in `OLLAMA_API_KEY`.

## Bionic GPT

Obtain an API key and a model ID from your Bionic GPT deployment:

```powershell
$env:A2A_TOKEN = 'this-devices-relay-token'
$env:BIONIC_API_KEY = 'your-bionic-api-key'
$env:BIONIC_BASE_URL = 'https://your-bionic.example.com/v1'
$env:A2A_MODEL = 'your-bionic-model-id'
python -m network_a2a --server wss://agents.example.com/connect --provider bionic
```

Use `http://localhost/v1` for a local deployment. API roots are operator
configuration; peers cannot supply URLs or API keys in requests.

## Cloud providers

For OpenAI, set `OPENAI_API_KEY` and choose a Responses-capable model available
to your account:

```powershell
$env:A2A_TOKEN = 'this-devices-relay-token'
$env:OPENAI_API_KEY = 'your-openai-api-key'
$env:A2A_MODEL = 'your-openai-model-id'
python -m network_a2a --server wss://agents.example.com/connect --provider openai
```

For Claude or Gemini, use `--provider anthropic` or `--provider gemini`, set the
corresponding key from the table, and select that provider's model ID. The same
pattern works for Groq, DeepSeek, Mistral, and OpenRouter.

## Custom compatible servers

For a local chat completions server (for example one you run with LM Studio or
vLLM), configure the API root including its version path:

```powershell
$env:A2A_PROVIDER_BASE_URL = 'http://localhost:1234/v1'
$env:A2A_MODEL = 'your-loaded-model-id'
python -m network_a2a --server wss://agents.example.com/connect --provider openai-compatible
```

Set `A2A_PROVIDER_API_KEY` if that server needs authentication. Compatible means
it accepts `model`, `messages`, `stream: false`, and `max_tokens`, and returns
`choices[0].message.content`. Provider-specific model features can require an
additional adapter.

## Send requests from another device

Use a different relay identity's token on the sending device:

```powershell
$env:A2A_TOKEN = 'sending-devices-relay-token'
python -m network_a2a --server wss://agents.example.com/connect --to laptop_bob --payload '{"text":"Explain how agents communicate over a network."}'
```

The result has a consistent shape:

```json
{
  "text": "The model's response",
  "provider": "ollama",
  "model": "llama3.2",
  "usage": {"input_tokens": 25, "output_tokens": 60},
  "finish_reason": "stop"
}
```

Usage fields and finish reasons follow the provider's naming; Ollama token
counts are mapped to `input_tokens` and `output_tokens`. A truncated response
can still contain text; inspect `finish_reason` before treating it as complete.

Accepted inputs are a JSON string, `{"text":"..."}`, or explicit conversation
history:

```json
{
  "messages": [
    {"role": "user", "content": "My project uses Python."},
    {"role": "assistant", "content": "What would you like to build?"},
    {"role": "user", "content": "Suggest a layout for an agent adapter."}
  ]
}
```

History must start and end with a user message. Assistant content is text; user
content can also be a list of text, document, and image parts. The desktop app
prepares PDF text and base64 images and maps them to each provider's native API.
Set `vision=True` in `ProviderConfig` for a vision-capable model. There is no shared
memory in the adapter: include history in each
request, which prevents different peers' conversations from being mixed.

Desktop model settings expose image support, Ollama web search, and SearXNG.
Python `ProviderConfig` accepts `web_search="auto"` or `"always"`; the default is
`"off"`. Set `search_provider="ollama"` with `search_api_key`, or use the default
`search_provider="searxng"` with `searxng_url`. Automatic mode permits the model
to request a search, then generates an answer from bounded result snippets with source URLs. This is a
model decision, not a comparison with its training dataset. See the setup and
attachment limits in [DESKTOP_GUIDE.md](DESKTOP_GUIDE.md).

## Local settings and failures

Optional settings: `--system-prompt` (or `A2A_SYSTEM_PROMPT`), `--max-tokens`
(default 1024), `--provider-concurrency` (default 4), `--provider-timeout`
(default 55 seconds), `--provider-base-url`, and `--api-key-env`. Provider URLs,
models, keys, system instructions, and token budgets are controlled locally.
Remote requests cannot override these settings.

The provider timeout includes queueing. Keep it below the relay timeout
(default 60 seconds) and the AgentClient timeout (default 75 seconds). For
longer generations, update all three together using the Python client and relay
configuration. Local models may need more time for an initial model load.

Errors travel through the relay as safe categories: `authentication`,
`rate_limit`, `http_error`, `timeout`, `connection`, `invalid_input`,
`invalid_response`, `no_text`, or `web_search`. Provider bodies, keys and URLs are excluded
from error messages. The adapters do not retry generation requests automatically,
because a failed or timed-out call might already have generated billable output.

These adapters produce non-streaming text and optionally use a configured search
service. They do not invoke shell commands or implement provider-specific agent workflows. Use a custom
handler or the existing local A2A bridge for those workflows. `--provider` and
`--local-a2a-url` are mutually exclusive.

## Use from Python

```python
import asyncio
import os
import httpx
from network_a2a.adapters import create_adapter, load_config
from network_a2a.client import AgentClient

async def main():
    config = load_config("ollama", model="llama3.2")
    async with httpx.AsyncClient() as http:
        adapter = create_adapter(config, http)
        agent = AgentClient("wss://agents.example.com/connect", os.environ["A2A_TOKEN"], adapter)
        await agent.run()

asyncio.run(main())
```

Add a provider by implementing `HTTPAdapter.generate(messages, instructions=None)` and registering
a `ProviderSpec` in `network_a2a/adapters/__init__.py`. The base adapter handles
input validation, concurrency, timeouts, bounded HTTP responses, and result
formatting.

## Validation and API references

Install `requirements-dev.txt`, then run
`python -m pytest tests/test_adapters.py tests/test_agent_integration.py`.
Adapter tests mock provider HTTP
responses; integration tests run a real relay and a subprocess provider agent
against a local mock Ollama endpoint. No paid cloud requests are made by tests.

The wire formats follow the official documentation:
[Ollama](https://docs.ollama.com/api/chat),
[Bionic GPT](https://bionic-gpt.com/docs/guides/api/),
[OpenAI Responses](https://developers.openai.com/api/reference/typescript/resources/beta/subresources/responses/methods/create),
[Claude Messages](https://platform.claude.com/docs/en/api/messages/create),
[Gemini](https://ai.google.dev/api/generate-content),
[Groq](https://console.groq.com/docs/openai),
[DeepSeek](https://api-docs.deepseek.com/api/create-chat-completion/),
[Mistral](https://docs.mistral.ai/api), and
[OpenRouter](https://openrouter.ai/docs/quickstart).
