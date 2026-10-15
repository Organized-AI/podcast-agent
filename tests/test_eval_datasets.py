"""The eval datasets are data the gates depend on, so they get a schema check like code."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = {"verified", "possibly_outdated_or_inaccurate", "unverifiable"}
TYPES = {"factual", "statistical", "historical", "prediction", "anecdotal"}


def _kb_ids() -> set[str]:
    data = json.loads((ROOT / "data" / "knowledge_base.json").read_text())
    entries = data["entries"] if isinstance(data, dict) else data
    return {e["id"] for e in entries}


def test_factcheck_cases_are_well_formed():
    lines = (ROOT / "evals" / "datasets" / "factcheck.jsonl").read_text().splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    assert len(cases) >= 20
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    kb = _kb_ids()
    for c in cases:
        assert c["type"] in TYPES, c["id"]
        assert set(c["acceptable"]) <= VERDICTS and c["expected"] in c["acceptable"], c["id"]
        assert c["claim"].strip() and c["why"].strip(), c["id"]
        assert set(c["kb"]) <= kb, f"{c['id']}: unknown KB ids {set(c['kb']) - kb}"
        assert len(c["today"]) == 10, c["id"]
    tags = {t for c in cases for t in c["tags"]}
    assert {"stale-fact", "passed-deadline", "future-deadline", "no-deadline", "contradicted", "anecdote",
            "verified-fact"} <= tags


def test_episode_expectations_are_well_formed():
    doc = yaml.safe_load((ROOT / "evals" / "datasets" / "episodes.yaml").read_text())
    assert doc["version"] == 1
    for eid, exp in doc["episodes"].items():
        assert (ROOT / exp["transcript"]).exists(), eid
        lo, hi = exp["summary"]["words"]
        assert 0 < lo < hi, eid
        assert all(isinstance(alts, list) and alts for alts in exp["summary"]["mentions"]), eid
        for claim in exp["claims"]:
            assert claim["match"] and set(claim["acceptable"]) <= VERDICTS, (eid, claim)
            assert all(isinstance(w, str) for w in claim["match"]), f"{eid}: quote numbers in match, e.g. \"200\""
        assert all(isinstance(w, str) for alts in exp["summary"]["mentions"] for w in alts), eid
