"""Executor: run the plan step by step, isolate failures, assemble the report.

A failing step is recorded and its dependents are skipped, but independent steps still run —
a search outage should cost you the fact-check table, not the summary.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from podcast_agent.models import (
    Claim,
    Episode,
    EpisodeMeta,
    EpisodeReport,
    FactCheck,
    Notes,
    Plan,
    StepResult,
    Summary,
    format_seconds,
)

if TYPE_CHECKING:
    from podcast_agent.agent.context import RunContext


@dataclass
class EpisodeState:
    episode: Episode
    plan: Plan | None = None
    summary: Summary | None = None
    notes: Notes | None = None
    claims: list[Claim] | None = None
    fact_checks: list[FactCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# A step returns (human-readable detail, number of LLM attempts used).
StepFn = Callable[["RunContext", EpisodeState, dict[str, Any]], tuple[str, int]]
REGISTRY: dict[str, StepFn] = {}


def register(name: str) -> Callable[[StepFn], StepFn]:
    def deco(fn: StepFn) -> StepFn:
        REGISTRY[name] = fn
        return fn

    return deco


def process_episode(ctx: RunContext, episode: Episode, run_id: str) -> EpisodeReport:
    # Importing the step modules registers them.
    from podcast_agent.agent import steps, verifier  # noqa: F401
    from podcast_agent.agent.planner import capability_catalog, make_plan

    ctx.trace.episode_id = episode.episode_id
    ctx.reset_usage()
    ctx.trace.rule(f"{episode.episode_id} · {episode.title}")
    ctx.trace.event(
        "ingest", "ingest",
        f"{episode.source_format.upper()} · {len(episode.utterances)} lines · {episode.word_count} words · "
        f"~{format_seconds(episode.duration_seconds)} · speakers: {', '.join(episode.speakers)}",
    )

    state = EpisodeState(episode=episode)
    results: list[StepResult] = []
    started = time.perf_counter()

    t0 = time.perf_counter()
    state.plan = make_plan(ctx, episode)
    results.append(StepResult(step="plan", status="ok", duration_ms=_ms(t0), detail=f"{len(state.plan.steps)} steps"))

    catalog = capability_catalog(ctx)
    done: set[str] = set()
    for planned in state.plan.steps:
        name = planned.step
        missing = [d for d in catalog.get(name, {}).get("requires", []) if d not in done]
        if missing:
            msg = f"skipped — dependency failed: {', '.join(missing)}"
            ctx.trace.warn(name, msg)
            results.append(StepResult(step=name, status="skipped", detail=msg))
            continue
        fn = REGISTRY.get(name)
        if fn is None:
            ctx.trace.warn(name, "skipped — not implemented")
            results.append(StepResult(step=name, status="skipped", detail="not implemented"))
            continue
        ctx.trace.event("step", name, f"start — {planned.reason}" if planned.reason else "start")
        t0 = time.perf_counter()
        try:
            detail, attempts = fn(ctx, state, planned.params)
            done.add(name)
            results.append(StepResult(step=name, status="ok", duration_ms=_ms(t0), attempts=attempts, detail=detail))
            ctx.trace.event("result", name, f"done in {_ms(t0) / 1000:.1f}s — {detail}")
        except Exception as exc:  # isolate the failure, keep going
            ctx.trace.error(name, f"failed: {exc}", traceback=traceback.format_exc(limit=4))
            state.warnings.append(f"{name} failed: {exc}")
            results.append(StepResult(step=name, status="failed", duration_ms=_ms(t0), detail=str(exc)))

    ctx.trace.event(
        "result", "done",
        f"{episode.episode_id} finished in {(time.perf_counter() - started):.1f}s · "
        f"{ctx.usage.llm_calls} LLM calls ({ctx.usage.cached_calls} cached) · "
        f"{'~' if ctx.usage.estimated else ''}{ctx.usage.input_tokens:,} in / {ctx.usage.output_tokens:,} out tokens",
    )
    return EpisodeReport(
        run_id=run_id,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        episode=EpisodeMeta(
            episode_id=episode.episode_id,
            title=episode.title,
            host=episode.host,
            guests=episode.guests,
            duration=format_seconds(episode.duration_seconds),
            word_count=episode.word_count,
            utterances=len(episode.utterances),
            source=episode.source_path,
            cleaning=state.episode.cleaning,
        ),
        provider=ctx.llm.name,
        models=ctx.llm.describe(),
        plan=state.plan,
        summary=state.summary,
        notes=state.notes,
        fact_checks=state.fact_checks,
        steps=results,
        usage=ctx.usage.model_copy(),
        warnings=state.warnings,
    )


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
