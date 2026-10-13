"""Anthropic Messages API provider (default)."""

from __future__ import annotations

from typing import Any

from podcast_agent.llm.base import LLMError, LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, fast_model: str | None, api_key: str | None, timeout_s: float = 90):
        super().__init__(model, fast_model)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise LLMError("The 'anthropic' package is not installed") from exc
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        # The SDK retries 408/409/429/5xx with exponential backoff; we raise the ceiling a little.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=4)
        self._anthropic = anthropic

    def _complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float | None,
        cache_prefix: str | None,
        task: str,
        context: dict[str, Any],
    ) -> LLMResponse:
        system_blocks: list[dict[str, Any]] = []
        if cache_prefix:
            # Shared episode context first, marked cacheable: later steps on the same episode
            # read it from Anthropic's prompt cache instead of paying full input price.
            system_blocks.append({"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}})
        system_blocks.append({"type": "text", "text": system})
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            resp = self._client.messages.create(**kwargs)
        except self._anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc
        text = "".join(getattr(block, "text", "") for block in resp.content if block.type == "text")
        usage = resp.usage
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=(usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            stop_reason=resp.stop_reason,
        )
