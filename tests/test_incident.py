"""The incident bridge: an alert group becomes one enriched issue, and its resolution closes it with MTTD and MTTR."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from podcast_agent.ops import incident
from podcast_agent.ops.incident import Config, Deploy, EpisodeTrace, GitHub, Sources

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)  # the bad config goes live
BAD_SHA, GOOD_SHA, FIX_SHA = "a" * 40, "b" * 40, "c" * 40


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def payload(status: str = "firing") -> dict[str, Any]:
    labels = {"alertname": "PodcastAgentCostBudgetExceeded", "severity": "ticket",
              "deployment_environment_name": "dev", "podcast_agent_budget_usd": "0.15"}
    alert = {"status": status, "labels": labels, "annotations": {},
             "startsAt": incident.iso(at(2)), "endsAt": incident.iso(at(12.5)) if status == "resolved" else
             "0001-01-01T00:00:00Z", "generatorURL": "http://prometheus/graph?g0.expr=x", "fingerprint": "f1"}
    return {"version": "4", "status": status, "groupKey": '{}:{alertname="PodcastAgentCostBudgetExceeded"}',
            "receiver": "incident-bridge", "groupLabels": {"alertname": labels["alertname"]}, "commonLabels": labels,
            "commonAnnotations": {"summary": "Episodes are costing more than their $0.15 budget",
                                  "description": "2 episode(s) went over budget in the last 5 minutes."},
            "externalURL": "http://alertmanager", "alerts": [alert]}


def episode(minutes: float, cost: float, claims: int, calls: int, trace: str) -> EpisodeTrace:
    return EpisodeTrace(trace_id=trace, start=at(minutes), end=at(minutes) + timedelta(seconds=5),
                        episode="canary-roundtable", traffic="canary", cost=cost, claims=claims, llm_calls=calls)


class FakeSources(Sources):
    def __init__(self, cfg: Config, *, live_over: float = 0.0, down: bool = False):
        super().__init__(cfg, httpx.Client())
        self.live_over, self.down = live_over, down

    def prom(self, query: str) -> list[tuple[dict[str, str], float]]:
        if self.down:
            raise httpx.ConnectError("connection refused")
        if "budget_violations" in query:
            return [({"podcast_agent_traffic": "canary"}, 2.0), ({"podcast_agent_traffic": "live"}, self.live_over)]
        if "episodes_total" in query:
            return [({"podcast_agent_traffic": "canary"}, 15.0), ({"podcast_agent_traffic": "live"}, 3.0)]
        if "episode_cost_usd_sum" in query:
            return [({}, 0.072 if "offset 1h" in query else 0.213)]
        if "podcast_agent_step_name" in query:
            return [({"podcast_agent_step_name": "verify_claims"}, 0.39), ({"podcast_agent_step_name": "summarize"},
                                                                             0.03)]
        if "priced_as" in query:
            return [({"gen_ai_request_model": "offline-heuristic", "podcast_agent_cost_priced_as": "claude-sonnet-5-5"},
                     0.41)]
        return []

    def episodes(self, tags: dict[str, str], start: datetime, end: datetime, limit: int = 40) -> list[EpisodeTrace]:
        if self.down:
            raise httpx.ConnectError("connection refused")
        if tags.get("podcast_agent.cost.over_budget") == "true":
            found = [episode(1, 0.213, 35, 92, "t-bad1"), episode(2, 0.214, 35, 92, "t-bad2"),
                     episode(6.5, 0.213, 35, 92, "t-bad3")]
        else:
            found = [episode(-1, 0.072, 8, 28, "t-good0"), episode(7, 0.072, 8, 28, "t-good1"),
                     episode(8, 0.072, 8, 28, "t-good2")]
        return [e for e in found if start <= e.start <= end]

    def argocd_history(self) -> list[Deploy]:
        if self.down:
            raise httpx.ConnectError("connection refused")
        return [Deploy(GOOD_SHA, at(-30)), Deploy(BAD_SHA, T0), Deploy(FIX_SHA, at(6))]

    def healthz(self) -> dict[str, Any]:
        return {"status": "ok", "version": "0.3.0", "commit": "sha-1234567", "provider": {"provider": "offline"}}


class FakeGitHub(GitHub):
    def __init__(self, cfg: Config):
        super().__init__(cfg, httpx.Client())
        self.issues: list[dict[str, Any]] = []
        self.comments: list[tuple[int, str]] = []

    def find_open_issue(self, key: str) -> dict[str, Any] | None:
        return next((i for i in self.issues if i["state"] == "open"
                     and (incident.read_marker(i["body"]) or {}).get("key") == key), None)

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        issue = {"number": 70 + len(self.issues), "title": title, "body": body, "labels": labels, "state": "open",
                 "created_at": incident.iso(at(2) + timedelta(seconds=10)), "html_url": "https://github.com/x/1"}
        self.issues.append(issue)
        return issue

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))

    def close(self, number: int) -> None:
        next(i for i in self.issues if i["number"] == number)["state"] = "closed"

    def compare(self, base: str, head: str) -> list[dict[str, Any]]:
        assert (base, head) == (GOOD_SHA, BAD_SHA)
        return [{"sha": "d" * 40, "commit": {"message": "Docs tweak", "author": {"name": "Jordan Hill"}}},
                {"sha": BAD_SHA, "commit": {"message": "Raise dev claim budget", "author": {"name": "Jordan Hill"}}}]

    def commit(self, sha: str) -> dict[str, Any]:
        if sha == BAD_SHA:
            patch = '@@ -3,6 +3,8 @@ config:\n   SEARCH_PROVIDER: mock\n+  MAX_CLAIMS: "40"\n+  MAX_STEPS_PER_CLAIM: "12"'
            return {"files": [{"filename": "deploy/helm/podcast-agent/values-dev.yaml", "patch": patch}]}
        return {"files": [{"filename": "README.md", "patch": "+docs"}]}

    def pulls_for(self, sha: str) -> list[dict[str, Any]]:
        return [{"number": 69, "title": "Raise the dev claim budget so long episodes get every claim checked",
                 "user": {"login": "jordan"}, "html_url": "https://github.com/x/pull/69",
                 "merged_at": incident.iso(at(-1)), "labels": [{"name": "drill"}]}]


@pytest.fixture
def cfg() -> Config:
    return Config(repository="Organized-AI/podcast-agent", token="t", labels=["incident"], prometheus="http://p",
                  jaeger="http://j", argocd="http://a", api="http://api", jaeger_ui="http://claws:30686",
                  mlflow_ui="http://claws:30500", argocd_ui="http://claws:30090")


def test_a_firing_alert_opens_one_issue_with_the_evidence(cfg):
    gh = FakeGitHub(cfg)
    result = incident.handle(payload(), cfg, FakeSources(cfg), gh, now=at(2.2))
    assert result["action"] == "opened"
    (issue,) = gh.issues
    body = issue["body"]
    assert issue["title"] == ("[incident] PodcastAgentCostBudgetExceeded in dev: Episodes are costing more than their "
                              "$0.15 budget")
    assert issue["labels"] == ["incident", "severity/ticket", "drill"]
    # Who is affected: only the canary, so customers have not seen it yet.
    assert "**No customer episode has gone over yet.** Only the synthetic canary has: 2 canary" in body
    assert "| Mean cost per episode | $0.21 | $0.07 |" in body
    assert "| `verify_claims` | $0.39 | 93% |" in body
    assert "offline-heuristic (priced as claude-sonnet-5-5)" in body
    # The worst episode and the baseline, linked in both trace backends.
    assert "[Jaeger](http://claws:30686/trace/t-bad2)" in body
    assert "[MLflow](http://claws:30500/#/experiments/0/traces?selectedEvaluationId=tr-t-bad2&traceId=tr-t-bad2)" in body
    assert "checks 35 claims instead of 8 and makes 92 model calls instead of 28" in body
    # What changed: the deploy before the alert, and only the commit that touched deploy config.
    assert f"to `{BAD_SHA[:7]}` at 2026-10-15T12:00:00Z, 2m 0s before the alert (previous deploy: `{GOOD_SHA[:7]}`)" in body
    assert "**Suspect: #69 Raise the dev claim budget so long episodes get every claim checked** by @jordan" in body
    assert '+  MAX_CLAIMS: "40"' in body and "Docs tweak" not in body
    assert "revert #69" in body and "image `sha-1234567`" in body
    assert incident.read_marker(body) == {"key": incident.group_key(payload()), "alertname": "PodcastAgentCostBudgetExceeded",
                                          "started_at": "2026-10-15T12:02:00Z",
                                          "deploy": {"revision": BAD_SHA, "deployed_at": "2026-10-15T12:00:00Z"},
                                          "suspect": 69}


def test_a_redelivered_alert_does_not_open_a_second_issue(cfg):
    gh = FakeGitHub(cfg)
    incident.handle(payload(), cfg, FakeSources(cfg), gh, now=at(2.2))
    assert incident.handle(payload(), cfg, FakeSources(cfg), gh, now=at(3))["action"] == "exists"
    assert len(gh.issues) == 1


def test_live_overruns_say_customers_are_affected(cfg):
    gh = FakeGitHub(cfg)
    incident.handle(payload(), cfg, FakeSources(cfg, live_over=1.2), gh, now=at(2.2))
    assert "**Customers are affected:** 1 live episode(s) went over budget" in gh.issues[0]["body"]


def test_the_ticket_still_opens_when_every_source_is_down(cfg):
    gh = FakeGitHub(cfg)
    incident.handle(payload(), cfg, FakeSources(cfg, down=True), gh, now=at(2.2))
    body = gh.issues[0]["body"]
    assert "<summary>Enrichment notes</summary>" in body
    assert "- Prometheus: ConnectError: connection refused" in body and "- Argo CD: ConnectError" in body
    assert "No deploy found before the alert." in body and "revert the last config change" in body


def test_resolution_comments_the_timeline_and_closes_the_issue(cfg):
    gh = FakeGitHub(cfg)
    incident.handle(payload(), cfg, FakeSources(cfg), gh, now=at(2.2))
    result = incident.handle(payload("resolved"), cfg, FakeSources(cfg), gh, now=at(12.6))
    assert result == {"action": "closed", "issue": 70, "mttd_s": 120.0, "mttr_s": 425.0}
    assert gh.issues[0]["state"] == "closed"
    ((number, comment),) = gh.comments
    assert number == 70
    assert "**MTTD 2m 0s · MTTR 7m 5s**" in comment
    assert f"| Impact starts: Argo CD synced {BAD_SHA[:7]} | 2026-10-15T12:00:00Z |" in comment
    assert "| First episode over budget (`canary-roundtable`, $0.21) | 2026-10-15T12:01:00Z | +1m 0s |" in comment
    assert "| This issue opened | 2026-10-15T12:02:10Z | +10s after the alert |" in comment
    assert f"| Fix live: Argo CD synced `{FIX_SHA[:7]}` | 2026-10-15T12:06:00Z | 4m 0s after the alert |" in comment
    assert "| Last episode over budget (`canary-roundtable`) | 2026-10-15T12:06:30Z |" in comment
    assert "| First canary back under budget ($0.07, 8 claims) | 2026-10-15T12:07:05Z | **MTTR 7m 5s** |" in comment
    assert "| Alert resolved | 2026-10-15T12:12:30Z | after 5m 25s clean" in comment
    timeline = incident.MARKER.pattern  # the machine-readable copy the postmortem reads
    assert "incident-bridge-resolution" in comment and timeline


def test_a_resolution_with_no_open_issue_changes_nothing(cfg):
    gh = FakeGitHub(cfg)
    assert incident.handle(payload("resolved"), cfg, FakeSources(cfg), gh, now=at(13)) == {"action": "nothing-open"}
    assert not gh.comments


def test_without_a_token_the_webhook_runs_dry():
    dry = Config(repository="Organized-AI/podcast-agent", token=None)
    assert dry.dry_run
    client = TestClient(incident.create_app(dry))
    assert client.get("/healthz").json() == {"status": "ok", "repository": "Organized-AI/podcast-agent",
                                             "dry_run": True}
    resp = client.post("/alertmanager", json=payload())
    assert resp.status_code == 200
    assert resp.json()["action"] == "opened" and resp.json()["issue"] == 0


@pytest.mark.parametrize(("seconds", "text"), [(None, "–"), (9.6, "10s"), (125, "2m 5s"), (3720, "1h 2m"),
                                               (-30, "-30s")])
def test_durations_read_like_a_person_wrote_them(seconds, text):
    assert incident.duration(seconds) == text
