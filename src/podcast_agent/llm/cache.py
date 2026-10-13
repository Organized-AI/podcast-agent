"""Content-addressed response cache.

Keyed on (provider, model, prompts, sampling params). Re-running the same episode with the
same prompts costs nothing and returns identical output, which keeps demos and regression
tests reproducible. Delete the cache directory (or set LLM_CACHE=false) to force fresh calls.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from podcast_agent.llm.base import LLMProvider, LLMResponse


class CachedProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, cache_dir: str | Path):
        super().__init__(inner.model, inner.fast_model)
        self.inner = inner
        self.name = inner.name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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
        key_material = json.dumps(
            {
                "provider": self.inner.name,
                "model": model,
                "system": system,
                "prefix": cache_prefix,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            sort_keys=True,
        )
        key = hashlib.sha256(key_material.encode()).hexdigest()
        path = self.cache_dir / f"{key[:2]}" / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text())
            resp = LLMResponse(**data)
            resp.cached = True
            resp.latency_ms = 0
            return resp
        resp = self.inner._complete(
            system=system,
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_prefix=cache_prefix,
            task=task,
            context=context,
        )
        if resp.stop_reason != "max_tokens":  # never cache a truncated answer
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(resp)))
        return resp
