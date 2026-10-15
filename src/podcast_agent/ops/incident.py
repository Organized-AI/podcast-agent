"""Incident bridge: an Alertmanager webhook that opens an enriched GitHub issue, then closes it with the timeline.

    podcast-agent incident-bridge --port 8080     # dev runs it beside the API; Alertmanager posts to /alertmanager

An alert says that something is wrong. The ticket should also say how bad, since when, where the money or the errors go
and what changed, so whoever picks it up starts from the evidence instead of five dashboards. For each alert group that
starts firing the bridge gathers, every source optional:

* Prometheus: episodes over budget (canary and live apart), cost per episode now and an hour earlier, spend by step
  and by model;
* Jaeger: the most expensive over-budget episode and the last episode under budget, linked in Jaeger and MLflow;
* Argo CD: the deploy just before the alert; GitHub: the pull request behind it and its diff;
* the API's /healthz: what is running.

It opens one issue per alert group; a marker in the body makes a redelivered webhook a no-op. When the group resolves
it comments the timeline (impact start, detection, ticket, mitigation, recovery) with MTTD and MTTR, and closes the
issue. With no GITHUB_TOKEN it runs dry and logs the issue it would have opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import anyio
import httpx
from fastapi import FastAPI, Request

log = logging.getLogger("incident-bridge")
T = TypeVar("T")
MARKER = re.compile(r"<!-- incident-bridge (\{.*?\}) -->", re.S)
EPISODE_OP = "invoke_agent podcast-agent"
DEPLOY_PATHS = ("deploy/", "config/")
MAX_SUSPECTS = 3  # newest first; a long list buries the one that matters


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


@dataclass
class Config:
    repository: str = field(default_factory=lambda: _env("GITHUB_REPOSITORY", "Organized-AI/podcast-agent") or "")
    token: str | None = field(default_factory=lambda: _env("GITHUB_TOKEN"), repr=False)
    github_api: str = field(default_factory=lambda: _env("GITHUB_API_URL", "https://api.github.com") or "")
    labels: list[str] = field(default_factory=lambda: (_env("INCIDENT_LABELS", "incident") or "").split(","))
    window: str = field(default_factory=lambda: _env("INCIDENT_WINDOW", "15m") or "15m")
    # In-cluster addresses the bridge queries ...
    prometheus: str | None = field(default_factory=lambda: _env("PROMETHEUS_URL"))
    jaeger: str | None = field(default_factory=lambda: _env("JAEGER_URL"))
    argocd: str | None = field(default_factory=lambda: _env("ARGOCD_URL"))
    argocd_app: str = field(default_factory=lambda: _env("ARGOCD_APP", "podcast-agent-dev") or "")
    api: str | None = field(default_factory=lambda: _env("API_URL"))
    # ... and the addresses people open from the issue (tailnet-only in the lab).
    prometheus_ui: str | None = field(default_factory=lambda: _env("PROMETHEUS_UI_URL"))
    jaeger_ui: str | None = field(default_factory=lambda: _env("JAEGER_UI_URL"))
    mlflow_ui: str | None = field(default_factory=lambda: _env("MLFLOW_UI_URL"))
    mlflow_experiment: str = field(default_factory=lambda: _env("MLFLOW_EXPERIMENT_ID", "0") or "0")
    argocd_ui: str | None = field(default_factory=lambda: _env("ARGOCD_UI_URL"))
    alertmanager_ui: str | None = field(default_factory=lambda: _env("ALERTMANAGER_UI_URL"))
    runbook: str = field(default_factory=lambda: _env(
        "RUNBOOK_URL", "https://github.com/Organized-AI/podcast-agent/blob/main/docs/runbooks/cost-budget.md") or "")

    @property
    def dry_run(self) -> bool:
        return not self.token


# --------------------------------------------------------------------------- time helpers


def parse_time(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-"):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if value else None


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = round(seconds)
    h, rest = divmod(abs(s), 3600)
    m, sec = divmod(rest, 60)
    text = f"{h}h {m}m" if h else (f"{m}m {sec}s" if m else f"{sec}s")
    return f"-{text}" if s < 0 else text


def between(start: datetime | None, end: datetime | None) -> float | None:
    return (end - start).total_seconds() if start and end else None


def money(value: float | None) -> str:
    if value is None:
        return "–"
    return f"${value:.4f}" if abs(value) < 0.01 else f"${value:.2f}"


# --------------------------------------------------------------------------- evidence


@dataclass
class EpisodeTrace:
    trace_id: str
    start: datetime
    end: datetime
    episode: str
    traffic: str
    cost: float | None
    claims: int | None
    llm_calls: int | None


@dataclass
class Deploy:
    revision: str
    deployed_at: datetime | None


@dataclass
class Change:
    sha: str
    pr: int | None
    title: str
    author: str
    url: str
    merged_at: datetime | None
    labels: list[str]
    files: list[dict[str, Any]]


@dataclass
class Evidence:
    over_budget: dict[str, float] = field(default_factory=dict)  # traffic → episodes over budget in the window
    episodes: dict[str, float] = field(default_factory=dict)  # traffic → episodes in the window
    mean_cost_now: float | None = None
    mean_cost_before: float | None = None
    spend_by_step: list[tuple[str, float]] = field(default_factory=list)
    spend_by_model: list[tuple[str, float]] = field(default_factory=list)
    worst: EpisodeTrace | None = None
    baseline: EpisodeTrace | None = None
    deploy: Deploy | None = None
    previous_deploy: Deploy | None = None
    changes: list[Change] = field(default_factory=list)
    running: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)  # sources that could not be read


class Sources:
    """Read-only queries against Prometheus, Jaeger, Argo CD and the API. Every method may raise; gather() copes."""

    def __init__(self, cfg: Config, client: httpx.Client):
        self.cfg, self.http = cfg, client

    def _get(self, url: str, **params: Any) -> Any:
        resp = self.http.get(url, params=params or None)
        resp.raise_for_status()
        return resp.json()

    def prom(self, query: str) -> list[tuple[dict[str, str], float]]:
        if not self.cfg.prometheus:
            raise RuntimeError("PROMETHEUS_URL is not set")
        data = self._get(f"{self.cfg.prometheus}/api/v1/query", query=query)["data"]["result"]
        return [(r["metric"], float(r["value"][1])) for r in data if r["value"][1] not in ("NaN", "+Inf", "-Inf")]

    def episodes(self, tags: dict[str, str], start: datetime, end: datetime, limit: int = 40) -> list[EpisodeTrace]:
        """Episodes (invoke_agent spans) in [start, end] whose attributes match `tags`, oldest first.

        Jaeger's API v3 search; attributes are matched here, because its search does not filter on them.
        """
        if not self.cfg.jaeger:
            raise RuntimeError("JAEGER_URL is not set")
        resp = self.http.get(f"{self.cfg.jaeger}/api/v3/traces", params={
            "query.service_name": "podcast-agent", "query.operation_name": EPISODE_OP,
            "query.start_time_min": iso(start), "query.start_time_max": iso(end), "query.search_depth": limit})
        if resp.status_code == 404:  # "No traces found"
            return []
        resp.raise_for_status()
        spans = [span for rs in resp.json().get("result", {}).get("resourceSpans", [])
                 for ss in rs.get("scopeSpans", []) for span in ss.get("spans", [])]
        found = []
        for span in spans:
            if span.get("name") != EPISODE_OP:
                continue
            attrs = {a["key"]: _otlp_value(a.get("value", {})) for a in span.get("attributes", [])}
            if all(str(attrs.get(k)).lower() == v for k, v in tags.items()):
                found.append(_episode(span, attrs))
        return sorted(found, key=lambda e: e.start)

    def argocd_history(self) -> list[Deploy]:
        if not self.cfg.argocd:
            raise RuntimeError("ARGOCD_URL is not set")
        app = self._get(f"{self.cfg.argocd}/api/v1/applications/{self.cfg.argocd_app}")
        history = app.get("status", {}).get("history") or []
        return [Deploy(revision=h.get("revision", ""), deployed_at=parse_time(h.get("deployedAt"))) for h in history]

    def healthz(self) -> dict[str, Any]:
        if not self.cfg.api:
            raise RuntimeError("API_URL is not set")
        result: dict[str, Any] = self._get(f"{self.cfg.api}/healthz")
        return result


def _otlp_value(value: dict[str, Any]) -> Any:
    for kind in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if kind in value:
            return value[kind]
    return None


def _episode(span: dict[str, Any], attrs: dict[str, Any]) -> EpisodeTrace:
    return EpisodeTrace(
        trace_id=span["traceId"],
        start=datetime.fromtimestamp(int(span["startTimeUnixNano"]) / 1e9, UTC),
        end=datetime.fromtimestamp(int(span["endTimeUnixNano"]) / 1e9, UTC),
        episode=str(attrs.get("podcast_agent.episode.id", "?")), traffic=str(attrs.get("podcast_agent.traffic", "?")),
        cost=_num(attrs.get("podcast_agent.cost.usd")), claims=_int(attrs.get("podcast_agent.claims")),
        llm_calls=_int(attrs.get("podcast_agent.llm.calls")),
    )


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


class GitHub:
    """The few REST calls the bridge needs. Dry run (no token) logs writes instead of making them."""

    def __init__(self, cfg: Config, client: httpx.Client):
        self.cfg, self.http = cfg, client
        self.base = f"{cfg.github_api}/repos/{cfg.repository}"
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if cfg.token:
            self.headers["Authorization"] = f"Bearer {cfg.token}"

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self.http.request(method, f"{self.base}{path}", headers=self.headers, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def find_open_issue(self, key: str) -> dict[str, Any] | None:
        if self.cfg.dry_run:
            return None
        label = self.cfg.labels[0]
        for issue in self._call("GET", "/issues", params={"state": "open", "labels": label, "per_page": 100}):
            state = read_marker(issue.get("body") or "")
            if state and state.get("key") == key:
                return dict(issue)
        return None

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        if self.cfg.dry_run:
            log.warning("dry run (no GITHUB_TOKEN): would open %r with labels %s\n%s", title, labels, body)
            return {"number": 0, "html_url": "", "created_at": iso(datetime.now(UTC))}
        issue: dict[str, Any] = self._call("POST", "/issues", json={"title": title, "body": body, "labels": labels})
        return issue

    def comment(self, number: int, body: str) -> None:
        if self.cfg.dry_run:
            log.warning("dry run: would comment on #%s\n%s", number, body)
            return
        self._call("POST", f"/issues/{number}/comments", json={"body": body})

    def close(self, number: int) -> None:
        if self.cfg.dry_run:
            log.warning("dry run: would close #%s", number)
            return
        self._call("PATCH", f"/issues/{number}", json={"state": "closed", "state_reason": "completed"})

    def compare(self, base: str, head: str) -> list[dict[str, Any]]:
        commits: list[dict[str, Any]] = self._call("GET", f"/compare/{base}...{head}").get("commits", [])
        return commits

    def commit(self, sha: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", f"/commits/{sha}")
        return result

    def pulls_for(self, sha: str) -> list[dict[str, Any]]:
        pulls: list[dict[str, Any]] = self._call("GET", f"/commits/{sha}/pulls")
        return pulls


# --------------------------------------------------------------------------- gather


def group_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(str(payload.get("groupKey", "")).encode()).hexdigest()[:16]


def started_at(payload: dict[str, Any]) -> datetime | None:
    starts = [t for a in payload.get("alerts", []) if (t := parse_time(a.get("startsAt")))]
    return min(starts) if starts else None


def ended_at(payload: dict[str, Any]) -> datetime | None:
    ends = [t for a in payload.get("alerts", []) if (t := parse_time(a.get("endsAt")))]
    return max(ends) if ends else None


def selector(labels: dict[str, str]) -> str:
    env = labels.get("deployment_environment_name")
    return f'{{deployment_environment_name="{env}"}}' if env else ""


def _attempt(ev: Evidence, what: str, fn: Callable[[], T]) -> T | None:
    try:
        return fn()
    except Exception as exc:  # one missing source must not stop the ticket
        ev.notes.append(f"{what}: {type(exc).__name__}: {str(exc)[:160]}")
        log.warning("enrichment: %s failed: %s", what, exc)
        return None


def gather(payload: dict[str, Any], cfg: Config, src: Sources, gh: GitHub, now: datetime) -> Evidence:
    ev = Evidence()
    labels = payload.get("commonLabels", {})
    sel, w = selector(labels), cfg.window
    start = started_at(payload) or now

    def by(metric_query: str, label: str) -> dict[str, float]:
        return {m.get(label, "?"): v for m, v in src.prom(metric_query)}

    ev.over_budget = _attempt(ev, "Prometheus", lambda: by(
        f"sum by (podcast_agent_traffic) (increase(podcast_agent_budget_violations_total{sel}[{w}]))",
        "podcast_agent_traffic")) or {}
    if not ev.notes:
        ev.episodes = _attempt(ev, "Prometheus episodes", lambda: by(
            f"sum by (podcast_agent_traffic) (increase(podcast_agent_episodes_total{sel}[{w}]))",
            "podcast_agent_traffic")) or {}
        def mean(offset: str = "") -> str:
            return (f"sum(increase(podcast_agent_episode_cost_usd_sum{sel}[{w}]{offset}))"
                    f" / sum(increase(podcast_agent_episode_cost_usd_count{sel}[{w}]{offset}))")

        ev.mean_cost_now = _first(_attempt(ev, "Prometheus mean cost", lambda: src.prom(mean())))
        ev.mean_cost_before = _first(_attempt(ev, "Prometheus mean cost an hour earlier",
                                              lambda: src.prom(mean(" offset 1h"))))
        steps = _attempt(ev, "Prometheus spend by step", lambda: src.prom(
            f"sum by (podcast_agent_step_name) (increase(podcast_agent_cost_usd_total{sel}[{w}]))"))
        ev.spend_by_step = sorted(((m.get("podcast_agent_step_name", "?"), v) for m, v in steps or []),
                                  key=lambda kv: -kv[1])
        models = _attempt(ev, "Prometheus spend by model", lambda: src.prom(
            f"sum by (gen_ai_request_model, podcast_agent_cost_priced_as) "
            f"(increase(podcast_agent_cost_usd_total{sel}[{w}]))"))
        ev.spend_by_model = sorted(
            ((_model_name(m), v) for m, v in models or []), key=lambda kv: -kv[1])

    window_start = now - timedelta(hours=1)
    over = _attempt(ev, "Jaeger", lambda: src.episodes({"podcast_agent.cost.over_budget": "true"}, window_start, now))
    if over:
        ev.worst = max(over, key=lambda e: e.cost or 0.0)
    history = _attempt(ev, "Argo CD", src.argocd_history) or []
    before = [d for d in history if d.deployed_at and d.deployed_at <= start]
    if before:
        ev.deploy = max(before, key=lambda d: d.deployed_at or now)
        earlier = [d for d in before if d is not ev.deploy and d.revision != ev.deploy.revision]
        ev.previous_deploy = max(earlier, key=lambda d: d.deployed_at or now) if earlier else None
    good_until = ev.deploy.deployed_at if ev.deploy and ev.deploy.deployed_at else start
    under = _attempt(ev, "Jaeger baseline", lambda: src.episodes(
        {"podcast_agent.cost.over_budget": "false", "podcast_agent.traffic": "canary"},
        good_until - timedelta(hours=1), good_until))
    if under:
        ev.baseline = under[-1]
    deploy, previous = ev.deploy, ev.previous_deploy
    if deploy and previous:
        ev.changes = _attempt(ev, "GitHub", lambda: suspect_changes(gh, previous, deploy)) or []
    ev.running = _attempt(ev, "API /healthz", src.healthz)
    return ev


def _first(rows: list[tuple[dict[str, str], float]] | None) -> float | None:
    return rows[0][1] if rows else None


def _model_name(metric: dict[str, str]) -> str:
    model, priced = metric.get("gen_ai_request_model", "?"), metric.get("podcast_agent_cost_priced_as", "")
    return model if not priced or priced == model else f"{model} (priced as {priced})"


def suspect_changes(gh: GitHub, previous: Deploy, current: Deploy) -> list[Change]:
    """Commits between the two deploys that touched deployment config, newest first, with their pull requests."""
    out = []
    for c in reversed(gh.compare(previous.revision, current.revision)):
        detail = gh.commit(c["sha"])
        files = [f for f in detail.get("files", []) if f.get("filename", "").startswith(DEPLOY_PATHS)]
        if not files:
            continue
        pulls = gh.pulls_for(c["sha"])
        pr = pulls[0] if pulls else {}
        out.append(Change(
            sha=c["sha"], pr=pr.get("number"), title=pr.get("title") or c["commit"]["message"].split("\n")[0],
            author=(pr.get("user") or c.get("author") or {}).get("login", c["commit"]["author"]["name"]),
            url=pr.get("html_url") or c.get("html_url", ""), merged_at=parse_time(pr.get("merged_at")),
            labels=[lab["name"] for lab in pr.get("labels", [])], files=files,
        ))
    return out


# --------------------------------------------------------------------------- render


def jaeger_link(cfg: Config, trace_id: str) -> str | None:
    return f"{cfg.jaeger_ui}/trace/{trace_id}" if cfg.jaeger_ui else None


def mlflow_link(cfg: Config, trace_id: str) -> str | None:
    if not cfg.mlflow_ui:
        return None
    tr = f"tr-{trace_id}"
    return f"{cfg.mlflow_ui}/#/experiments/{cfg.mlflow_experiment}/traces?selectedEvaluationId={tr}&traceId={tr}"


def _links(cfg: Config, trace_id: str) -> str:
    parts = [f"[{name}]({url})" for name, url in (("Jaeger", jaeger_link(cfg, trace_id)),
                                                  ("MLflow", mlflow_link(cfg, trace_id))) if url]
    return " · ".join(parts) or f"`{trace_id}`"


def _episode_line(cfg: Config, e: EpisodeTrace) -> str:
    return (f"`{e.episode}` ({e.traffic}) at {e.start:%H:%M:%S} UTC: **{money(e.cost)}**, {e.claims} claims, "
            f"{e.llm_calls} model calls · {_links(cfg, e.trace_id)}")


def _patch(files: list[dict[str, Any]], max_lines: int = 40) -> str:
    lines: list[str] = []
    for f in files:
        lines.append(f"--- {f['filename']}")
        lines += (f.get("patch") or "(binary or large file: no patch)").splitlines()
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"… {len(lines) - max_lines} more lines"]
    return "```diff\n" + "\n".join(lines) + "\n```"


def render_issue(payload: dict[str, Any], ev: Evidence, cfg: Config) -> tuple[str, str, list[str]]:
    labels = payload.get("commonLabels", {})
    notes = payload.get("commonAnnotations", {})
    alertname = labels.get("alertname", "alert")
    env = labels.get("deployment_environment_name", "")
    budget = labels.get("podcast_agent_budget_usd")
    start = started_at(payload)
    summary = notes.get("summary", alertname)
    title = f"[incident] {alertname}{f' in {env}' if env else ''}: {summary}"

    live, canary = round(ev.over_budget.get("live", 0)), round(ev.over_budget.get("canary", 0))
    if live:
        impact = f"**Customers are affected:** {live} live episode(s) went over budget in the last {cfg.window}."
    elif canary:
        impact = (f"**No customer episode has gone over yet.** Only the synthetic canary has: {canary} canary "
                  f"episode(s) over budget in the last {cfg.window}, 0 live.")
    else:
        impact = "Prometheus shows no over-budget episode in the window; the alert may be resolving."

    out = [f"## {alertname}{f' · {env}' if env else ''}", "",
           f"**{summary}.** {notes.get('description', '').strip()}", "", impact, "",
           f"Firing since {iso(start) or '?'}"
           + (f" · budget ${budget} per episode" if budget else "")
           + (f" · [alert]({payload['alerts'][0].get('generatorURL')})" if payload.get("alerts") else ""), ""]

    out += ["### Cost", "", f"| | Last {cfg.window} | An hour earlier |", "|---|---|---|",
            f"| Mean cost per episode | {money(ev.mean_cost_now)} | {money(ev.mean_cost_before)} |",
            f"| Episodes (canary / live) | {round(ev.episodes.get('canary', 0))} / {round(ev.episodes.get('live', 0))}"
            f" | |",
            f"| Over budget (canary / live) | {canary} / {live} | |", ""]
    if ev.spend_by_step:
        total = sum(v for _, v in ev.spend_by_step) or 1.0
        out += ["| Step | Spend | Share |", "|---|---|---|"]
        out += [f"| `{k}` | {money(v)} | {v / total:.0%} |" for k, v in ev.spend_by_step]
        out.append("")
    if ev.spend_by_model:
        out += ["| Model | Spend |", "|---|---|"] + [f"| {k} | {money(v)} |" for k, v in ev.spend_by_model] + [""]

    out += ["### Episodes", ""]
    if ev.worst:
        out.append(f"- Most expensive over budget: {_episode_line(cfg, ev.worst)}")
    if ev.baseline:
        out.append(f"- Last canary under budget before the deploy: {_episode_line(cfg, ev.baseline)}")
    if ev.worst and ev.baseline and ev.worst.claims and ev.baseline.claims:
        out.append(f"- The same canary now checks {ev.worst.claims} claims instead of {ev.baseline.claims} and makes "
                   f"{ev.worst.llm_calls} model calls instead of {ev.baseline.llm_calls}.")
    if not (ev.worst or ev.baseline):
        out.append("- No traces found (see the notes below).")
    out.append("")

    out += ["### What changed", ""]
    if ev.deploy:
        ago = between(ev.deploy.deployed_at, start)
        out.append(f"Argo CD synced `{cfg.argocd_app}` to `{ev.deploy.revision[:7]}` at {iso(ev.deploy.deployed_at)}, "
                   f"{duration(ago)} before the alert"
                   + (f" (previous deploy: `{ev.previous_deploy.revision[:7]}`)." if ev.previous_deploy else "."))
        if cfg.argocd_ui:
            out[-1] += f" [Argo CD]({cfg.argocd_ui}/applications/argocd/{cfg.argocd_app})"
    else:
        out.append("No deploy found before the alert.")
    shown = ev.changes[:MAX_SUSPECTS]
    for ch in shown:
        ref = f"#{ch.pr}" if ch.pr else f"`{ch.sha[:7]}`"
        out += ["", f"**Suspect: {ref} {ch.title}** by @{ch.author}"
                + (f", merged {iso(ch.merged_at)}" if ch.merged_at else "") + f" ({ch.url})", "", _patch(ch.files)]
    if len(ev.changes) > len(shown):
        out += ["", f"…and {len(ev.changes) - len(shown)} earlier commit(s) that touched deploy/ or config/ "
                    "between the two deploys."]
    if ev.deploy and ev.previous_deploy and not ev.changes and not any(n.startswith("GitHub") for n in ev.notes):
        out.append("No commit between the two deploys touched deploy/ or config/.")
    if ev.running:
        provider = ev.running.get("provider", {})
        out += ["", f"Running: podcast-agent {ev.running.get('version')} · image `{ev.running.get('commit')}` · "
                    f"provider {provider.get('provider', provider) if isinstance(provider, dict) else provider}"]
    out.append("")

    first = ev.changes[0] if ev.changes else None
    out += ["### Next steps", "",
            "1. Confirm the suspect: the cost moved with the deploy above, and the diff changes what drives it.",
            ("2. Mitigate: revert "
             + ((f"#{first.pr} (the Revert button on the pull request, or `git revert {first.sha[:7]}`)" if first.pr
                 else f"`{first.sha[:7]}` (`git revert {first.sha[:7]}` in a pull request)") if first
                else "the last config change")
             + "; merge it after CI. Argo CD syncs dev within a minute."),
            "3. Watch the next canary come back under budget. The alert resolves after five minutes without an "
            "overrun, and this issue closes itself with the timeline.",
            f"4. Runbook: {cfg.runbook}", ""]
    links = [(n, u) for n, u in (("Prometheus", cfg.prometheus_ui), ("Alertmanager", cfg.alertmanager_ui),
                                  ("Jaeger", cfg.jaeger_ui), ("MLflow", cfg.mlflow_ui), ("Argo CD", cfg.argocd_ui)) if u]
    if links:
        out += ["Dashboards (tailnet only): " + " · ".join(f"[{n}]({u})" for n, u in links), ""]
    if ev.notes:
        out += ["<details><summary>Enrichment notes</summary>", ""] + [f"- {n}" for n in ev.notes] + ["", "</details>",
                                                                                                       ""]
    state = {"key": group_key(payload), "alertname": alertname, "started_at": iso(start),
             "deploy": {"revision": ev.deploy.revision, "deployed_at": iso(ev.deploy.deployed_at)} if ev.deploy else None,
             "suspect": first.pr if first else None}
    out.append(f"<!-- incident-bridge {json.dumps(state)} -->")

    issue_labels = [lab for lab in cfg.labels if lab]
    severity = labels.get("severity")
    if severity:
        issue_labels.append(f"severity/{severity}")
    if any("drill" in ch.labels for ch in ev.changes):
        issue_labels.append("drill")
    return title, "\n".join(out), issue_labels


def read_marker(body: str) -> dict[str, Any] | None:
    m = MARKER.search(body)
    return json.loads(m.group(1)) if m else None


@dataclass
class Timeline:
    impact_start: datetime | None = None
    impact_source: str = ""
    first_bad: EpisodeTrace | None = None
    detected: datetime | None = None
    ticket: datetime | None = None
    mitigation: Deploy | None = None
    recovered: EpisodeTrace | None = None
    last_bad: EpisodeTrace | None = None
    resolved: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def mttd_s(self) -> float | None:
        return between(self.impact_start, self.detected)

    @property
    def mttr_s(self) -> float | None:
        return between(self.impact_start, self.recovered.end if self.recovered else None)

    def as_dict(self) -> dict[str, Any]:
        def ep(e: EpisodeTrace | None) -> dict[str, Any] | None:
            return None if e is None else {**asdict(e), "start": iso(e.start), "end": iso(e.end)}

        return {
            "impact_start": iso(self.impact_start), "impact_source": self.impact_source, "first_bad": ep(self.first_bad),
            "detected": iso(self.detected), "ticket": iso(self.ticket),
            "mitigation": None if not self.mitigation else {"revision": self.mitigation.revision,
                                                            "deployed_at": iso(self.mitigation.deployed_at)},
            "recovered": ep(self.recovered), "last_bad": ep(self.last_bad), "resolved": iso(self.resolved),
            "mttd_s": self.mttd_s, "mttr_s": self.mttr_s, "notes": self.notes,
        }


def build_timeline(state: dict[str, Any], issue: dict[str, Any], payload: dict[str, Any], src: Sources,
                   now: datetime) -> Timeline:
    ev = Evidence()  # collects notes from failed sources
    t = Timeline(detected=parse_time(state.get("started_at")) or started_at(payload),
                 ticket=parse_time(issue.get("created_at")), resolved=ended_at(payload) or now)
    deploy = state.get("deploy") or {}
    t.impact_start = parse_time(deploy.get("deployed_at"))
    t.impact_source = f"Argo CD synced {str(deploy.get('revision', ''))[:7]}" if t.impact_start else ""
    look_from = (t.impact_start or t.detected or now) - timedelta(minutes=5)
    bad = _attempt(ev, "Jaeger", lambda: src.episodes({"podcast_agent.cost.over_budget": "true"}, look_from, now,
                                                       limit=100)) or []
    if bad:
        t.first_bad, t.last_bad = bad[0], bad[-1]
        if not t.impact_start:
            t.impact_start, t.impact_source = bad[0].start, "first over-budget episode"
    history = _attempt(ev, "Argo CD", src.argocd_history) or []
    after = [d for d in history if d.deployed_at and t.detected and d.deployed_at > t.detected]
    if after:
        t.mitigation = min(after, key=lambda d: d.deployed_at or now)
    if t.mitigation and t.mitigation.deployed_at:
        mitigated_at = t.mitigation.deployed_at
        good = _attempt(ev, "Jaeger recovery", lambda: src.episodes(
            {"podcast_agent.cost.over_budget": "false", "podcast_agent.traffic": "canary"}, mitigated_at, now)) or []
        t.recovered = good[0] if good else None
    t.notes = ev.notes
    return t


def render_resolution(t: Timeline) -> str:
    def row(event: str, at: datetime | None, note: str = "") -> str:
        return f"| {event} | {iso(at) or '–'} | {note} |"

    since = t.impact_start
    rows = [
        row(f"Impact starts: {t.impact_source or 'unknown'}", t.impact_start),
        row(f"First episode over budget (`{t.first_bad.episode}`, {money(t.first_bad.cost)})" if t.first_bad
            else "First episode over budget", t.first_bad.start if t.first_bad else None,
            f"+{duration(between(since, t.first_bad.start if t.first_bad else None))}"),
        row("Alert firing", t.detected, f"**MTTD {duration(t.mttd_s)}**"),
        row("This issue opened", t.ticket, f"+{duration(between(t.detected, t.ticket))} after the alert"),
        row(f"Fix live: Argo CD synced `{t.mitigation.revision[:7]}`" if t.mitigation else "Fix live",
            t.mitigation.deployed_at if t.mitigation else None,
            f"{duration(between(t.detected, t.mitigation.deployed_at if t.mitigation else None))} after the alert"),
        row(f"Last episode over budget (`{t.last_bad.episode}`)" if t.last_bad else "Last episode over budget",
            t.last_bad.start if t.last_bad else None),
        row(f"First canary back under budget ({money(t.recovered.cost)}, {t.recovered.claims} claims)"
            if t.recovered else "First canary back under budget", t.recovered.end if t.recovered else None,
            f"**MTTR {duration(t.mttr_s)}**"),
        row("Alert resolved", t.resolved, f"after {duration(between(t.recovered.end if t.recovered else None, t.resolved))} "
                                          "clean (the rule's 5-minute window)"),
    ]
    out = ["## Resolved", "",
           f"**MTTD {duration(t.mttd_s)} · MTTR {duration(t.mttr_s)}** (impact start → detection, and impact start → "
           "first episode back under budget).", "",
           "| Event | Time (UTC) | |", "|---|---|---|", *rows, ""]
    if t.notes:
        out += ["Missing evidence: " + "; ".join(t.notes), ""]
    out.append(f"<!-- incident-bridge-resolution {json.dumps(t.as_dict())} -->")
    return "\n".join(out)


# --------------------------------------------------------------------------- webhook


_lock = threading.Lock()


def handle(payload: dict[str, Any], cfg: Config, src: Sources, gh: GitHub, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    key = group_key(payload)
    with _lock:  # Alertmanager can retry while the first delivery is still gathering evidence
        existing = gh.find_open_issue(key)
        if payload.get("status") == "firing":
            if existing:
                return {"action": "exists", "issue": existing["number"]}
            title, body, labels = render_issue(payload, gather(payload, cfg, src, gh, now), cfg)
            issue = gh.create_issue(title, body, labels)
            log.info("opened #%s %s", issue.get("number"), title)
            return {"action": "opened", "issue": issue.get("number"), "url": issue.get("html_url")}
        if not existing:
            return {"action": "nothing-open"}
        state = read_marker(existing.get("body") or "") or {}
        timeline = build_timeline(state, existing, payload, src, now)
        gh.comment(existing["number"], render_resolution(timeline))
        gh.close(existing["number"])
        log.info("closed #%s: MTTD %s, MTTR %s", existing["number"], duration(timeline.mttd_s),
                 duration(timeline.mttr_s))
        return {"action": "closed", "issue": existing["number"], "mttd_s": timeline.mttd_s, "mttr_s": timeline.mttr_s}


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    client = httpx.Client(timeout=httpx.Timeout(5.0))
    src, gh = Sources(cfg, client), GitHub(cfg, client)
    app = FastAPI(title="Podcast Agent incident bridge")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "repository": cfg.repository, "dry_run": cfg.dry_run}

    @app.post("/alertmanager")
    async def alertmanager(request: Request) -> dict[str, Any]:
        payload = await request.json()
        return await anyio.to_thread.run_sync(handle, payload, cfg, src, gh)

    return app


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    ap = argparse.ArgumentParser(prog="podcast-agent incident-bridge", description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="0.0.0.0")  # noqa: S104 - a container listens on all interfaces
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config()
    log.info("incident bridge for %s%s", cfg.repository, " (dry run: no GITHUB_TOKEN)" if cfg.dry_run else "")
    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
