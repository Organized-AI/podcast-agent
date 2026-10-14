"""Re-verify a finished run from its files alone: `podcast-agent audit <run_dir>`.

The audit trusts nothing the run says about itself. It re-reads the outputs, the trace and the
source transcripts and re-checks every guarantee the agent claims to enforce:

* run      files present, output hashes match the manifest (tamper evidence), trace belongs to
           this run and is complete, source transcripts unchanged
* plan     required steps present and in order, no failed steps
* notes    summary length, exactly 5 takeaways, every quote verbatim at its timestamp and
           speaker, topic tags well formed
* claims   every claim grounded in its transcript line, confidence within the evidence cap,
           cited evidence ids unique, a complete verification trace per claim

Results are PASS, WARN (accepted with a recorded warning, or not checkable) or FAIL, written to
audit.json and audit.md next to the run.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from podcast_agent.agent.steps import VERBATIM_THRESHOLD, locate_span
from podcast_agent.agent.verifier import calibrate
from podcast_agent.models import Episode, EpisodeReport, Verdict
from podcast_agent.runtime import sha256_file
from podcast_agent.text import word_count
from podcast_agent.transcript import TranscriptError, load_episode, normalize_episode

Status = Literal["pass", "warn", "fail"]
REQUIRED_STEPS = ("summarize", "extract_notes", "extract_claims", "verify_claims")
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CLAIM_GROUNDING = 0.85


@dataclass
class Check:
    scope: str  # "run" or an episode id
    area: str  # run · plan · notes · claims
    name: str
    status: Status
    detail: str


@dataclass
class AuditResult:
    run_id: str
    run_dir: str
    audited_at: str
    checks: list[Check] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(1 for c in self.checks if c.status == s) for s in ("pass", "warn", "fail")}

    def add(self, scope: str, area: str, name: str, ok: bool | None, detail: str, warn: bool = False) -> None:
        status: Status = "pass" if ok else ("warn" if warn or ok is None else "fail")
        self.checks.append(Check(scope, area, name, status, detail))

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "run_dir": self.run_dir, "audited_at": self.audited_at,
                "summary": self.counts, "checks": [asdict(c) for c in self.checks]}


# --------------------------------------------------------------------------- helpers


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _find_source(recorded: str, search_dirs: list[Path]) -> Path | None:
    p = Path(recorded)
    if p.exists():
        return p
    for d in search_dirs:
        candidate = d / p.name
        if candidate.exists():
            return candidate
    return None


def _summary_bounds(word_total: int, mode: str) -> tuple[int, int]:
    lo = min(200, max(60, int(word_total * 0.6))) if mode == "adaptive" else 200
    return int(lo * 0.9), int(300 * 1.07)


# --------------------------------------------------------------------------- audit


def audit_run(run_dir: str | Path, transcripts: list[str | Path] | None = None) -> AuditResult:
    run_dir = Path(run_dir)
    search_dirs = [Path(t) for t in (transcripts or [])] + [Path("data/transcripts")]
    manifest = _load_json(run_dir / "run_manifest.json") or {}
    combined = _load_json(run_dir / "report.json") or {}
    res = AuditResult(run_id=manifest.get("run_id") or combined.get("run_id") or "unknown",
                      run_dir=str(run_dir), audited_at=datetime.now(UTC).isoformat(timespec="seconds"))

    # ---- run-level -----------------------------------------------------------------------
    required = ["run_manifest.json", "report.json", "report.md", "logs/trace.jsonl"]
    missing = [f for f in required if not (run_dir / f).exists()]
    res.add("run", "run", "Required files present", not missing,
            "all present: " + ", ".join(required) if not missing else "missing: " + ", ".join(missing))
    if not manifest or not combined:
        return res

    hashes = manifest.get("outputs_sha256") or {}
    if hashes:
        bad = [name for name, digest in hashes.items()
               if not (run_dir / name).exists() or sha256_file(run_dir / name) != digest]
        res.add("run", "run", "Output files match the manifest hashes", not bad,
                f"{len(hashes)} files re-hashed with SHA-256, all match" if not bad
                else "changed since the run: " + ", ".join(bad))
    else:
        res.add("run", "run", "Output files match the manifest hashes", None,
                "manifest has no hashes (written by an older version)", warn=True)

    events = []
    trace_file = run_dir / "logs" / "trace.jsonl"
    if trace_file.exists():
        for line in trace_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    events.append({"_bad": line})
    foreign = [e for e in events if e.get("run_id") != res.run_id]
    unordered = sum(1 for a, b in itertools.pairwise(events) if a.get("ts", "") > b.get("ts", ""))
    expected = manifest.get("trace_events")
    complete = expected is None or expected == len(events)
    res.add("run", "run", "Trace belongs to this run, in order and complete",
            bool(events) and not foreign and not unordered and complete,
            f"{len(events)} events, all run_id {res.run_id}, timestamps in order"
            + (f", count matches the manifest ({expected})" if expected is not None else "")
            if events and not foreign and not unordered and complete
            else f"{len(events)} events · {len(foreign)} from another run · {unordered} out of order"
            + (f" · manifest expected {expected}" if not complete else ""))

    sources: dict[str, Episode | None] = {}
    changed, not_found = [], []
    for ep in manifest.get("episodes", []):
        rec = ep.get("input") or {}
        src = _find_source(rec.get("path", ""), search_dirs) if rec.get("path") else None
        if src is None:
            not_found.append(ep["episode_id"])
            sources[ep["episode_id"]] = None
            continue
        if rec.get("sha256") and sha256_file(src) != rec["sha256"]:
            changed.append(ep["episode_id"])
        try:
            sources[ep["episode_id"]] = normalize_episode(load_episode(src))
        except TranscriptError:
            sources[ep["episode_id"]] = None
    res.add("run", "run", "Source transcripts unchanged since the run", not changed and not not_found,
            f"{len(sources)} transcripts re-hashed, all match" if not changed and not not_found
            else "; ".join(filter(None, [f"changed: {', '.join(changed)}" if changed else "",
                                          f"not found: {', '.join(not_found)}" if not_found else ""])),
            warn=bool(not_found) and not changed)

    mode = (manifest.get("settings") or {}).get("summary_length_mode", "spec")

    # ---- per episode -------------------------------------------------------------------------
    for raw in combined.get("episodes", []):
        rep = EpisodeReport.model_validate(raw)
        eid = rep.episode.episode_id
        src_ep = sources.get(eid)

        names = [s.step for s in (rep.plan.steps if rep.plan else [])]
        missing_steps = [s for s in REQUIRED_STEPS if s not in names]
        ordered = "extract_claims" in names and "verify_claims" in names and \
            names.index("extract_claims") < names.index("verify_claims")
        failed = [s.step for s in rep.steps if s.status == "failed"]
        res.add(eid, "plan", "Plan has every required step, in order, and none failed",
                not missing_steps and ordered and not failed,
                f"{len(names)} planned steps; {len(rep.steps)} executed, all ok" if not missing_steps and ordered and not failed
                else "; ".join(filter(None, [f"missing {missing_steps}" if missing_steps else "",
                                              "verify before extract" if not ordered else "",
                                              f"failed {failed}" if failed else ""])))

        if rep.summary:
            lo, hi = _summary_bounds(rep.episode.word_count, mode)
            wc = word_count(rep.summary.text)
            in_range = lo <= wc <= hi
            warned = any(w.startswith("summary:") for w in rep.warnings)
            res.add(eid, "notes", f"Summary length ({mode} mode)", in_range,
                    f"{wc} words, accepted range {lo}–{hi}" if in_range
                    else f"{wc} words, outside {lo}–{hi}" + (" · accepted with a recorded warning" if warned else ""),
                    warn=warned)
        else:
            res.add(eid, "notes", "Summary present", False, "no summary in the report")

        n = rep.notes
        if n:
            res.add(eid, "notes", "Exactly 5 takeaways", len(n.takeaways) == 5, f"{len(n.takeaways)} takeaways")
            if src_ep is None:
                res.add(eid, "notes", "Quotes verbatim at their timestamp and speaker", None,
                        "source transcript not available to re-check", warn=True)
            else:
                problems = []
                for q in n.quotes:
                    u, score = locate_span(src_ep, q.text, q.timestamp)
                    if u is None or score < VERBATIM_THRESHOLD:
                        problems.append(f"[{q.timestamp}] not found (best {score:.2f})")
                    elif u.timestamp != q.timestamp or u.speaker_name != q.speaker:
                        problems.append(f"[{q.timestamp}] attributed to {q.speaker}, transcript says [{u.timestamp}] {u.speaker_name}")
                count_ok = 3 <= len(n.quotes) <= 5
                res.add(eid, "notes", "Quotes verbatim at their timestamp and speaker", not problems and count_ok,
                        f"{len(n.quotes)} quotes re-matched against the transcript, all ≥ {VERBATIM_THRESHOLD:.2f}"
                        if not problems and count_ok else "; ".join(problems + ([] if count_ok else [f"{len(n.quotes)} quotes"])))
            bad_tags = [t for t in n.topics if not KEBAB.match(t)]
            res.add(eid, "notes", "Topic tags well formed", 4 <= len(n.topics) <= 10 and not bad_tags,
                    f"{len(n.topics)} kebab-case tags" if not bad_tags else f"malformed: {bad_tags}")

        if not rep.fact_checks:
            res.add(eid, "claims", "Claims checked", None, "no fact-checks in this episode", warn=True)
            continue

        if src_ep is not None:
            ungrounded = []
            for fc in rep.fact_checks:
                u, score = locate_span(src_ep, fc.claim.quote or fc.claim.claim, fc.claim.timestamp)
                if u is None or score < CLAIM_GROUNDING:
                    ungrounded.append(f"{fc.claim.id} ({score:.2f})")
            res.add(eid, "claims", "Every claim is grounded in its transcript line", not ungrounded,
                    f"{len(rep.fact_checks)} claims re-matched at ≥ {CLAIM_GROUNDING:.2f}" if not ungrounded
                    else "ungrounded: " + ", ".join(ungrounded))

        over_cap, dupes = [], []
        for fc in rep.fact_checks:
            cap = calibrate(fc.verdict, fc.raw_confidence, fc.evidence)
            if fc.confidence > cap + 1e-6 or fc.verdict not in tuple(Verdict):
                over_cap.append(f"{fc.claim.id} {fc.confidence:.2f} > {cap:.2f}")
            ids = [e.id for e in fc.evidence]
            if len(ids) != len(set(ids)):
                dupes.append(fc.claim.id)
        res.add(eid, "claims", "Confidence within the evidence cap; evidence ids unique", not over_cap and not dupes,
                f"{len(rep.fact_checks)} verdicts re-calibrated from their evidence, all within cap"
                if not over_cap and not dupes else "; ".join(over_cap + [f"duplicate ids in {d}" for d in dupes]))

        gaps = []
        for fc in rep.fact_checks:
            step = f"verify {fc.claim.id}"
            evs = [e for e in events if e.get("episode_id") == eid and e.get("step") == step]
            actions = sum(1 for e in evs if e.get("kind") == "action")
            has_verdict = any(e.get("kind") in ("verdict", "warn") for e in evs)
            if not evs or not has_verdict or actions != fc.tool_calls:
                gaps.append(f"{fc.claim.id}: {len(evs)} events, {actions}/{fc.tool_calls} tool calls, "
                            f"{'verdict' if has_verdict else 'no verdict'}")
        res.add(eid, "claims", "Every verdict has a complete trace", not gaps,
                f"{len(rep.fact_checks)} claims: tool calls in the trace match the report, each ends in a verdict"
                if not gaps else "; ".join(gaps))
    return res


def render_audit_md(res: AuditResult) -> str:
    c = res.counts
    icon = {"pass": "✅ PASS", "warn": "⚠️ WARN", "fail": "❌ FAIL"}
    lines = [f"# Audit of run `{res.run_id}`", "",
             f"{c['pass']} passed · {c['warn']} warnings · {c['fail']} failed · audited {res.audited_at}", "",
             "| Scope | Area | Check | Result | Detail |", "|---|---|---|---|---|"]
    for ch in res.checks:
        detail = ch.detail.replace("|", "\\|")
        lines.append(f"| {ch.scope} | {ch.area} | {ch.name} | {icon[ch.status]} | {detail} |")
    lines += ["", "Re-run with `podcast-agent audit <run_dir>`. A WARN means the run recorded the issue itself "
              "(for example a summary accepted with a warning) or the check could not be performed.", ""]
    return "\n".join(lines)


def write_audit(res: AuditResult, run_dir: Path) -> None:
    (run_dir / "audit.json").write_text(json.dumps(res.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "audit.md").write_text(render_audit_md(res), encoding="utf-8")
