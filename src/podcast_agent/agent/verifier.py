"""Fact-checking agent: a ReAct-style tool loop per claim.

For each claim the model alternates action (search_knowledge_base | web_search, with a one-line
purpose for the log) → observation until it calls `finish` with a verdict. The loop is bounded (tool budget per claim),
tool failures are retried and then surfaced to the model as observations, the final answer is
validated (evidence ids must exist), and confidence is calibrated against the evidence actually
gathered rather than taken on faith.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from podcast_agent.agent.pipeline import EpisodeState, register
from podcast_agent.llm.base import LLMRefusal
from podcast_agent.llm.structured import extract_json
from podcast_agent.models import Claim, Evidence, FactCheck, Verdict
from podcast_agent.prompts import verify_opening, verify_system
from podcast_agent.tools.web_search import ToolError, with_retries

if TYPE_CHECKING:
    from podcast_agent.agent.context import RunContext

TOOLS = ("search_knowledge_base", "web_search")


class VerifyTurn(BaseModel):
    # A one-sentence explanation of the call for the log. Older transcripts used "thought".
    purpose: str = Field(default="", validation_alias=AliasChoices("purpose", "thought"))
    action: Literal["search_knowledge_base", "web_search", "finish"]
    input: dict[str, Any] = Field(default_factory=dict)


class FinishInput(BaseModel):
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(validation_alias=AliasChoices("rationale", "reason", "explanation", "justification"))
    evidence_ids: list[str] = Field(default_factory=list,
                                    validation_alias=AliasChoices("evidence_ids", "evidence", "sources", "citations"))


def normalize_turn(raw: Any) -> Any:
    """Accept the common near-misses so a formatting slip doesn't cost a turn: the query given as a
    plain string, finish fields placed beside "input" instead of inside it, or familiar key synonyms."""
    if not isinstance(raw, dict):
        return raw
    d = dict(raw)
    for alt in ("tool", "name"):
        if "action" not in d and alt in d:
            d["action"] = d.pop(alt)
    for alt in ("arguments", "args", "parameters", "params"):
        if "input" not in d and alt in d:
            d["input"] = d.pop(alt)
    if isinstance(d.get("input"), str):
        d["input"] = {"query": d["input"]}
    if d.get("action") == "finish":
        inp = dict(d.get("input") or {})
        for key in ("verdict", "confidence", "rationale", "reason", "explanation", "evidence_ids", "evidence"):
            if key not in inp and key in d:
                inp[key] = d[key]
        d["input"] = inp
    return d


def describe_error(exc: Exception) -> str:
    """One line naming what was wrong, for the model's correction prompt and the log."""
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in e['loc']) or 'reply'}: {e['msg']}" for e in exc.errors()[:3])
    return str(exc)[:160]


def calibrate(verdict: Verdict, raw: float, cited: list[Evidence]) -> float:
    """Cap confidence by the evidence actually gathered.

    unverifiable → ≤0.50 (by definition the evidence is insufficient)
    no tool evidence → ≤0.60 (model knowledge only)
    one independent source type → ≤0.90; knowledge base + web agreeing → ≤0.95
    """
    sources = {e.source for e in cited if e.source != "model_knowledge"}
    if verdict == Verdict.UNVERIFIABLE:
        cap = 0.5
    elif not sources:
        cap = 0.6
    elif len(sources) == 1:
        cap = 0.9
    else:
        cap = 0.95
    return round(max(0.0, min(raw, cap)), 2)


def run_tool(ctx: RunContext, action: str, query: str, next_id: int, step: str) -> list[Evidence]:
    def on_retry(attempt: int, exc: Exception) -> None:
        ctx.trace.warn(step, f"{action} failed ({exc}); retry {attempt}", indent=1)

    if action == "search_knowledge_base":
        hits = ctx.kb.search(query, k=3)
        return [
            Evidence(
                id=f"E{next_id + i}", source="knowledge_base", title=f"{h.entry.id}: {h.entry.title}",
                snippet=h.entry.content, url=h.entry.url, date=h.entry.as_of, relevance=h.relevance,
            )
            for i, h in enumerate(hits)
        ]
    if ctx.search is None:
        raise ToolError("web search is disabled in this deployment")
    results = with_retries(lambda: ctx.search.search(query, k=3), attempts=3, on_retry=on_retry)
    return [
        Evidence(
            id=f"E{next_id + i}", source="web_search", title=r.title, snippet=r.snippet,
            url=r.url, date=r.published, relevance=r.relevance,
        )
        for i, r in enumerate(results)
    ]


def format_observation(results: list[Evidence]) -> str:
    if not results:
        return "No results."
    lines = []
    for ev in results:
        when = f"as of {ev.date}" if ev.source == "knowledge_base" else f"published {ev.date or 'unknown'}"
        lines.append(f"[{ev.id}] {ev.source} · {when} · relevance {ev.relevance:.2f}\n    {ev.title}\n    {ev.snippet}")
    return "\n".join(lines)


def verify_claim(
    ctx: RunContext, claim: Claim, episode_title: str, max_steps: int, tools: list[str]
) -> FactCheck:
    step = f"verify {claim.id}"
    s = ctx.settings
    ctx.trace.event("step", step, f"“{claim.claim}” ({claim.type}, {claim.speaker} @ {claim.timestamp})")
    system = verify_system(s.today, max_steps, tools)
    messages: list[dict[str, str]] = [{"role": "user", "content": verify_opening(claim, episode_title)}]
    evidence: dict[str, Evidence] = {}
    history: list[dict[str, Any]] = []
    tool_calls, tools_used = 0, []
    corrections = 0
    final: FinishInput | None = None
    max_turns = max_steps + 4

    for _turn in range(max_turns):
        force = tool_calls >= max_steps
        try:
            resp = ctx.llm.complete(
                system=system, messages=messages, tier="main", max_tokens=800, temperature=s.temperature,
                task="verify_step",
                context={"claim": claim, "history": history, "tools": tools, "max_steps": max_steps,
                         "today": s.today, "force_finish": force},
            )
        except LLMRefusal as exc:
            # A refusal is a policy decision, not a parse error: don't resend the same request.
            ctx.trace.error(step, f"model declined: {exc}", indent=1, category=exc.category)
            break
        ctx.record_usage(resp)
        ctx.trace.llm_call(step, resp)
        messages.append({"role": "assistant", "content": resp.text})
        try:
            turn = VerifyTurn.model_validate(normalize_turn(extract_json(resp.text)))
        except (ValueError, ValidationError) as exc:
            corrections += 1
            problem = describe_error(exc)
            ctx.trace.event("self_correct", step, f"unusable turn ({problem}); asking again", indent=1,
                            reply=resp.text[:600])
            if corrections > 2:
                break
            messages.append({"role": "user", "content": f"That reply could not be used ({problem}). Respond with exactly "
                             'one JSON object: {"action": "<tool name or finish>", "input": {...}, "purpose": "<one sentence>"}'})
            continue
        if turn.purpose:
            ctx.trace.event("purpose", step, turn.purpose, indent=1)

        if turn.action == "finish":
            try:
                candidate = FinishInput.model_validate(turn.input)
                problems = [f"unknown evidence id {i}" for i in candidate.evidence_ids if i not in evidence]
                if candidate.verdict != Verdict.UNVERIFIABLE and not candidate.evidence_ids and evidence:
                    problems.append("cite the evidence ids that support this verdict")
            except ValidationError as exc:
                candidate, problems = None, [f"invalid finish input: {describe_error(exc)}"]
            if problems and corrections < 2:
                corrections += 1
                ctx.trace.event("self_correct", step, f"finish rejected — {problems[0]}", indent=1)
                messages.append({"role": "user", "content": "Your finish call has problems:\n- " + "\n- ".join(problems)
                                 + f"\nValid evidence ids: {', '.join(evidence) or 'none'}. Call finish again."})
                continue
            if candidate is not None:
                candidate.evidence_ids = [i for i in candidate.evidence_ids if i in evidence]
                final = candidate
            break

        if force:
            corrections += 1
            messages.append({"role": "user", "content": f"You have used all {max_steps} tool calls. Call finish now."})
            if corrections > 3:
                break
            continue

        query = str(turn.input.get("query") or "").strip() or claim.claim
        if turn.action not in tools:
            observation = f"ERROR: tool '{turn.action}' is not available. Available tools: {', '.join(tools)}, finish."
            ctx.trace.warn(step, observation, indent=1)
            results: list[Evidence] = []
            error = observation
        else:
            ctx.trace.event("action", step, f'{turn.action}("{query}")', indent=1)
            tool_calls += 1
            tools_used.append(turn.action)
            try:
                results = run_tool(ctx, turn.action, query, len(evidence) + 1, step)
                error = None
            except ToolError as exc:
                results, error = [], f"ERROR: {turn.action} is unavailable ({exc}). Use another tool or finish."
                ctx.trace.warn(step, error, indent=1)
            observation = error or format_observation(results)
            for ev in results:
                evidence[ev.id] = ev
            if results:
                top = results[0]
                ctx.trace.event(
                    "observation", step,
                    f"{len(results)} result(s); top [{top.id}] {top.title[:70]} (relevance {top.relevance:.2f}, {top.date})",
                    indent=1,
                )
            elif not error:
                ctx.trace.event("observation", step, "no results", indent=1)
        history.append({"action": turn.action, "query": query, "error": error,
                        "results": [ev.model_dump() for ev in results]})
        left = max_steps - tool_calls
        budget = f"\n\n({left} tool call(s) left.)" if left > 0 else f"\n\nYou have used all {max_steps} tool calls. Call finish now."
        messages.append({"role": "user", "content": f"Observation:\n{observation}{budget}"})

    if final is None:
        fc = FactCheck(
            claim=claim, verdict=Verdict.UNVERIFIABLE, confidence=0.2, raw_confidence=0.0,
            rationale="The verification loop did not produce a valid verdict within its budget; flagged for human review.",
            evidence=list(evidence.values())[:2], tool_calls=tool_calls, tools_used=tools_used,
        )
        ctx.trace.warn(step, "no valid verdict — defaulting to ❓ unverifiable (0.20) for human review", indent=1)
        return fc

    cited = [evidence[i] for i in final.evidence_ids]
    if final.verdict != Verdict.UNVERIFIABLE and not cited:
        cited = [Evidence(id="M1", source="model_knowledge", title="Model background knowledge (no tool evidence)",
                          snippet=final.rationale)]
    confidence = calibrate(final.verdict, final.confidence, cited)
    ctx.trace.event(
        "verdict", step,
        f"{final.verdict.emoji} {final.verdict.label} · confidence {confidence:.2f}"
        + (f" (model said {final.confidence:.2f}, capped by evidence)" if confidence < round(final.confidence, 2) else ""),
        indent=1,
    )
    ctx.trace.event("info", step, final.rationale, indent=2)
    return FactCheck(
        claim=claim, verdict=final.verdict, confidence=confidence, raw_confidence=round(final.confidence, 2),
        rationale=final.rationale, evidence=cited, tool_calls=tool_calls, tools_used=tools_used,
    )


@register("verify_claims")
def verify_claims(ctx: RunContext, state: EpisodeState, params: dict[str, Any]) -> tuple[str, int]:
    claims = state.claims or []
    if not claims:
        return "no claims to verify", 0
    available = ["search_knowledge_base"] + (["web_search"] if ctx.search else [])
    tools = [t for t in (params.get("tools") or available) if t in available] or available
    max_steps = int(params.get("max_steps_per_claim") or ctx.settings.max_steps_per_claim)
    state.fact_checks = [verify_claim(ctx, c, state.episode.title, max_steps, tools) for c in claims]
    counts = {v: sum(1 for fc in state.fact_checks if fc.verdict == v) for v in Verdict}
    calls = sum(fc.tool_calls for fc in state.fact_checks)
    return (
        f"{counts[Verdict.VERIFIED]} ✅ · {counts[Verdict.OUTDATED]} ⚠️ · {counts[Verdict.UNVERIFIABLE]} ❓ "
        f"across {len(claims)} claims · {calls} tool calls",
        len(state.fact_checks),
    )
