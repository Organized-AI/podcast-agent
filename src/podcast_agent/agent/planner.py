"""Planning: profile the episode, ask the model for an execution plan, then validate/repair it.

The model chooses steps, order and parameters (strategy, claim budget, tool set, step budget)
from a catalog of capabilities. A deterministic validator enforces the guardrails — required
deliverables, dependency order, parameter ranges — and repairs the plan if the model gets it
wrong, so autonomy never costs us a missing deliverable.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from podcast_agent.llm.base import LLMError
from podcast_agent.llm.structured import StructuredOutputError, call_structured
from podcast_agent.models import Episode, Plan, PlanStep, format_seconds
from podcast_agent.prompts import PLANNER_SYSTEM, planner_prompt
from podcast_agent.transcript import count_fillers

if TYPE_CHECKING:
    from podcast_agent.agent.context import RunContext

CANONICAL_ORDER = ["normalize_transcript", "summarize", "extract_notes", "extract_claims", "verify_claims"]


def capability_catalog(ctx: RunContext) -> dict:
    tools = ["search_knowledge_base"] + (["web_search"] if ctx.search else [])
    return {
        "normalize_transcript": {
            "description": "Remove filler words, stutters and repeated words (raw text is kept for audit).",
            "required": False, "requires": [], "params": {},
        },
        "summarize": {
            "description": f"Write the {ctx.settings.summary_min_words}-{ctx.settings.summary_max_words} word episode summary.",
            "required": True, "requires": [], "params": {"strategy": ["single_pass", "map_reduce"]},
        },
        "extract_notes": {
            "description": "Top 5 takeaways, verbatim quotes with timestamps, tag-style topics.",
            "required": True, "requires": [], "params": {},
        },
        "extract_claims": {
            "description": "Find check-worthy factual statements and rewrite each as a standalone claim.",
            "required": True, "requires": [], "params": {"max_claims": "integer"},
        },
        "verify_claims": {
            "description": "Verify each claim in a tool-using reasoning loop and assign a verdict and confidence.",
            "required": True, "requires": ["extract_claims"],
            "params": {"max_steps_per_claim": "integer", "tools": tools},
        },
    }


def profile_episode(ep: Episode) -> dict:
    raw = " ".join(u.raw_text for u in ep.utterances)
    capitalized = re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z]+|[A-Z]{2,})\b", raw)
    speaker_tokens = {t for s in ep.speakers for t in re.findall(r"[A-Za-z]+", s)}
    orgs = sorted({c for c in capitalized if c not in speaker_tokens and c not in {"I", "So", "And", "But"}})
    return {
        "episode_id": ep.episode_id,
        "title": ep.title,
        "source_format": ep.source_format,
        "duration": format_seconds(ep.duration_seconds),
        "word_count": ep.word_count,
        "lines": len(ep.utterances),
        "speakers": ep.speakers,
        "sections": ep.sections,
        "filler_or_repeated_words": count_fillers(raw),
        "claim_signals": {
            "numbers": len(re.findall(r"\$?\d[\d,.]*%?", raw)),
            "years": len(re.findall(r"\b(?:19|20)\d{2}\b", raw)),
            "named_entities": orgs[:20],
            "prediction_cues": len(re.findall(r"(?i)\b(will|won't|going to|next big|anytime soon|by 20\d\d)\b", raw)),
        },
    }


def validate_plan(plan: Plan, catalog: dict) -> list[str]:
    problems = []
    names = [s.step for s in plan.steps]
    for name, spec in catalog.items():
        if spec["required"] and name not in names:
            problems.append(f"required capability '{name}' is missing")
    if len(set(names)) != len(names):
        problems.append("a capability appears more than once")
    for i, name in enumerate(names):
        for dep in catalog.get(name, {}).get("requires", []):
            if dep not in names[:i]:
                problems.append(f"'{name}' must come after '{dep}'")
    return problems


def repair_plan(plan: Plan, ctx: RunContext, ep: Episode, catalog: dict) -> Plan:
    notes: list[str] = []
    seen: dict[str, PlanStep] = {}
    for step in plan.steps:
        if step.step in seen:
            notes.append(f"dropped duplicate '{step.step}'")
            continue
        seen[step.step] = step.model_copy(deep=True)
    for name, spec in catalog.items():
        if spec["required"] and name not in seen:
            seen[name] = PlanStep(step=name, reason="added by validator: required deliverable")
            notes.append(f"added missing required step '{name}'")
    ordered = list(seen.values())  # keep the model's order unless it breaks a dependency
    if _has_dependency_violation(ordered, catalog):
        ordered.sort(key=lambda st: CANONICAL_ORDER.index(st.step))
        notes.append("re-ordered steps to satisfy dependencies")

    s = ctx.settings
    available_tools = catalog["verify_claims"]["params"]["tools"]
    for step in ordered:
        p = dict(step.params)
        if step.step == "summarize":
            default = "map_reduce" if ep.word_count > s.map_reduce_threshold_words else "single_pass"
            if p.get("strategy") not in ("single_pass", "map_reduce"):
                p["strategy"] = default
        elif step.step == "extract_claims":
            p["max_claims"] = _clamp(p.get("max_claims"), min(3, s.max_claims), s.max_claims, s.max_claims)
        elif step.step == "verify_claims":
            p["max_steps_per_claim"] = _clamp(p.get("max_steps_per_claim"), 2, 6, s.max_steps_per_claim)
            tools = [t for t in (p.get("tools") or available_tools) if t in available_tools]
            p["tools"] = tools or available_tools
        if p != step.params:
            changed = {k: v for k, v in p.items() if step.params.get(k) != v}
            if changed:
                notes.append(f"{step.step}: set {changed}")
        step.params = p
    return Plan(goal=plan.goal, steps=ordered, repaired=bool(notes) or plan.repaired, repair_notes=plan.repair_notes + notes)


def default_plan(ctx: RunContext, ep: Episode, catalog: dict) -> Plan:
    plan = Plan(
        goal="Produce show notes and a fact-check report (deterministic fallback plan).",
        steps=[PlanStep(step=n, reason="fallback plan") for n in CANONICAL_ORDER],
    )
    return repair_plan(plan, ctx, ep, catalog)


def make_plan(ctx: RunContext, ep: Episode) -> Plan:
    catalog = capability_catalog(ctx)
    profile = profile_episode(ep)
    s = ctx.settings
    signals = profile["claim_signals"]
    ctx.trace.event(
        "ingest", "plan",
        f"profile: {profile['word_count']} words · {profile['lines']} lines · {len(profile['speakers'])} speakers · "
        f"{profile['filler_or_repeated_words']} filler/repeat tokens · {len(signals['named_entities'])} named entities · "
        f"{signals['years']} years · {signals['prediction_cues']} prediction cues",
        profile=profile,
    )
    try:
        result = call_structured(
            ctx,
            step="plan",
            schema=Plan,
            system=PLANNER_SYSTEM,
            prompt=planner_prompt(profile, catalog, s.map_reduce_threshold_words, s.max_claims),
            tier="fast",
            max_tokens=900,
            validate=lambda p: validate_plan(p, catalog),
            task="plan",
            context={"profile": profile, "catalog": catalog, "settings": s},
        )
        plan = repair_plan(result.value, ctx, ep, catalog)
    except (StructuredOutputError, LLMError) as exc:
        ctx.trace.warn("plan", f"planner failed ({exc}); using deterministic fallback plan")
        plan = default_plan(ctx, ep, catalog)

    ctx.trace.event("plan", "plan", f"goal: {plan.goal}", plan=plan)
    for i, step in enumerate(plan.steps, 1):
        params = f" {step.params}" if step.params else ""
        ctx.trace.event("plan", "plan", f"{i}. {step.step}{params} — {step.reason}", indent=1)
    for note in plan.repair_notes:
        ctx.trace.event("self_correct", "plan", f"validator: {note}", indent=1)
    return plan


def _has_dependency_violation(steps: list[PlanStep], catalog: dict) -> bool:
    names = [st.step for st in steps]
    return any(
        dep not in names[:i] for i, name in enumerate(names) for dep in catalog.get(name, {}).get("requires", [])
    )


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return max(lo, min(hi, default))
