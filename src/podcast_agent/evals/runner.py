"""Run an eval suite and check its thresholds.

A suite (``evals/suites/<name>.yaml``) has up to two sections:

* ``factcheck``: every case in a labeled dataset goes through the real verifier with the configured
  provider (the offline heuristics, or Claude in the nightly suite), each on the case's own "today".
* ``runs``: committed run folders scored against per-episode expectations, without calling a model.

Each section reports metrics; ``thresholds`` turn some of them into gates. A bare number is a minimum,
``{max: x}`` a maximum. Any miss makes the suite fail, and ``podcast-agent eval`` exits non-zero.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from podcast_agent.agent.context import RunContext
from podcast_agent.agent.steps import VERBATIM_THRESHOLD, locate_span
from podcast_agent.agent.verifier import verify_claim
from podcast_agent.evals import metrics as m
from podcast_agent.evals.datasets import (
    EpisodeExpectation,
    FactCheckCase,
    load_episode_expectations,
    load_factcheck_cases,
)
from podcast_agent.models import Claim, EpisodeReport
from podcast_agent.text import word_count
from podcast_agent.transcript import load_episode, normalize_episode

TOOL_SOURCES = ("knowledge_base", "web_search")


@dataclass
class Check:
    section: str
    metric: str
    value: float
    threshold: float
    direction: str  # "min" or "max"

    @property
    def passed(self) -> bool:
        return self.value >= self.threshold if self.direction == "min" else self.value <= self.threshold


@dataclass
class Section:
    name: str
    kind: str  # "factcheck" or "run"
    metrics: dict[str, float]
    rows: list[dict[str, Any]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)


@dataclass
class SuiteResult:
    suite: str
    description: str
    provider: str
    sections: list[Section]

    @property
    def checks(self) -> list[Check]:
        return [c for s in self.sections for c in s.checks]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite, "description": self.description, "provider": self.provider, "passed": self.passed,
            "sections": [{"name": s.name, "kind": s.kind, "metrics": s.metrics, "rows": s.rows,
                          "checks": [{**asdict(c), "passed": c.passed} for c in s.checks]} for s in self.sections],
        }


# --------------------------------------------------------------------------- fact-check cases


def run_factcheck(ctx: RunContext, cases: list[FactCheckCase], *, max_steps: int, tools: list[str],
                  name: str = "factcheck") -> Section:
    """Verify every case with the context's provider and score the verdicts against the labels."""
    rows: list[dict[str, Any]] = []
    usage_before = ctx.usage.model_copy()
    for case in cases:
        ctx.settings.today = case.today
        ctx.trace.episode_id = f"eval:{case.id}"
        claim = Claim(id=case.id, claim=case.claim, speaker="eval", timestamp="00:00", quote=case.claim,
                      type=case.type, time_sensitive=case.time_sensitive)
        fc = verify_claim(ctx, claim, "Eval dataset", max_steps, tools)
        cited_kb = [e.title.split(":", 1)[0] for e in fc.evidence if e.source == "knowledge_base"]
        rows.append({
            "id": case.id, "tags": case.tags, "expected": case.expected, "acceptable": case.acceptable,
            "verdict": fc.verdict.value, "correct": fc.verdict.value in case.acceptable,
            "confidence": fc.confidence, "raw_confidence": fc.raw_confidence,
            "tool_evidence": sum(1 for e in fc.evidence if e.source in TOOL_SOURCES),
            "cited_kb": cited_kb, "kb_expected": case.kb, "tool_calls": fc.tool_calls,
        })
    correct = [r["correct"] for r in rows]
    asserted = [r for r in rows if r["verdict"] != "unverifiable"]
    with_kb = [r for r in rows if r["kb_expected"] and r["expected"] != "unverifiable"]
    used = ctx.usage
    values = {
        "cases": float(len(rows)),
        "verdict_accuracy": m.accuracy(correct),
        # an acceptable alternative counts as the expected label, so ambiguous cases are not penalised
        "macro_f1": m.macro_f1([r["verdict"] if r["correct"] else r["expected"] for r in rows],
                               [r["verdict"] for r in rows]),
        "citation_validity": m.accuracy([r["tool_evidence"] > 0 for r in asserted]) if asserted else 1.0,
        "kb_recall": m.accuracy([bool(set(r["cited_kb"]) & set(r["kb_expected"])) for r in with_kb]) if with_kb else 1.0,
        # Calibration only means something for ✅ and ⚠️: there confidence is how sure the agent is of the
        # verdict. A ❓ carries low confidence by design, so including it would punish correct abstentions.
        "brier": m.brier([r["confidence"] for r in asserted], [r["correct"] for r in asserted]),
        "ece": m.expected_calibration_error([r["confidence"] for r in asserted], [r["correct"] for r in asserted]),
        "tool_calls_per_case": sum(r["tool_calls"] for r in rows) / len(rows) if rows else 0.0,
        "llm_calls": float(used.llm_calls - usage_before.llm_calls),
        "input_tokens": float(used.input_tokens - usage_before.input_tokens),
        "output_tokens": float(used.output_tokens - usage_before.output_tokens),
    }
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        for tag in r["tags"]:
            by_tag[tag].append(r["correct"])
    values.update({f"accuracy.{tag}": m.accuracy(oks) for tag, oks in sorted(by_tag.items())})
    return Section(name=name, kind="factcheck", metrics=_round(values), rows=rows)


# --------------------------------------------------------------------------- scoring a run folder


def score_run(run_dir: str | Path, expectations: dict[str, EpisodeExpectation], *, name: str | None = None,
              root: str | Path = ".") -> Section:
    """Score a finished run's report.json against the per-episode expectations. Calls no model."""
    run_dir, root = Path(run_dir), Path(root)
    combined = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for raw in combined.get("episodes", []):
        rep = EpisodeReport.model_validate(raw)
        exp = expectations.get(rep.episode.episode_id)
        if exp is None:
            continue
        source = normalize_episode(load_episode(root / exp.transcript))
        summary = rep.summary.text if rep.summary else ""
        words = word_count(summary)
        notes = rep.notes
        quotes_ok = [locate_span(source, q.text, q.timestamp)[1] >= VERBATIM_THRESHOLD for q in notes.quotes] if notes else []
        shape_ok = notes is not None and len(notes.takeaways) == exp.notes.takeaways \
            and exp.notes.quotes[0] <= len(notes.quotes) <= exp.notes.quotes[1] \
            and exp.notes.topics[0] <= len(notes.topics) <= exp.notes.topics[1]
        claim_rows = []
        for want in exp.claims:
            match = next((fc for fc in rep.fact_checks if m.matches_all(fc.claim.claim, want.match)), None)
            verdict = match.verdict.value if match is not None else None
            claim_rows.append({"match": want.match, "found": match is not None, "verdict": verdict,
                               "correct": verdict is not None and verdict in want.acceptable})
        rows.append({
            "episode": rep.episode.episode_id,
            "summary_words": words,
            "summary_in_range": exp.summary.words[0] <= words <= exp.summary.words[1],
            "summary_coverage": m.mention_coverage(summary, exp.summary.mentions),
            "notes_shape_ok": shape_ok,
            "quotes": len(quotes_ok), "quotes_verbatim": sum(quotes_ok),
            "claims": claim_rows,
            "llm_calls": rep.usage.llm_calls,
            "tokens": rep.usage.input_tokens + rep.usage.output_tokens,
        })
    claims = [c for r in rows for c in r["claims"]]
    found_claims = [c for c in claims if c["found"]]
    quotes = sum(r["quotes"] for r in rows)
    values = {
        "episodes": float(len(rows)),
        "summary_in_range": m.accuracy([r["summary_in_range"] for r in rows]),
        "summary_coverage": sum(r["summary_coverage"] for r in rows) / len(rows) if rows else 0.0,
        "notes_shape_ok": m.accuracy([r["notes_shape_ok"] for r in rows]),
        "quote_fidelity": sum(r["quotes_verbatim"] for r in rows) / quotes if quotes else 0.0,
        "claim_recall": len(found_claims) / len(claims) if claims else 1.0,
        "claim_verdict_accuracy": m.accuracy([c["correct"] for c in found_claims]) if found_claims else 0.0,
        "llm_calls_per_episode": sum(r["llm_calls"] for r in rows) / len(rows) if rows else 0.0,
        "tokens_per_episode": sum(r["tokens"] for r in rows) / len(rows) if rows else 0.0,
    }
    return Section(name=name or run_dir.name, kind="run", metrics=_round(values), rows=rows)


# --------------------------------------------------------------------------- suites


def apply_thresholds(section: Section, thresholds: dict[str, Any]) -> None:
    for metric, rule in thresholds.items():
        if metric not in section.metrics:
            raise ValueError(f"{section.name}: no metric named {metric!r} (have {', '.join(section.metrics)})")
        if isinstance(rule, dict):
            direction, value = ("max", rule["max"]) if "max" in rule else ("min", rule["min"])
        else:
            direction, value = "min", rule
        section.checks.append(Check(section.name, metric, section.metrics[metric], float(value), direction))


def run_suite(path: str | Path, ctx: RunContext | None = None, *, root: str | Path = ".") -> SuiteResult:
    """Run every section of a suite file. ``ctx`` is needed only when the suite has a factcheck section."""
    suite = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    root = Path(root)
    sections: list[Section] = []
    fc_spec = suite.get("factcheck")
    if fc_spec:
        if ctx is None:
            raise ValueError("this suite runs fact-check cases, so it needs a model context")
        cases = load_factcheck_cases(root / fc_spec["dataset"])
        available = ["search_knowledge_base"] + (["web_search"] if ctx.search else [])
        section = run_factcheck(ctx, cases, max_steps=int(fc_spec.get("max_steps_per_claim", 4)), tools=available)
        apply_thresholds(section, fc_spec.get("thresholds") or {})
        sections.append(section)
    for spec in suite.get("runs") or []:
        section = score_run(root / spec["path"], load_episode_expectations(root / spec["expectations"]),
                            name=spec.get("name"), root=root)
        apply_thresholds(section, spec.get("thresholds") or {})
        sections.append(section)
    provider = ctx.llm.describe() if ctx else {"provider": "none"}
    return SuiteResult(suite=suite.get("name", Path(path).stem), description=suite.get("description", ""),
                       provider=" · ".join(f"{k}={v}" for k, v in provider.items()), sections=sections)


# --------------------------------------------------------------------------- reports


def render_markdown(result: SuiteResult) -> str:
    out = [f"# Eval suite: {result.suite}", "", result.description, "",
           f"Provider: {result.provider} · **{'PASS' if result.passed else 'FAIL'}** "
           f"({sum(c.passed for c in result.checks)}/{len(result.checks)} thresholds met)", ""]
    for s in result.sections:
        out += [f"## {s.name} ({s.kind})", "", "| Metric | Value | Threshold | Result |", "|---|---|---|---|"]
        gated = {c.metric: c for c in s.checks}
        for metric, value in s.metrics.items():
            c = gated.get(metric)
            rule = f"{'≥' if c.direction == 'min' else '≤'} {c.threshold:g}" if c else ""
            out.append(f"| `{metric}` | {value:g} | {rule} | {('✅' if c.passed else '❌') if c else ''} |")
        out.append("")
        if s.kind == "factcheck":
            out += ["| Case | Expected | Verdict | Confidence | KB cited | |", "|---|---|---|---|---|---|"]
            for r in s.rows:
                out.append(f"| `{r['id']}` | {' / '.join(r['acceptable'])} | {r['verdict']} | {r['confidence']:.2f} | "
                           f"{', '.join(r['cited_kb']) or '—'} | {'✅' if r['correct'] else '❌'} |")
        else:
            out += ["| Episode | Summary words | Coverage | Quotes verbatim | Claims found / right |", "|---|---|---|---|---|"]
            for r in s.rows:
                found = sum(c["found"] for c in r["claims"])
                right = sum(c["correct"] for c in r["claims"])
                out.append(f"| {r['episode']} | {r['summary_words']} | {r['summary_coverage']:.2f} | "
                           f"{r['quotes_verbatim']}/{r['quotes']} | {found}/{len(r['claims'])} found, {right} right |")
            misses = [(r["episode"], c) for r in s.rows for c in r["claims"] if not c["correct"]]
            if misses:
                out += ["", "Claims that missed:"]
                out += [f"- {ep}: {' + '.join(c['match'])} → {c['verdict'] or 'not extracted'}" for ep, c in misses]
        out.append("")
    return "\n".join(out)


def write_reports(result: SuiteResult, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out / "eval.json", out / "eval.md"
    json_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def _round(values: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 4) for k, v in values.items()}
