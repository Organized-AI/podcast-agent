"""What each model call costs, and the per-episode budget that watches the total.

Prices are list prices in USD per million tokens, checked on 2026-10-13 (docs/DEPLOYMENT.md, section 4). A model with no
price of its own (the offline heuristics, a local model, a Claude model added after this table) is priced as the
configured Claude model of the same tier, and the call records which model it was priced as. An offline run, the canary
and the cost drill therefore show what the same work would cost on Claude while spending nothing.

The budget is per episode (``COST_BUDGET_USD``). In ``warn`` mode an episode that goes over it finishes normally and is
recorded: a warning in the report, a ``podcast_agent.budget_exceeded`` event on the episode's span and one count on
``podcast_agent.budget.violations``, which the PodcastAgentCostBudgetExceeded alert watches. In ``enforce`` mode the
claims left once the budget is spent are not verified: each is marked unverifiable and flagged for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MODES = ("warn", "enforce")


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float

    def cost(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
        return (input_tokens * self.input + output_tokens * self.output + cache_read_tokens * self.cache_read) / 1e6


# Cache reads cost 0.05x the input price.
PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.005),
}
TIER_DEFAULTS = {"main": "claude-sonnet-5-5", "fast": "claude-haiku-5-5"}
_DATED = re.compile(r"-\d{8}$")  # claude-…-20261001 snapshots share their alias's price


def priced_as(model: str, tier: str, tier_model: str | None = None) -> str:
    """The model whose price applies: the model itself, else the tier's configured Claude model, else the tier default."""
    for candidate in (model, _DATED.sub("", model), tier_model or "", TIER_DEFAULTS.get(tier, TIER_DEFAULTS["main"])):
        if candidate in PRICES:
            return candidate
    return TIER_DEFAULTS["main"]


def call_cost(model: str, tier: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
              tier_model: str | None = None) -> tuple[float, str]:
    """(USD, the model it was priced as) for one model call."""
    name = priced_as(model, tier, tier_model)
    return PRICES[name].cost(input_tokens, output_tokens, cache_read_tokens), name


def usd(value: float) -> str:
    return f"${value:.4f}" if value < 0.01 else f"${value:.2f}"
