"""The cost drill: the fault it injects, how it judges the ticket, and the postmortem and follow-ups it writes."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta

import yaml
from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools" / "drill"))
from postmortem import DrillRecord, Episode, PullRequest, plan_followups, render

T0 = datetime(2026, 10, 15, 21, 0, tzinfo=UTC)
PATH = "docs/postmortems/2026-10-15-cost-budget-drill.md"


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _drill():
    spec = importlib.util.spec_from_file_location("cost_drill", ROOT / "tools" / "drill" / "cost_drill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(**overrides) -> DrillRecord:
    r = DrillRecord(
        repo="Organized-AI/podcast-agent", environment="dev", budget_usd=0.15,
        change={"MAX_CLAIMS": ("8", "40"), "MAX_STEPS_PER_CLAIM": ("4", "12")},
        bad_pr=PullRequest(70, "Raise dev's claim budget", "https://github.com/x/pull/70", opened_at=at(-4),
                           ci_passed_at=at(-1), merged_at=at(-0.5), merge_sha="a" * 40),
        revert_pr=PullRequest(72, 'Revert "Raise dev\'s claim budget"', "https://github.com/x/pull/72",
                              opened_at=at(3), ci_passed_at=at(6), merged_at=at(6.2)),
        issue_number=71, issue_url="https://github.com/x/issues/71", issue_opened_at=at(2.2),
        issue_closed_at=at(14), deployed_at=at(0), alert_fired_at=at(2), mitigated_at=at(7),
        recovered_at=at(8), resolved_at=at(13.5),
        baseline=Episode(at(-2), 0.072, 8, 28, "good"),
        over_budget=[Episode(at(1), 0.213, 35, 92, "bad1"), Episode(at(2), 0.214, 35, 92, "bad2"),
                     Episode(at(6.5), 0.213, 35, 92, "bad3")],
        ticket_checks=[(True, "the suspect pull request"), (False, "the runbook")],
        bridge={"mttd_s": 120.0, "mttr_s": 485.0},
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


DEV_VALUES = """# dev: a small cluster
config:
  LLM_PROVIDER: offline
  SEARCH_PROVIDER: mock
  # the canary normally costs about half of this
  COST_BUDGET_USD: "0.15"

secrets:
  optional: true
"""


def test_the_fault_adds_the_budgets_to_devs_config_block():
    # A fixture, not the live values-dev.yaml: on the drill's own pull request that file already has the fault.
    changed = _drill().raise_claim_budget(DEV_VALUES, "40", "12")
    assert yaml.safe_load(changed)["config"] == {**yaml.safe_load(DEV_VALUES)["config"], "MAX_CLAIMS": "40",
                                                 "MAX_STEPS_PER_CLAIM": "12"}
    assert changed.replace('  # Check every claim in long episodes, not just the first eight.\n  MAX_CLAIMS: "40"\n'
                           '  MAX_STEPS_PER_CLAIM: "12"\n', "") == DEV_VALUES


def test_a_drill_cannot_start_while_another_ones_change_is_live():
    import pytest

    with pytest.raises(SystemExit, match="already sets MAX_CLAIMS"):
        _drill().raise_claim_budget('config:\n  MAX_CLAIMS: "40"\n', "40", "12")


def test_the_ticket_check_looks_for_each_piece_of_evidence():
    body = ("**No customer episode has gone over yet.** | Mean cost per episode | $0.21 | `verify_claims` "
            "[Jaeger](x) [MLflow](y) **Suspect: #70 Raise** MAX_CLAIMS revert #70 runbooks/cost-budget.md")
    pr = PullRequest(70, "t", "u")
    assert all(ok for ok, _ in _drill().check_ticket({"body": body}, pr))
    missing = [what for ok, what in _drill().check_ticket({"body": body.replace("[MLflow](y)", "")}, pr) if not ok]
    assert missing == ["the most expensive episode, linked in Jaeger and MLflow"]


def test_derived_numbers():
    r = record()
    assert (r.mttd_s, r.mttr_s, r.ticket_s) == (120, 480, 12)
    assert (r.revert_ci_s, r.time_to_mitigate_s, r.resolve_lag_s) == (180, 300, 330)
    assert round(r.extra_spend, 3) == round(3 * 0.213 + 0.001 - 3 * 0.072, 3)


def test_follow_ups_follow_from_the_measurements():
    titles = [f.title for f in plan_followups(record(), PATH)]
    assert titles == ["Check a config change's cost in CI before it merges",
                      "Revert deploy config faster: CI was most of the time to mitigate",
                      "Resolve the cost alert when the canary recovers, not five minutes later",
                      "Page when live episodes go over budget; keep the canary at ticket"]
    # A merge that waits on a reviewer adds its own follow-up.
    slow_review = record(revert_pr=PullRequest(72, "r", "u", opened_at=at(3), ci_passed_at=at(3.5), merged_at=at(6.5)),
                         resolved_at=at(9))
    assert [f.title for f in plan_followups(slow_review, PATH)][1] == (
        "Make reverting a deploy-config change a one-person action")
    # A quick revert and a quickly resolving alert leave only the two that always apply.
    quick = record(revert_pr=PullRequest(72, "r", "u", opened_at=at(3), ci_passed_at=at(3.5)), resolved_at=at(9))
    assert len(plan_followups(quick, PATH)) == 2
    gate = plan_followups(record(), PATH)[0]
    assert "#71" in gate.body and PATH in gate.body
    assert "the canary went from $0.07 (8 claims) to $0.21 (35 claims) against a $0.15 budget" in gate.body


def test_the_postmortem_tells_the_story_with_the_numbers():
    r = record()
    r.followups = plan_followups(r, PATH)
    for n, f in enumerate(r.followups, 80):
        f.number, f.url = n, f"https://github.com/x/issues/{n}"
    text = render(r, PATH)
    assert text.startswith("# Postmortem: episodes over the cost budget after a claim-budget change (drill)")
    assert "| **MTTD** | **2m 0s** (bad config live → alert) |" in text
    assert "| **MTTR** | **8m 0s** (bad config live → canary back under budget) |" in text
    assert "**Customers: none.** 0 live episodes went over budget" in text
    assert "| Argo CD synced dev: **impact starts** | 2026-10-15T21:00:00Z | 30s after merge |" in text
    assert "| Incident issue #71 opened by the bridge | 2026-10-15T21:02:12Z | 12s after the alert |" in text
    assert "| #72 merged after CI | 2026-10-15T21:06:12Z | CI 3m 0s, merged 12s after |" in text
    assert "MTTD 2m 0s, MTTR 8m 5s (its comment on #71)" in text
    assert "checking 35 claims instead of 8" in text
    assert "35 claims and 92 model calls instead of 8 and 28, $0.21 instead of $0.07" in text
    assert "- ✅ the suspect pull request" in text and "- ❌ the runbook" in text
    assert "1 of 2 evidence checks passed on #71" in text
    assert "- CI on the revert took 3m 0s of the 5m 0s from alert to fix." in text
    assert "- [#80](https://github.com/x/issues/80) Check a config change's cost in CI before it merges" in text
    assert "blameless" in text
