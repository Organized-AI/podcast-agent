"""Typed data contracts shared by every stage of the pipeline.

Every LLM output is parsed into one of these models, so a malformed
response fails loudly at the boundary instead of leaking into the report.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

Role = Literal["host", "guest", "unknown"]


# --------------------------------------------------------------------------- transcript


class Utterance(BaseModel):
    idx: int
    timestamp: str  # as written in the source, e.g. "01:20" or "00:02:14"
    seconds: int
    speaker: str  # label as written in the source, e.g. "Dr. Priya"
    speaker_name: str  # resolved full name, e.g. "Dr. Priya Patel"
    role: Role = "unknown"
    section: str | None = None
    text: str  # normalized text (fillers removed once normalize_transcript has run)
    raw_text: str  # untouched source text


class Episode(BaseModel):
    episode_id: str
    title: str
    host: str | None = None
    guests: list[str] = Field(default_factory=list)
    utterances: list[Utterance]
    source_path: str | None = None
    source_format: Literal["json", "txt"] = "json"
    cleaning: dict[str, int] = Field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return sum(len(re.findall(r"\b[\w'’-]+\b", u.text)) for u in self.utterances)

    @property
    def duration_seconds(self) -> int:
        return self.utterances[-1].seconds if self.utterances else 0

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for u in self.utterances:
            if u.speaker_name not in seen:
                seen.append(u.speaker_name)
        return seen

    @property
    def sections(self) -> list[str]:
        seen: list[str] = []
        for u in self.utterances:
            if u.section and u.section not in seen:
                seen.append(u.section)
        return seen

    def render(self, raw: bool = False) -> str:
        """Render the transcript as timestamped lines for prompting."""
        lines = []
        for u in self.utterances:
            section = f" [{u.section}]" if u.section else ""
            text = u.raw_text if raw else u.text
            lines.append(f"[{u.timestamp}] {u.speaker_name} ({u.role}){section}: {text}")
        return "\n".join(lines)

    def find_timestamp(self, timestamp: str) -> Utterance | None:
        target = normalize_timestamp(timestamp)
        for u in self.utterances:
            if normalize_timestamp(u.timestamp) == target:
                return u
        return None


def timestamp_to_seconds(ts: str) -> int:
    parts = [int(p) for p in ts.strip().strip("[]").split(":")]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    raise ValueError(f"Unrecognised timestamp: {ts!r}")


def normalize_timestamp(ts: str) -> int:
    """Compare timestamps by seconds so "01:20" == "00:01:20"."""
    try:
        return timestamp_to_seconds(ts)
    except (ValueError, AttributeError):
        return -1


def format_seconds(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- plan

StepName = Literal[
    "normalize_transcript",
    "summarize",
    "extract_notes",
    "extract_claims",
    "verify_claims",
]


class PlanStep(BaseModel):
    step: StepName
    reason: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]
    repaired: bool = False
    repair_notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- outputs


class Summary(BaseModel):
    text: str
    word_count: int
    strategy: Literal["single_pass", "map_reduce"] = "single_pass"


class Takeaway(BaseModel):
    text: str
    timestamp: str | None = None


class Quote(BaseModel):
    text: str
    speaker: str
    timestamp: str
    match_score: float = 1.0  # fuzzy match against the transcript line (1.0 = verbatim)


class Notes(BaseModel):
    takeaways: list[Takeaway]
    quotes: list[Quote]
    topics: list[str]


ClaimType = Literal["factual", "statistical", "historical", "prediction", "anecdotal"]


class Claim(BaseModel):
    id: str
    claim: str  # standalone, decontextualized statement
    speaker: str
    timestamp: str
    quote: str  # supporting span from the transcript
    type: ClaimType = "factual"
    time_sensitive: bool = False


class Verdict(StrEnum):
    VERIFIED = "verified"
    OUTDATED = "possibly_outdated_or_inaccurate"
    UNVERIFIABLE = "unverifiable"

    @property
    def emoji(self) -> str:
        return {"verified": "✅", "possibly_outdated_or_inaccurate": "⚠️", "unverifiable": "❓"}[self.value]

    @property
    def label(self) -> str:
        return {
            "verified": "Verified true",
            "possibly_outdated_or_inaccurate": "Possibly outdated/inaccurate",
            "unverifiable": "Unverifiable",
        }[self.value]


EvidenceSource = Literal["knowledge_base", "web_search", "model_knowledge"]


class Evidence(BaseModel):
    id: str
    source: EvidenceSource
    title: str
    snippet: str
    url: str | None = None
    date: str | None = None
    relevance: float = 0.0


class FactCheck(BaseModel):
    claim: Claim
    verdict: Verdict
    confidence: float
    raw_confidence: float  # what the model reported, before calibration caps
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: int = 0
    tools_used: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def method(self) -> str:
        """Which of the PDF's three verification methods produced the verdict."""
        sources = {e.source for e in self.evidence if e.source != "model_knowledge"}
        if sources == {"knowledge_base", "web_search"}:
            return "knowledge base + web search"
        if sources == {"knowledge_base"}:
            return "knowledge base"
        if sources == {"web_search"}:
            return "web search"
        return "reasoning step"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verification(self) -> str:
        """Verification cell in the assignment's style, e.g. "✅ Verified true (confirmed by knowledge base KB-031)"."""
        kb_ids = [m.group(1) for e in self.evidence if e.source == "knowledge_base"
                  for m in [re.match(r"(KB-\d+)", e.title)] if m]
        web = [e for e in self.evidence if e.source == "web_search"]
        if self.verdict == Verdict.UNVERIFIABLE:
            reason = {"anecdotal": "personal anecdote", "prediction": "future prediction"}.get(
                self.claim.type, "no supporting source found")
            return f"{self.verdict.emoji} {self.verdict.label} ({reason})"
        if kb_ids:
            basis = "knowledge base " + ", ".join(dict.fromkeys(kb_ids))
            if web and self.verdict == Verdict.VERIFIED:
                basis += " and web search"
        elif web:
            basis = f"web search: {web[0].title}"
        else:
            basis = "reasoning step, no external source"
        if basis.startswith("reasoning"):
            return f"{self.verdict.emoji} {self.verdict.label} ({basis})"
        verb = "confirmed by" if self.verdict == Verdict.VERIFIED else "contradicted by"
        return f"{self.verdict.emoji} {self.verdict.label} ({verb} {basis})"


class FactCheckRow(BaseModel):
    """One row of the assignment's fact-check table: Claim | Verification | Confidence."""

    claim: str
    verification: str
    confidence: float


# --------------------------------------------------------------------------- run metadata


class StepResult(BaseModel):
    step: str
    status: Literal["ok", "failed", "skipped"]
    duration_ms: int = 0
    attempts: int = 0
    detail: str = ""


class Usage(BaseModel):
    llm_calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    estimated: bool = False  # True when token counts are estimates (offline provider)


class EpisodeMeta(BaseModel):
    episode_id: str
    title: str
    host: str | None
    guests: list[str]
    duration: str
    word_count: int
    utterances: int
    source: str | None
    cleaning: dict[str, int] = Field(default_factory=dict)


class EpisodeReport(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    generated_at: str
    episode: EpisodeMeta
    provider: str
    models: dict[str, str]
    plan: Plan | None = None
    summary: Summary | None = None
    notes: Notes | None = None
    fact_checks: list[FactCheck] = Field(default_factory=list)
    steps: list[StepResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    warnings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fact_check_table(self) -> list[FactCheckRow]:
        return [FactCheckRow(claim=fc.claim.claim, verification=fc.verification, confidence=fc.confidence)
                for fc in self.fact_checks]
