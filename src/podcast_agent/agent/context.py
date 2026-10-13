"""Per-run dependencies handed to every agent step."""

from __future__ import annotations

from dataclasses import dataclass, field

from podcast_agent.config import Settings
from podcast_agent.llm.base import LLMProvider, LLMResponse
from podcast_agent.models import Usage
from podcast_agent.tools.knowledge_base import KnowledgeBase
from podcast_agent.tools.web_search import SearchTool
from podcast_agent.trace import Tracer


@dataclass
class RunContext:
    settings: Settings
    llm: LLMProvider
    trace: Tracer
    kb: KnowledgeBase
    search: SearchTool | None
    usage: Usage = field(default_factory=Usage)

    def record_usage(self, resp: LLMResponse) -> None:
        u = self.usage
        u.llm_calls += 1
        if resp.cached:
            u.cached_calls += 1
            return
        u.input_tokens += resp.input_tokens
        u.output_tokens += resp.output_tokens
        u.cache_read_tokens += resp.cache_read_tokens
        u.estimated = u.estimated or resp.estimated

    def reset_usage(self) -> Usage:
        previous, self.usage = self.usage, Usage()
        return previous
