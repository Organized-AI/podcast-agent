"""The eval runner: metrics, the fact-check section, scoring a run folder, thresholds and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import ScriptedProvider, make_ctx

from podcast_agent.cli import main
from podcast_agent.evals import metrics as m
from podcast_agent.evals.datasets import load_episode_expectations, load_factcheck_cases
from podcast_agent.evals.runner import apply_thresholds, render_markdown, run_factcheck, run_suite, score_run

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "datasets" / "factcheck.jsonl"
EPISODES = ROOT / "evals" / "datasets" / "episodes.yaml"


def test_metrics():
    assert m.accuracy([True, False, True, True]) == 0.75
    assert m.macro_f1(["a", "a", "b"], ["a", "b", "b"], labels=["a", "b"]) == pytest.approx((2 / 3 + 2 / 3) / 2)
    assert m.macro_f1(["a"], ["a"], labels=["a", "b"]) == 1.0  # labels absent from both are skipped
    assert m.brier([0.9, 0.2], [True, False]) == pytest.approx((0.01 + 0.04) / 2)
    assert m.expected_calibration_error([0.9, 0.9], [True, False], bins=5) == pytest.approx(0.4)
    assert m.mention_coverage("GitLab writes handbooks", [["handbook"], ["async"], ["gitlab", "doist"]]) == pytest.approx(2 / 3)
    assert m.matches_all("Bitcoin will reach $200K by 2025.", ["bitcoin", "200"])
    assert not m.matches_all("Bitcoin will reach $200K", ["bitcoin", "2040"])


def test_factcheck_section_scores_the_offline_verifier():
    cases = load_factcheck_cases(CASES)
    section = run_factcheck(make_ctx(), cases, max_steps=4, tools=["search_knowledge_base", "web_search"])
    assert section.metrics["cases"] == len(cases)
    assert section.metrics["verdict_accuracy"] >= 0.9
    assert section.metrics["accuracy.future-deadline"] == 1.0
    assert section.metrics["citation_validity"] == 1.0
    row = next(r for r in section.rows if r["id"] == "perseverance-landing")
    assert row["correct"] and "KB-031" in row["cited_kb"]


def test_a_wrong_verdict_is_scored_wrong_and_fails_its_threshold():
    case = next(c for c in load_factcheck_cases(CASES) if c.id == "perseverance-landing")
    wrong = {"purpose": "x", "action": "finish",
             "input": {"verdict": "possibly_outdated_or_inaccurate", "confidence": 0.9, "rationale": "r", "evidence_ids": []}}
    section = run_factcheck(make_ctx(ScriptedProvider({"verify_step": [wrong]})), [case], max_steps=4,
                            tools=["search_knowledge_base"])
    assert section.rows[0]["verdict"] == "possibly_outdated_or_inaccurate" and not section.rows[0]["correct"]
    assert section.metrics["citation_validity"] == 0.0  # an assertion with no tool evidence
    assert section.rows[0]["confidence"] == 0.6  # capped: no tool evidence
    apply_thresholds(section, {"verdict_accuracy": 0.9, "brier": {"max": 0.3}})  # (0.6 - 0)² = 0.36
    by_metric = {c.metric: c.passed for c in section.checks}
    assert by_metric == {"verdict_accuracy": False, "brier": False}


def test_scoring_the_committed_claude_run_finds_the_issue_13_miss():
    section = score_run(ROOT / "samples" / "claude-run", load_episode_expectations(EPISODES), root=ROOT)
    assert section.metrics["episodes"] == 4
    assert section.metrics["quote_fidelity"] == 1.0 and section.metrics["claim_recall"] == 1.0
    misses = [(r["episode"], c["match"]) for r in section.rows for c in r["claims"] if not c["correct"]]
    assert misses == [("ep004", ["Mars", "2040"])]
    assert "Mars + 2040 → possibly_outdated_or_inaccurate" in render_markdown_section(section)


def render_markdown_section(section):
    from podcast_agent.evals.runner import SuiteResult

    return render_markdown(SuiteResult("t", "", "none", [section]))


def test_unknown_metric_in_thresholds_is_an_error():
    section = score_run(ROOT / "samples" / "claude-run", load_episode_expectations(EPISODES), root=ROOT)
    with pytest.raises(ValueError, match="no metric named"):
        apply_thresholds(section, {"accuracy": 1.0})


def _suite(tmp_path, threshold: float) -> Path:
    path = tmp_path / "suite.yaml"
    path.write_text(
        "name: t\nruns:\n  - name: claude-run\n    path: samples/claude-run\n"
        f"    expectations: evals/datasets/episodes.yaml\n    thresholds: {{claim_verdict_accuracy: {threshold}}}\n"
    )
    return path


def test_run_suite_without_a_model_section(tmp_path):
    result = run_suite(_suite(tmp_path, 0.9), root=ROOT)
    assert result.passed and result.provider == "provider=none"
    assert not run_suite(_suite(tmp_path, 1.0), root=ROOT).passed


def test_cli_exit_code_follows_the_thresholds(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    out = tmp_path / "reports"
    assert main(["eval", "--suite", str(_suite(tmp_path, 0.9)), "--out", str(out)]) == 0
    assert json.loads((out / "eval.json").read_text())["passed"] is True
    assert main(["eval", "--suite", str(_suite(tmp_path, 1.0))]) == 1
    assert main(["eval", "--score", "samples/offline-mode"]) == 0  # scoring alone never gates
