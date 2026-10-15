"""Cost per call and per episode, the episode budget in warn and enforce mode, and the metrics the alert reads."""

from __future__ import annotations

import pytest
from conftest import ROOT, make_ctx
from test_telemetry import _counter_total, _metric_points

from podcast_agent import cost, telemetry
from podcast_agent.agent.pipeline import process_episode
from podcast_agent.config import Settings
from podcast_agent.llm.base import LLMResponse
from podcast_agent.models import Verdict
from podcast_agent.transcript import load_episode

CANARY = ROOT / "data" / "canary" / "canary_roundtable.json"


@pytest.mark.parametrize(
    ("model", "tier", "expected"),
    [
        ("claude-sonnet-5-5", "main", "claude-sonnet-5-5"),
        ("claude-haiku-5-5-20261001", "fast", "claude-haiku-5-5"),  # a dated snapshot has its alias's price
        ("offline-heuristic", "fast", "claude-haiku-5-5"),  # no price of its own: the tier's Claude model
        ("llama3.1:8b", "main", "claude-sonnet-5-5"),
    ],
)
def test_a_model_without_a_price_is_priced_as_its_tiers_claude_model(model, tier, expected):
    assert cost.priced_as(model, tier) == expected


def test_call_cost_uses_input_output_and_cache_read_prices():
    usd, priced = cost.call_cost("claude-sonnet-5-5", "main", 1_000_000, 100_000, 1_000_000)
    assert priced == "claude-sonnet-5-5"
    assert usd == pytest.approx(2.00 + 1.00 + 0.10)


def test_usage_adds_up_by_step_and_skips_response_cache_hits():
    ctx = make_ctx()
    ctx.record_usage(LLMResponse(text="", model="claude-haiku-5-5", input_tokens=10_000, output_tokens=2_000),
                     step="extract_claims", tier="fast")
    ctx.record_usage(LLMResponse(text="", model="claude-sonnet-5-5", input_tokens=10_000, output_tokens=1_000),
                     step="verify_claims", tier="main")
    ctx.record_usage(LLMResponse(text="", model="claude-sonnet-5-5", input_tokens=10_000, output_tokens=1_000,
                                 cached=True), step="verify_claims", tier="main")
    assert ctx.usage.cost_by_step == {"extract_claims": pytest.approx(0.002), "verify_claims": pytest.approx(0.03)}
    assert ctx.usage.cost_usd == pytest.approx(0.032)
    assert ctx.usage.cached_calls == 1


def test_budget_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("COST_BUDGET_USD", "off")
    monkeypatch.setenv("COST_BUDGET_MODE", "enforce")
    s = Settings()
    assert s.cost_budget_usd is None
    assert s.cost_budget_mode == "enforce"
    monkeypatch.setenv("COST_BUDGET_MODE", "block")
    with pytest.raises(ValueError, match="warn or enforce"):
        Settings()


def _canary(**overrides):
    ctx = make_ctx(**overrides)
    return ctx, process_episode(ctx, load_episode(CANARY), "cost-test")


def test_the_canary_fits_the_dev_budget_at_the_default_claim_budget():
    _, report = _canary(max_claims=8, cost_budget_usd=0.15)
    assert len(report.fact_checks) == 8
    assert 0.05 < report.usage.cost_usd < 0.15
    assert not report.usage.over_budget and report.usage.budget_usd == 0.15
    # Verification is most of the bill, as docs/DEPLOYMENT.md says.
    assert report.usage.cost_by_step["verify_claims"] > report.usage.cost_usd / 2


def test_raising_the_claim_budget_takes_the_canary_over_budget_and_warns():
    _, report = _canary(max_claims=40, cost_budget_usd=0.15)
    assert len(report.fact_checks) > 30
    assert report.usage.cost_usd > 0.15 and report.usage.over_budget
    assert any("over the $0.15 episode budget (warn mode)" in w for w in report.warnings)
    assert all(fc.verdict for fc in report.fact_checks)  # warn mode checks every claim


def test_enforce_mode_stops_verifying_once_the_budget_is_spent():
    _, report = _canary(max_claims=40, cost_budget_usd=0.15, cost_budget_mode="enforce")
    skipped = [fc for fc in report.fact_checks if fc.rationale.startswith("Not checked: the episode reached")]
    assert skipped and all(fc.verdict == Verdict.UNVERIFIABLE and fc.confidence == 0.2 for fc in skipped)
    assert skipped == report.fact_checks[-len(skipped):]  # every claim after the budget ran out, none before
    _, unbounded = _canary(max_claims=40, cost_budget_usd=0.15)
    assert report.usage.cost_usd < unbounded.usage.cost_usd


def test_no_budget_means_no_overrun():
    _, report = _canary(max_claims=40, cost_budget_usd=None)
    assert report.usage.budget_usd is None and not report.usage.over_budget


def test_an_over_budget_episode_is_counted_and_marked_on_its_span(otel):
    exporter, reader = otel
    exporter.clear()
    telemetry.start_budget_series(0.15, "warn")
    before = _metric_points(reader)
    assert {(p.attributes["podcast_agent.traffic"], p.attributes["podcast_agent.budget.usd"])
            for p in before["podcast_agent.budget.violations"]} >= {("live", "0.15"), ("canary", "0.15")}

    _, report = _canary(max_claims=40, cost_budget_usd=0.15)
    after = _metric_points(reader)
    added = (_counter_total(after, "podcast_agent.budget.violations")
             - _counter_total(before, "podcast_agent.budget.violations"))
    assert added == 1
    spend = _counter_total(after, "podcast_agent.cost.usd") - _counter_total(before, "podcast_agent.cost.usd")
    assert spend == pytest.approx(report.usage.cost_usd, rel=1e-3)
    steps = {p.attributes.get("podcast_agent.step.name") for p in after["podcast_agent.cost.usd"]}
    assert {"plan", "summarize", "extract_claims", "verify_claims"} <= steps
    assert {p.attributes["podcast_agent.cost.priced_as"] for p in after["podcast_agent.cost.usd"]} <= set(cost.PRICES)
    assert after["podcast_agent.episode.cost.usd"]

    (span,) = [s for s in exporter.get_finished_spans() if s.name == "invoke_agent podcast-agent"]
    assert span.attributes["podcast_agent.traffic"] == "canary"
    assert span.attributes["podcast_agent.cost.over_budget"] is True
    assert span.attributes["podcast_agent.cost.usd"] == pytest.approx(report.usage.cost_usd)
    (event,) = [e for e in span.events if e.name == "podcast_agent.budget_exceeded"]
    assert event.attributes["podcast_agent.budget.usd"] == "0.15"


def test_the_canary_reports_its_cost_through_the_api(monkeypatch, data_dir, capsys):
    from fastapi.testclient import TestClient

    from podcast_agent import server
    from podcast_agent.ops import canary

    for key, value in {"LLM_PROVIDER": "offline", "KB_PATH": str(data_dir / "knowledge_base.json"),
                       "MOCK_WEB_PATH": str(data_dir / "mock_web_index.json"), "AGENT_TODAY": "2026-10-13",
                       "LLM_CACHE": "false", "MAX_CLAIMS": "40", "COST_BUDGET_USD": "0.15"}.items():
        monkeypatch.setenv(key, value)
    server._shared.cache_clear()
    client = TestClient(server.app)
    monkeypatch.setattr(canary.httpx, "post", lambda url, json, timeout: client.post("/v1/process", json=json))
    try:
        assert canary.main(["--url", "http://api", "--transcript", str(CANARY)]) == 0
    finally:
        server._shared.cache_clear()
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["ok"] and result["episode"] == "canary-roundtable" and result["claims"] > 30
    assert result["over_budget"] is True and result["budget_usd"] == 0.15 and result["cost_usd"] > 0.15


def test_the_canary_fails_loudly_when_the_api_is_down(monkeypatch, capsys):
    import httpx

    from podcast_agent.ops import canary

    def refuse(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(canary.httpx, "post", refuse)
    assert canary.main(["--url", "http://api", "--transcript", str(CANARY)]) == 1
    assert '"ok": false' in capsys.readouterr().out
