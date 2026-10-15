"""Rubric judge for summaries (live suite only).

A model grades each summary against its transcript on weighted criteria, then claims in the summary
that the transcript does not support are penalised. The judge is a second opinion next to the
deterministic checks (length, theme coverage, verbatim quotes), not a replacement for them.

    score = Σ weight·(rating − 1)/4  −  0.15 · invented facts, clipped to [0, 1]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from podcast_agent.llm.structured import call_structured

if TYPE_CHECKING:
    from podcast_agent.agent.context import RunContext
    from podcast_agent.models import Episode

JUDGE_VERSION = "2026-10-15.1"
CRITERIA: dict[str, tuple[float, str]] = {
    "faithfulness": (0.5, "Every statement in the summary is supported by the transcript; opinions are attributed to the speaker who held them."),
    "coverage": (0.3, "The core themes, key discussions and the main opinions of the episode are all present."),
    "concision": (0.2, "No padding, repetition or generic filler; every sentence carries information."),
}
INVENTED_FACT_PENALTY = 0.15


class CriterionRating(BaseModel):
    rating: int = Field(ge=1, le=5)
    note: str = Field(description="one short sentence for the report")


class JudgeVerdict(BaseModel):
    faithfulness: CriterionRating
    coverage: CriterionRating
    concision: CriterionRating
    invented_facts: list[str] = Field(default_factory=list, description="summary statements the transcript does not support")


def judge_system() -> str:
    rubric = "\n".join(f"- {name} (weight {w}): {desc}" for name, (w, desc) in CRITERIA.items())
    return (
        "You grade podcast summaries for an editor. Rate each criterion from 1 (poor) to 5 (excellent) using "
        "only the transcript as ground truth.\n" + rubric + "\n"
        "List in invented_facts every statement in the summary that the transcript does not support "
        "(empty list if none).\n"
        'Respond with JSON only: {"faithfulness": {"rating": 1-5, "note": "..."}, "coverage": {...}, '
        '"concision": {...}, "invented_facts": ["..."]}'
    )


def score(verdict: JudgeVerdict) -> float:
    total = sum(w * (getattr(verdict, name).rating - 1) / 4 for name, (w, _) in CRITERIA.items())
    return round(max(0.0, min(1.0, total - INVENTED_FACT_PENALTY * len(verdict.invented_facts))), 4)


def judge_summary(ctx: RunContext, episode: Episode, summary: str) -> tuple[JudgeVerdict, float]:
    transcript = "\n".join(f"[{u.timestamp}] {u.speaker_name}: {u.text}" for u in episode.utterances)
    prompt = f"Transcript of {episode.title!r}:\n{transcript}\n\nSummary to grade:\n{summary}"
    result = call_structured(ctx, step=f"judge {episode.episode_id}", schema=JudgeVerdict, system=judge_system(),
                             prompt=prompt, tier="main", max_tokens=900, task="judge_summary")
    return result.value, score(result.value)
