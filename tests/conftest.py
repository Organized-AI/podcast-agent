from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from podcast_agent.agent.context import RunContext
from podcast_agent.config import Settings
from podcast_agent.llm.base import LLMProvider, LLMResponse
from podcast_agent.llm.offline import OfflineProvider
from podcast_agent.tools.knowledge_base import KnowledgeBase
from podcast_agent.tools.web_search import MockWebSearch
from podcast_agent.trace import Tracer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


class ScriptedProvider(LLMProvider):
    """Returns queued responses per task; falls back to the offline heuristics when the queue is empty."""

    name = "scripted"

    def __init__(self, script: dict[str, list[Any]] | None = None):
        super().__init__("scripted", "scripted")
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[dict[str, Any]] = []
        self._fallback = OfflineProvider()

    def _complete(self, *, system, messages, model, max_tokens, temperature, cache_prefix, task, context):
        self.calls.append({"task": task, "messages": [dict(m) for m in messages], "context": context})
        queue = self.script.get(task)
        if queue:
            item = queue.pop(0)
            text = item if isinstance(item, str) else json.dumps(item)
            return LLMResponse(text=text, model=model, input_tokens=10, output_tokens=10)
        return self._fallback._complete(
            system=system, messages=messages, model=model, max_tokens=max_tokens, temperature=temperature,
            cache_prefix=cache_prefix, task=task, context=context,
        )


def make_ctx(provider: LLMProvider | None = None, **overrides) -> RunContext:
    settings = Settings(
        provider="offline",
        kb_path=str(DATA / "knowledge_base.json"),
        mock_web_path=str(DATA / "mock_web_index.json"),
        today="2026-10-13",
        cache_enabled=False,
    )
    for k, v in overrides.items():
        setattr(settings, k, v)
    return RunContext(
        settings=settings,
        llm=provider or OfflineProvider(),
        trace=Tracer("test", console=Console(quiet=True)),
        kb=KnowledgeBase.load(settings.kb_path),
        search=MockWebSearch(settings.mock_web_path),
    )


@pytest.fixture
def data_dir() -> Path:
    return DATA
