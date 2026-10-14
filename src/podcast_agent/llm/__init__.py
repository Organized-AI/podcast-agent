"""LLM provider factory."""

from __future__ import annotations

from podcast_agent.config import Settings
from podcast_agent.llm.base import LLMError, LLMProvider


def build_provider(settings: Settings) -> LLMProvider:
    name = settings.resolved_provider()
    provider: LLMProvider
    if name == "anthropic":
        from podcast_agent.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            settings.anthropic_model, settings.anthropic_fast_model, settings.anthropic_api_key, settings.llm_timeout_s,
            prompt_cache=settings.prompt_cache,
        )
    elif name == "bedrock":
        from podcast_agent.llm.anthropic_provider import BedrockProvider

        if not settings.bedrock_model:
            raise LLMError("Set BEDROCK_MODEL to a Claude model or inference-profile ID from your Bedrock console")
        provider = BedrockProvider(
            settings.bedrock_model, settings.bedrock_fast_model, settings.aws_region, settings.llm_timeout_s,
            prompt_cache=settings.prompt_cache,
        )
    elif name == "openai":
        from podcast_agent.llm.openai_provider import OpenAICompatibleProvider

        if not settings.openai_model:
            raise LLMError("Set OPENAI_MODEL when LLM_PROVIDER=openai")
        provider = OpenAICompatibleProvider(
            name="openai", base_url=settings.openai_base_url, model=settings.openai_model,
            fast_model=settings.openai_fast_model, api_key=settings.openai_api_key,
            timeout_s=settings.llm_timeout_s, max_tokens_param=settings.openai_max_tokens_param,
        )
    elif name == "ollama":
        from podcast_agent.llm.openai_provider import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            name="ollama", base_url=settings.ollama_base_url, model=settings.ollama_model,
            api_key="ollama", timeout_s=max(settings.llm_timeout_s, 300),
        )
    elif name == "offline":
        from podcast_agent.llm.offline import OfflineProvider

        return OfflineProvider()  # deterministic; nothing to cache
    else:
        raise LLMError(f"Unknown LLM_PROVIDER {name!r} (use auto|anthropic|bedrock|openai|ollama|offline)")

    if settings.cache_enabled:
        from podcast_agent.llm.cache import CachedProvider

        provider = CachedProvider(provider, settings.cache_dir)
    return provider
