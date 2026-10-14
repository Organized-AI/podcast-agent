"""Transcript-level agent steps: normalize, summarize, key notes, claim extraction."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from podcast_agent.agent.pipeline import EpisodeState, register
from podcast_agent.llm.structured import call_structured
from podcast_agent.models import Claim, ClaimType, Episode, Notes, Quote, Summary, Takeaway, Utterance
from podcast_agent.prompts import (
    CLAIMS_SYSTEM,
    MAP_SYSTEM,
    NOTES_PROMPT,
    NOTES_SYSTEM,
    SUMMARY_SYSTEM,
    claims_prompt,
    episode_context,
    map_prompt,
    reduce_prompt,
    summary_prompt,
)
from podcast_agent.text import kebab, span_match, word_count
from podcast_agent.transcript import normalize_episode

if TYPE_CHECKING:
    from podcast_agent.agent.context import RunContext


# --------------------------------------------------------------------------- normalize


@register("normalize_transcript")
def normalize_transcript(ctx: RunContext, state: EpisodeState, params: dict[str, Any]) -> tuple[str, int]:
    state.episode = normalize_episode(state.episode)
    c = state.episode.cleaning
    detail = (
        f"removed {c['fillers']} filler words, {c['filler_phrases']} filler phrases, "
        f"{c['repeated_words']} repeated words across {c['utterances_changed']} lines"
    )
    for u in state.episode.utterances:
        if u.text != u.raw_text:
            ctx.trace.event("observation", "normalize", f"[{u.timestamp}] {u.raw_text!r} → {u.text!r}", indent=1)
    return detail, 0


# --------------------------------------------------------------------------- summarize


class SummaryOut(BaseModel):
    summary: str


class MapOut(BaseModel):
    notes: list[str]


def summary_bounds(ep: Episode, min_words: int, max_words: int, mode: str = "spec") -> tuple[int, int, int, int]:
    """Return (target_lo, target_hi, accept_lo, accept_hi).

    The prompt asks for the target range; the validator accepts ±10% because the spec says "~200–300".
    In adaptive mode the floor drops to ~60% of very short transcripts so the model is never pushed
    to pad a summary that would be longer than the source.
    """
    lo = min(min_words, max(60, int(ep.word_count * 0.6))) if mode == "adaptive" else min_words
    return lo, max_words, int(lo * 0.9), int(max_words * 1.07)


def chunk_episode(ep: Episode, target_words: int = 1500) -> list[list[Utterance]]:
    """Split into ~target_words segments, preferring to cut at section boundaries."""
    chunks: list[list[Utterance]] = [[]]
    words = 0
    for u in ep.utterances:
        n = word_count(u.text)
        prev_section = chunks[-1][-1].section if chunks[-1] else None
        boundary = u.section != prev_section
        if chunks[-1] and (words + n > target_words * 1.25 or (boundary and words >= target_words * 0.6)):
            chunks.append([])
            words = 0
        chunks[-1].append(u)
        words += n
    return [c for c in chunks if c]


@register("summarize")
def summarize(ctx: RunContext, state: EpisodeState, params: dict[str, Any]) -> tuple[str, int]:
    ep = state.episode
    s = ctx.settings
    lo, hi, accept_lo, accept_hi = summary_bounds(ep, s.summary_min_words, s.summary_max_words, s.summary_length_mode)
    strategy: Literal["single_pass", "map_reduce"] = (
        "map_reduce" if params.get("strategy") == "map_reduce"
        or (not params.get("strategy") and ep.word_count > s.map_reduce_threshold_words) else "single_pass"
    )

    def validate(out: SummaryOut) -> list[str]:
        problems = []
        wc = word_count(out.summary)
        if wc < accept_lo:
            problems.append(f"the summary is {wc} words; it must be {lo}–{hi} words — expand it using only the transcript")
        if wc > accept_hi:
            problems.append(f"the summary is {wc} words; it must be {lo}–{hi} words — tighten it")
        if re.search(r"\[?\b\d{1,2}:\d{2}\b", out.summary):
            problems.append("remove timestamps from the summary")
        if re.search(r"^\s*[-*•]", out.summary, re.MULTILINE):
            problems.append("write prose, not bullet points")
        return problems

    attempts = 0
    if strategy == "map_reduce":
        segments = chunk_episode(ep)
        ctx.trace.info("summarize", f"map-reduce over {len(segments)} segments")
        segment_notes: list[list[str]] = []
        for i, seg in enumerate(segments, 1):
            seg_text = "\n".join(f"[{u.timestamp}] {u.speaker_name}: {u.text}" for u in seg)
            seg_res = call_structured(
                ctx, step="summarize", schema=MapOut, system=MAP_SYSTEM,
                prompt=map_prompt(ep, seg_text, i, len(segments)), tier="fast", max_tokens=700,
                task="summarize_map", context={"episode": ep, "segment": seg},
            )
            attempts += seg_res.attempts
            segment_notes.append(seg_res.value.notes)
            ctx.trace.event("observation", "summarize", f"segment {i}/{len(segments)}: {len(seg_res.value.notes)} notes", indent=1)
        res = call_structured(
            ctx, step="summarize", schema=SummaryOut, system=SUMMARY_SYSTEM,
            prompt=reduce_prompt(ep, segment_notes, lo, hi), max_tokens=900, validate=validate,
            task="summarize", context={"episode": ep, "min_words": lo, "max_words": hi},
        )
    else:
        res = call_structured(
            ctx, step="summarize", schema=SummaryOut, system=SUMMARY_SYSTEM,
            prompt=summary_prompt(lo, hi), cache_prefix=episode_context(ep), max_tokens=900,
            validate=validate, task="summarize", context={"episode": ep, "min_words": lo, "max_words": hi},
        )
    attempts += res.attempts
    text = re.sub(r"\s+\n", "\n", res.value.summary).strip()
    state.summary = Summary(text=text, word_count=word_count(text), strategy=strategy)
    if res.unresolved:
        state.warnings.append(f"summary: {'; '.join(res.unresolved)}")
        ctx.trace.warn("summarize", f"accepted with unresolved issues: {res.unresolved[0]}")
    return f"{state.summary.word_count} words ({strategy}, target {lo}–{hi})", attempts


# --------------------------------------------------------------------------- key notes

VERBATIM_THRESHOLD = 0.9


class QuoteIn(BaseModel):
    text: str
    speaker: str = ""
    timestamp: str = ""


class NotesOut(BaseModel):
    takeaways: list[Takeaway]
    quotes: list[QuoteIn]
    topics: list[str]


def locate_span(ep: Episode, text: str, timestamp: str | None = None) -> tuple[Utterance | None, float]:
    """Find the transcript line a quote comes from. Prefers the claimed timestamp when it matches."""
    claimed = ep.find_timestamp(timestamp) if timestamp else None
    best_u, best = None, 0.0
    if claimed:
        best_u, best = claimed, max(span_match(text, claimed.text), span_match(text, claimed.raw_text))
        if best >= VERBATIM_THRESHOLD:
            return best_u, best
    for u in ep.utterances:
        score = max(span_match(text, u.text), span_match(text, u.raw_text))
        if score > best:
            best_u, best = u, score
    return best_u, best


def _strip_wrapping_quotes(text: str) -> str:
    return text.strip().strip('"“”\'‘’').strip()


def _normalize_topics(topics: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in (kebab(x) for x in topics) if t))


@register("extract_notes")
def extract_notes(ctx: RunContext, state: EpisodeState, params: dict[str, Any]) -> tuple[str, int]:
    ep = state.episode

    def validate(out: NotesOut) -> list[str]:
        problems = []
        if len(out.takeaways) != 5:
            problems.append(f"return exactly 5 takeaways (got {len(out.takeaways)})")
        for i, t in enumerate(out.takeaways, 1):
            if word_count(t.text) > 40:
                problems.append(f"takeaway {i} is {word_count(t.text)} words; keep each under 30 words")
            if t.timestamp and not ep.find_timestamp(t.timestamp):
                problems.append(f"takeaway {i} cites [{t.timestamp}], which is not a timestamp in the transcript")
        if not 3 <= len(out.quotes) <= 5:
            problems.append(f"return 3–5 quotes (got {len(out.quotes)})")
        for i, q in enumerate(out.quotes, 1):
            text = _strip_wrapping_quotes(q.text)
            u, score = locate_span(ep, text, q.timestamp)
            if score < VERBATIM_THRESHOLD:
                where = f" (closest line is [{u.timestamp}])" if u else ""
                problems.append(
                    f"quote {i} is not verbatim — best match {score:.2f}{where}; copy the exact words from a single line"
                )
            elif word_count(text) < 5:
                problems.append(f"quote {i} is too short to stand alone; use 6–40 words")
        n_topics = len(_normalize_topics(out.topics))
        if not 4 <= n_topics <= 10:
            problems.append(f"return 5–8 distinct topic tags (got {n_topics})")
        return problems

    res = call_structured(
        ctx, step="extract_notes", schema=NotesOut, system=NOTES_SYSTEM, prompt=NOTES_PROMPT,
        cache_prefix=episode_context(ep), max_tokens=1500, validate=validate,
        task="extract_notes", context={"episode": ep},
    )
    out = res.value

    # Deterministic post-processing: ground every quote in the transcript and repair attribution.
    quotes: list[Quote] = []
    for i, q in enumerate(out.quotes, 1):
        text = _strip_wrapping_quotes(q.text)
        u, score = locate_span(ep, text, q.timestamp)
        if u is None or score < VERBATIM_THRESHOLD:
            ctx.trace.warn("extract_notes", f"dropped quote {i}: not found in transcript (best match {score:.2f})")
            state.warnings.append(f"notes: dropped non-verbatim quote “{text[:60]}…”")
            continue
        if q.timestamp and ep.find_timestamp(q.timestamp) is not u:
            ctx.trace.event("self_correct", "extract_notes",
                            f"quote {i}: timestamp [{q.timestamp}] → [{u.timestamp}] (matched the source line)", indent=1)
        if q.speaker and q.speaker != u.speaker_name:
            ctx.trace.event("self_correct", "extract_notes",
                            f"quote {i}: speaker '{q.speaker}' → '{u.speaker_name}'", indent=1)
        if any(existing.text == text for existing in quotes):
            continue
        quotes.append(Quote(text=text, speaker=u.speaker_name, timestamp=u.timestamp, match_score=round(score, 3)))

    takeaways = []
    for t in out.takeaways[:5]:
        ts = t.timestamp if t.timestamp and ep.find_timestamp(t.timestamp) else None
        takeaways.append(Takeaway(text=t.text.strip(), timestamp=ts))
    topics = _normalize_topics(out.topics)[:8]

    state.notes = Notes(takeaways=takeaways, quotes=quotes, topics=topics)
    for i, t in enumerate(takeaways, 1):
        ctx.trace.event("observation", "extract_notes", f"takeaway {i}: {t.text}", indent=1)
    for kept in quotes:
        ctx.trace.event("observation", "extract_notes",
                        f"quote [{kept.timestamp}] {kept.speaker}: “{kept.text}” (match {kept.match_score:.2f})", indent=1)
    ctx.trace.event("observation", "extract_notes", "topics: " + " ".join(f"#{t}" for t in topics), indent=1)
    if res.unresolved:
        state.warnings.append(f"notes: {'; '.join(res.unresolved)}")
    return f"{len(takeaways)} takeaways · {len(quotes)} verbatim quotes · {len(topics)} topics", res.attempts


# --------------------------------------------------------------------------- claim extraction


class ClaimIn(BaseModel):
    claim: str
    type: ClaimType = "factual"
    time_sensitive: bool = False
    speaker: str = ""
    timestamp: str = ""
    quote: str = ""


class ClaimsOut(BaseModel):
    claims: list[ClaimIn]


@register("extract_claims")
def extract_claims(ctx: RunContext, state: EpisodeState, params: dict[str, Any]) -> tuple[str, int]:
    ep = state.episode
    max_claims = int(params.get("max_claims") or ctx.settings.max_claims)

    def validate(out: ClaimsOut) -> list[str]:
        problems = []
        for i, c in enumerate(out.claims, 1):
            if word_count(c.claim) < 4:
                problems.append(f"claim {i} is not a complete standalone sentence")
            _u, score = locate_span(ep, c.quote or c.claim, c.timestamp)
            if score < 0.85:
                problems.append(f"claim {i}: the quote must be a verbatim span of the transcript line it comes from")
        if len({c.claim.lower() for c in out.claims}) != len(out.claims):
            problems.append("remove duplicate claims")
        return problems

    res = call_structured(
        ctx, step="extract_claims", schema=ClaimsOut, system=CLAIMS_SYSTEM, prompt=claims_prompt(max_claims),
        cache_prefix=episode_context(ep), tier="fast", max_tokens=1800, validate=validate,
        task="extract_claims", context={"episode": ep, "max_claims": max_claims},
    )
    claims: list[Claim] = []
    for c in res.value.claims[:max_claims]:
        u, score = locate_span(ep, c.quote or c.claim, c.timestamp)
        if u is None or score < 0.85:
            ctx.trace.warn("extract_claims", f"dropped claim not grounded in the transcript: {c.claim[:70]}")
            continue
        claims.append(
            Claim(
                id=f"C{len(claims) + 1}",
                claim=c.claim.strip(),
                speaker=u.speaker_name,
                timestamp=u.timestamp,
                quote=_strip_wrapping_quotes(c.quote) or c.claim,
                type=c.type,
                time_sensitive=c.time_sensitive,
            )
        )
    state.claims = claims
    by_type: dict[str, int] = {}
    for cl in claims:
        by_type[cl.type] = by_type.get(cl.type, 0) + 1
        flag = " · time-sensitive" if cl.time_sensitive else ""
        ctx.trace.event("observation", "extract_claims", f"{cl.id} [{cl.timestamp}] ({cl.type}{flag}) {cl.claim}", indent=1)
    mix = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items()))
    return f"{len(claims)} check-worthy claims ({mix or 'none'})", res.attempts
