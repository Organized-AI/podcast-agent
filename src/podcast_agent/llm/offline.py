"""Offline provider: answers each task with the deterministic heuristics in `heuristics.py`.

It returns JSON text exactly like a real model would, so the same parsing, validation,
self-correction and tool-use code paths run end to end. Token counts are estimates of the
prompts that *would* have been sent, which makes cost projections realistic.
"""

from __future__ import annotations

import json
from typing import Any

from podcast_agent import heuristics
from podcast_agent.llm.base import LLMError, LLMProvider, LLMResponse, estimate_tokens


class OfflineProvider(LLMProvider):
    name = "offline"

    def __init__(self):
        super().__init__("offline-heuristic", "offline-heuristic")

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
        handler = heuristics.HANDLERS.get(task)
        if handler is None:
            raise LLMError(f"offline provider has no heuristic for task {task!r}")
        text = json.dumps(handler(context), ensure_ascii=False)
        prompt = (cache_prefix or "") + system + "".join(m["content"] for m in messages)
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(text),
            estimated=True,
        )
