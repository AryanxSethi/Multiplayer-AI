"""Provider registry. Adapters are async AgentClient handlers."""
import os
from dataclasses import dataclass

from .anthropic import AnthropicAdapter
from .base import ProviderConfig, ProviderError
from .gemini import GeminiAdapter
from .ollama import OllamaAdapter
from .openai import OpenAIAdapter
from .openai_compatible import OpenAICompatibleAdapter


@dataclass(frozen=True)
class ProviderSpec:
    adapter: type
    base_url: str | None
    key_env: str
    base_env: str
    key_required: bool = True


PROVIDERS = {
    "ollama": ProviderSpec(OllamaAdapter, "http://127.0.0.1:11434", "OLLAMA_API_KEY", "OLLAMA_BASE_URL", False),
    "bionic": ProviderSpec(OpenAICompatibleAdapter, None, "BIONIC_API_KEY", "BIONIC_BASE_URL"),
    "openai": ProviderSpec(OpenAIAdapter, "https://api.openai.com/v1", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    "anthropic": ProviderSpec(AnthropicAdapter, "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
    "gemini": ProviderSpec(GeminiAdapter, "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY", "GEMINI_BASE_URL"),
    "groq": ProviderSpec(OpenAICompatibleAdapter, "https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_BASE_URL"),
    "deepseek": ProviderSpec(OpenAICompatibleAdapter, "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    "mistral": ProviderSpec(OpenAICompatibleAdapter, "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "MISTRAL_BASE_URL"),
    "openrouter": ProviderSpec(OpenAICompatibleAdapter, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"),
    "openai-compatible": ProviderSpec(OpenAICompatibleAdapter, None, "A2A_PROVIDER_API_KEY", "A2A_PROVIDER_BASE_URL", False),
}


def load_config(provider, model=None, base_url=None, api_key_env=None, system_prompt=None,
                max_tokens=1024, timeout=55, concurrency=4, allow_insecure=False, environ=None,
                vision=False, web_search="off", searxng_url="", searxng_allow_insecure=False,
                search_provider="searxng", search_api_key_env="OLLAMA_SEARCH_API_KEY"):
    """Load provider and search credentials from the environment into validated settings."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    spec = PROVIDERS[provider]
    key_env = api_key_env or spec.key_env
    key = env.get(key_env) or None
    if spec.key_required and not key:
        raise ValueError(f"Set {key_env} for {provider}")
    base_url = base_url or env.get(spec.base_env) or (env.get("OLLAMA_HOST") if provider == "ollama" else None) or spec.base_url
    if not base_url:
        raise ValueError(f"Set {spec.base_env} or --provider-base-url")
    # The flags and the variable are named here rather than in ProviderConfig,
    # which the desktop app also builds. This is the only caller that knows a
    # command line exists, so this is the only place that can say one.
    try:
        return ProviderConfig(provider=provider, model=model or env.get("A2A_MODEL"), base_url=base_url,
                              api_key=key, system_prompt=system_prompt if system_prompt is not None else env.get("A2A_SYSTEM_PROMPT"),
                              max_tokens=max_tokens, timeout=timeout, concurrency=concurrency, allow_insecure=allow_insecure,
                              vision=vision, web_search=web_search, searxng_url=searxng_url, searxng_allow_insecure=searxng_allow_insecure,
                              search_provider=search_provider, search_api_key=env.get(search_api_key_env) or None)
    except ValueError as error:
        if str(error) == "Choose a model":
            raise ValueError("Choose a model with --model or A2A_MODEL") from None
        raise


def create_adapter(config, http):
    """Validate provider availability and credentials before constructing its HTTP adapter."""
    if config.provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {config.provider}")
    spec = PROVIDERS[config.provider]
    if spec.key_required and not config.api_key:
        raise ValueError(f"{config.provider} requires an API key")
    return spec.adapter(config, http)


__all__ = ["PROVIDERS", "ProviderConfig", "ProviderError", "load_config", "create_adapter"]
