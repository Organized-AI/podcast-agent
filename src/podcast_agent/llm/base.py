"""Provider-agnostic LLM interface.

The agent never talks to a vendor SDK directly. Each step asks for a completion with a
`tier` ("main" for quality-critical work, "fast" for cheap extraction/planning), and an
optional `cache_prefix` — the shared episode context. Providers that support prompt
caching (Anthropic) mark that prefix as cacheable so summary, notes and claim extraction
reuse the same cached transcript tokens; other providers simply prepend it.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

Tier = Literal["main", "fast"]


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False  # served from the local response cache
    estimated: bool = False  # token counts are estimates, not provider-reported
    stop_reason: str | None = None


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str, fast_model: str | None = None):
        self.model = model
        self.fast_model = fast_model or model

    def model_for(self, tier: Tier) -> str:
        return self.fast_model if tier == "fast" else self.model

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        tier: Tier = "main",
        max_tokens: int = 1500,
        temperature: float | None = 0.2,
        cache_prefix: str | None = None,
        task: str = "",
        context: dict[str, Any] | None = None,
    ) -> LLMResponse:
        model = self.model_for(tier)
        start = time.perf_counter()
        resp = self._complete(
            system=system,
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_prefix=cache_prefix,
            task=task,
            context=context or {},
        )
        if not resp.latency_ms:
            resp.latency_ms = int((time.perf_counter() - start) * 1000)
        return resp

    @abstractmethod
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
    ) -> LLMResponse: ...

    def describe(self) -> dict[str, str]:
        return {"provider": self.name, "main": self.model, "fast": self.fast_model}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) used when a provider reports none."""
    return max(1, len(text) // 4)
