"""Claude providers: the Anthropic API (default) and Claude on Amazon Bedrock.

Both use the same Messages API shape, so Bedrock is the Anthropic provider with a different client.

Claude 5.5 models reject non-default sampling parameters (temperature, top_p, top_k) and think by
default, with thinking tokens counted against max_tokens. So this provider never sends temperature,
sets an explicit effort level (ANTHROPIC_EFFORT, default "low": these are bounded extraction tasks),
and adds headroom to max_tokens for thinking. Only tokens actually generated are billed.
"""

from __future__ import annotations

from typing import Any

from podcast_agent.llm.base import LLMError, LLMProvider, LLMResponse

THINKING_HEADROOM = 4096


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str,
        fast_model: str | None,
        api_key: str | None = None,
        timeout_s: float = 90,
        *,
        client: Any = None,
        prompt_cache: bool = True,
        effort: str | None = "low",
        workspace_id: str | None = None,
    ):
        super().__init__(model, fast_model)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise LLMError("The 'anthropic' package is not installed") from exc
        self._anthropic = anthropic
        if client is None:
            if not api_key:
                raise LLMError("ANTHROPIC_API_KEY is not set")
            # The SDK retries 408/409/429/5xx with exponential backoff; we raise the ceiling a little.
            extra: dict[str, Any] = {}
            if workspace_id:
                # A key that isn't scoped to one workspace must name the workspace on every request.
                extra["default_headers"] = {"anthropic-workspace-id": workspace_id}
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=4, **extra)
        self._client = client
        self.prompt_cache = prompt_cache
        self.effort = effort

    def _complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float | None,  # not sent: Claude 5.5 models reject non-default sampling parameters
        cache_prefix: str | None,
        task: str,
        context: dict[str, Any],
    ) -> LLMResponse:
        system_blocks: list[dict[str, Any]] = []
        if cache_prefix:
            block: dict[str, Any] = {"type": "text", "text": cache_prefix}
            if self.prompt_cache:
                # Shared episode context first, marked cacheable: later steps on the same episode
                # read it from the prompt cache instead of paying full input price.
                block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(block)
        system_blocks.append({"type": "text", "text": system})
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens + THINKING_HEADROOM,
            "system": system_blocks,
            "messages": messages,
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            resp = self._client.messages.create(**kwargs)
        except self._anthropic.APIError as exc:
            raise LLMError(f"{self.name} API error: {exc}") from exc
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


class BedrockProvider(AnthropicProvider):
    """Claude on Amazon Bedrock. Credentials come from the AWS chain: env vars, ~/.aws profile, or an IAM role."""

    name = "bedrock"

    def __init__(
        self,
        model: str,
        fast_model: str | None,
        region: str,
        timeout_s: float = 90,
        *,
        client: Any = None,
        prompt_cache: bool = True,
        effort: str | None = "low",
    ):
        if client is None:
            try:
                import anthropic

                client = anthropic.AnthropicBedrock(aws_region=region, timeout=timeout_s, max_retries=4)
            except ImportError as exc:  # pragma: no cover
                raise LLMError("Bedrock needs boto3: pip install 'podcast-agent[aws]'") from exc
        super().__init__(model, fast_model, client=client, prompt_cache=prompt_cache, effort=effort)
