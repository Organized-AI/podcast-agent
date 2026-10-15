"""Per-run dependencies handed to every agent step."""

from __future__ import annotations

from dataclasses import dataclass, field

from podcast_agent import cost, telemetry
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

    def record_usage(self, resp: LLMResponse, step: str = "", tier: str = "main") -> None:
        u = self.usage
        u.llm_calls += 1
        if resp.cached:
            u.cached_calls += 1
            return
        u.input_tokens += resp.input_tokens
        u.output_tokens += resp.output_tokens
        u.cache_read_tokens += resp.cache_read_tokens
        u.estimated = u.estimated or resp.estimated
        s = self.settings
        usd, priced_as = cost.call_cost(resp.model, tier, resp.input_tokens, resp.output_tokens, resp.cache_read_tokens,
                                        s.anthropic_fast_model if tier == "fast" else s.anthropic_model)
        u.cost_usd = round(u.cost_usd + usd, 6)
        if step:
            u.cost_by_step[step] = round(u.cost_by_step.get(step, 0.0) + usd, 6)
        telemetry.record_cost(usd, model=resp.model, priced_as=priced_as, step=step, tier=tier)

    def budget_spent(self) -> bool:
        """Enforce mode only: the episode has used its whole budget, so skip work that would cost more."""
        budget = self.settings.cost_budget_usd
        return self.settings.cost_budget_mode == "enforce" and budget is not None and self.usage.cost_usd >= budget

    def reset_usage(self) -> Usage:
        previous, self.usage = self.usage, Usage()
        return previous
