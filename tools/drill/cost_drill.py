"""Cost drill: break the per-episode budget with a real GitOps change, and time how fast it is seen and undone.

    uv run python tools/drill/cost_drill.py                         # on the lab host (claws-mac-mini)
    LAB_HOST=claws-mac-mini uv run python tools/drill/cost_drill.py  # from any machine on the tailnet
    uv run python tools/drill/cost_drill.py --preflight             # only check that the lab is ready

Against the lab (deploy/gitops/lab) and the GitHub repository, with the offline provider, so it spends no API credits:

  0. preflight  dev is synced and healthy, the alert rule is loaded, the canary is running, nothing is firing
  1. baseline   the canary is under budget
  2. inject     a pull request raises dev's claim budget; merged once CI passes and someone approves it
  3. detect     Argo CD rolls it out, the canary goes over budget, PodcastAgentCostBudgetExceeded fires
  4. notify     the incident bridge opens a GitHub issue; the drill checks the evidence in it
  5. mitigate   a revert pull request, merged the same way; Argo CD rolls it out
  6. recover    the canary comes back under budget, the alert resolves, the bridge closes the issue
  7. learn      a postmortem in docs/postmortems/, follow-up issues, and the drill's log in samples/proof/

Each pull request waits for the PR Gate and then for a person to merge it, as a fix would in a real incident; the wait
counts toward MTTR. --auto-merge merges on green instead. GitHub access: GH_TOKEN or GITHUB_TOKEN, else `gh auth token`.
Commits are authored with your git identity (or DRILL_AUTHOR_NAME and DRILL_AUTHOR_EMAIL).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from postmortem import DrillRecord, Episode, PullRequest, plan_followups, render

from podcast_agent.ops.incident import Config, Sources, duration, iso, money, parse_time

T = TypeVar("T")
ROOT = Path(__file__).resolve().parents[2]
VALUES = "deploy/helm/podcast-agent/values-dev.yaml"
ALERT = "PodcastAgentCostBudgetExceeded"
APP = "podcast-agent-dev"
GATE = "PR Gate"
PROOF = "samples/proof/drill-cost.txt"
LOG: list[str] = []


def say(message: str) -> None:
    line = f"{datetime.now(UTC):%H:%M:%S} {message}"
    LOG.append(line)
    print(line, flush=True)


def wait_for(what: str, check: Callable[[], T | None], timeout_s: int, every_s: int = 10) -> T:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            result = check()
        except httpx.HTTPError as exc:
            say(f"  ({what}: {type(exc).__name__}, retrying)")
            result = None
        if result:
            return result
        time.sleep(every_s)
    raise SystemExit(f"timed out after {duration(timeout_s)} waiting for {what}")


# --------------------------------------------------------------------------- GitHub (REST only)


class GitHub:
    def __init__(self, repo: str, token: str, author: dict[str, str]):
        self.repo, self.author = repo, author
        self.http = httpx.Client(base_url=f"https://api.github.com/repos/{repo}", timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(3):
            resp = self.http.request(method, path, **kwargs)
            if resp.status_code < 500:
                break
            time.sleep(2 + attempt * 3)
        if resp.status_code >= 400:
            raise SystemExit(f"GitHub {method} {path}: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def main_sha(self) -> str:
        return str(self.call("GET", "/git/ref/heads/main")["object"]["sha"])

    def read(self, path: str, ref: str = "main") -> tuple[str, str]:
        data = self.call("GET", f"/contents/{path}", params={"ref": ref})
        return base64.b64decode(data["content"]).decode(), data["sha"]

    def put_file(self, branch: str, path: str, content: str, message: str) -> None:
        resp = self.http.get(f"/contents/{path}", params={"ref": branch})
        blob = resp.json()["sha"] if resp.status_code == 200 else None
        self.call("PUT", f"/contents/{path}", json={
            "message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch,
            "author": self.author, "committer": self.author, **({"sha": blob} if blob else {})})

    def open_pr(self, branch: str, files: dict[str, str], message: str, title: str, body: str,
                labels: list[str]) -> PullRequest:
        self.call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": self.main_sha()})
        for path, content in files.items():
            self.put_file(branch, path, content, message)
        pr = self.call("POST", "/pulls", json={"title": title, "head": branch, "base": "main", "body": body})
        self.call("POST", f"/issues/{pr['number']}/labels", json={"labels": labels})
        return PullRequest(number=pr["number"], title=title, url=pr["html_url"], head_sha=pr["head"]["sha"],
                           opened_at=parse_time(pr["created_at"]))

    def gate(self, sha: str) -> str | None:
        runs = self.call("GET", f"/commits/{sha}/check-runs", params={"check_name": GATE})["check_runs"]
        done = [r for r in runs if r["status"] == "completed"]
        return done[0]["conclusion"] if done else None

    def merge_after_ci(self, pr: PullRequest, auto: bool) -> PullRequest:
        """Wait for the PR Gate, then merge (auto) or wait for a person to merge, as in a real incident."""
        say(f"  waiting for {GATE} on #{pr.number}")
        conclusion = wait_for(f"CI on #{pr.number}", lambda: self.gate(pr.head_sha), 30 * 60, 15)
        if conclusion != "success":
            raise SystemExit(f"CI on #{pr.number} finished {conclusion}; not merging")
        pr.ci_passed_at = datetime.now(UTC)
        say(f"  {GATE} passed on #{pr.number} after {duration((pr.ci_passed_at - pr.opened_at).total_seconds())}"
            if pr.opened_at else f"  {GATE} passed on #{pr.number}")
        if auto:
            self.call("PUT", f"/pulls/{pr.number}/merge", json={
                "merge_method": "squash", "commit_title": f"{pr.title} (#{pr.number})", "sha": pr.head_sha})
        else:
            say(f"  ready for review: merge #{pr.number} to continue ({pr.url})")
        merged = wait_for(f"#{pr.number} to be merged", lambda: (lambda d: d if d.get("merged_at") else None)(
            self.call("GET", f"/pulls/{pr.number}")), 2 * 60 * 60, 10)
        pr.merge_sha, pr.merged_at = merged["merge_commit_sha"], parse_time(merged["merged_at"])
        if auto:
            self.call("DELETE", f"/git/refs/heads/{merged['head']['ref']}")
        waited = f", {duration((pr.merged_at - pr.ci_passed_at).total_seconds())} after CI" if pr.merged_at else ""
        say(f"  #{pr.number} merged as {pr.merge_sha[:7]}{waited}")
        return pr

    def incident_since(self, since: datetime) -> dict[str, Any] | None:
        issues = self.call("GET", "/issues", params={"labels": "incident", "state": "all", "since": iso(since),
                                                       "per_page": 20})
        fresh = [i for i in issues if "pull_request" not in i and (parse_time(i["created_at"]) or since) >= since
                 and ALERT in i["title"]]
        return min(fresh, key=lambda i: i["created_at"]) if fresh else None

    def issue(self, number: int) -> dict[str, Any]:
        result: dict[str, Any] = self.call("GET", f"/issues/{number}")
        return result

    def comments(self, number: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self.call("GET", f"/issues/{number}/comments", params={"per_page": 100})
        return result

    def comment(self, number: int, body: str) -> None:
        self.call("POST", f"/issues/{number}/comments", json={"body": body})

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = self.call("POST", "/issues", json={"title": title, "body": body, "labels": labels})
        return result


def github_token() -> str:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        try:
            token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            token = ""
    if not token:
        raise SystemExit("no GitHub token: set GH_TOKEN or run `gh auth login`")
    return token


def git_author() -> dict[str, str]:
    def cfg(key: str) -> str:
        return subprocess.run(["git", "config", key], capture_output=True, text=True, cwd=ROOT).stdout.strip()

    name, email = os.getenv("DRILL_AUTHOR_NAME") or cfg("user.name"), os.getenv("DRILL_AUTHOR_EMAIL") or cfg("user.email")
    if not (name and email):
        raise SystemExit("set git user.name and user.email (or DRILL_AUTHOR_NAME/DRILL_AUTHOR_EMAIL)")
    return {"name": name, "email": email}


# --------------------------------------------------------------------------- the lab


class Lab:
    def __init__(self, host: str):
        self.host = host
        self.http = httpx.Client(timeout=15)
        self.src = Sources(Config(prometheus=f"http://{host}:30909", jaeger=f"http://{host}:30686",
                                  argocd=f"http://{host}:30090", argocd_app=APP), self.http)

    def app(self, name: str = APP) -> dict[str, Any]:
        resp = self.http.get(f"http://{self.host}:30090/api/v1/applications/{name}")
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    def deployed_at(self, revision: str) -> datetime | None:
        for entry in self.app().get("status", {}).get("history") or []:
            if entry.get("revision") == revision:
                return parse_time(entry.get("deployedAt"))
        return None

    def healthy(self, name: str = APP) -> bool:
        status = self.app(name).get("status", {})
        return bool(status.get("sync", {}).get("status") == "Synced" and status.get("health", {}).get("status") == "Healthy")

    def alert(self) -> dict[str, Any] | None:
        resp = self.http.get(f"http://{self.host}:30903/api/v2/alerts",
                             params={"active": "true", "silenced": "false", "inhibited": "false",
                                     "filter": f'alertname="{ALERT}"'})
        resp.raise_for_status()
        alerts = resp.json()
        return alerts[0] if alerts else None

    def value(self, query: str) -> float:
        rows = self.src.prom(query)
        return rows[0][1] if rows else 0.0

    def rule_loaded(self) -> bool:
        resp = self.http.get(f"http://{self.host}:30909/api/v1/rules", params={"type": "alert"})
        resp.raise_for_status()
        return any(r["name"] == ALERT for g in resp.json()["data"]["groups"] for r in g["rules"])

    def canaries(self, start: datetime, end: datetime, over: bool | None = None) -> list[Episode]:
        tags = {"podcast_agent.traffic": "canary"}
        if over is not None:
            tags["podcast_agent.cost.over_budget"] = "true" if over else "false"
        return [Episode(start=e.start, cost=e.cost or 0.0, claims=e.claims, llm_calls=e.llm_calls,
                        trace_id=e.trace_id) for e in self.src.episodes(tags, start, end, limit=60)]


# --------------------------------------------------------------------------- the drill


def raise_claim_budget(values: str, claims: str, steps: str) -> str:
    """Add MAX_CLAIMS and MAX_STEPS_PER_CLAIM to dev's config block (they default to 8 and 4 in values.yaml)."""
    if "MAX_CLAIMS" in values:
        raise SystemExit(f"{VALUES} already sets MAX_CLAIMS; is a drill already in progress?")
    lines = values.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("config:"))
    end = next((i for i in range(at + 1, len(lines)) if lines[i] and not lines[i].startswith(" ")), len(lines))
    while end > at + 1 and not lines[end - 1].strip():
        end -= 1
    added = ["  # Check every claim in long episodes, not just the first eight.",
             f'  MAX_CLAIMS: "{claims}"', f'  MAX_STEPS_PER_CLAIM: "{steps}"']
    return "\n".join([*lines[:end], *added, *lines[end:]]) + "\n"


def check_ticket(issue: dict[str, Any], bad: PullRequest) -> list[tuple[bool, str]]:
    body = issue.get("body") or ""
    return [
        ("No customer episode has gone over yet" in body, "says who is affected: the canary only, no live episode"),
        ("| Mean cost per episode |" in body and "$" in body, "cost per episode now and an hour earlier"),
        ("`verify_claims`" in body, "spend by step, with verification on top"),
        ("[Jaeger](" in body and "[MLflow](" in body, "the most expensive episode, linked in Jaeger and MLflow"),
        (f"Suspect: #{bad.number}" in body, f"the suspect pull request, #{bad.number}, and its diff"),
        ("MAX_CLAIMS" in body, "the setting that changed, in the diff"),
        (f"revert #{bad.number}" in body, "the mitigation: revert the suspect"),
        ("runbooks/cost-budget.md" in body, "the runbook"),
    ]


def run(args: argparse.Namespace) -> int:
    lab = Lab(args.lab_host)
    say(f"cost drill against {args.repo}, lab at {args.lab_host}")

    say("0. preflight")
    for name in (APP, "observability", "mlflow"):
        ok = lab.healthy(name)
        say(f"  Argo CD {name}: {'Synced Healthy' if ok else 'NOT ready'}")
        if not ok:
            raise SystemExit(f"{name} is not Synced and Healthy")
    if not lab.rule_loaded():
        raise SystemExit(f"Prometheus has no {ALERT} rule")
    say(f"  Prometheus has the {ALERT} rule")
    canaries = lab.canaries(datetime.now(UTC) - timedelta(minutes=5), datetime.now(UTC))
    say(f"  canary episodes in Jaeger from the last 5 minutes: {len(canaries)}")
    if not canaries:
        raise SystemExit("the canary is not running")
    if lab.alert():
        raise SystemExit(f"{ALERT} is already firing; let it resolve first")
    say("  nothing firing")
    if args.preflight:
        return 0

    gh = GitHub(args.repo, github_token(), git_author())
    values, _ = gh.read(VALUES)
    budget = float(re.search(r'COST_BUDGET_USD:\s*"([0-9.]+)"', values).group(1))  # type: ignore[union-attr]

    say("1. baseline")
    recent = wait_for("a canary episode under budget", lambda: lab.canaries(
        datetime.now(UTC) - timedelta(minutes=5), datetime.now(UTC), over=False), 5 * 60)
    base = recent[-1]
    say(f"  canary under budget: {money(base.cost)} ({base.claims} claims, {base.llm_calls} calls) "
        f"against {money(budget)}")

    say(f"2. inject: raise dev's claim budget to {args.claims} ({args.steps} tool calls per claim)")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    title = "Raise dev's claim budget so long episodes get every claim checked"
    bad = gh.open_pr(
        f"drill/cost-{stamp}", {VALUES: raise_claim_budget(values, args.claims, args.steps)},
        f"{title}\n\nDrill: tools/drill/cost_drill.py", title,
        ("Long episodes have more check-worthy claims than the default budget of 8, so some go unchecked. "
         f"This raises dev to {args.claims} claims and {args.steps} tool calls per claim.\n\n"
         "> **Drill.** Opened by `tools/drill/cost_drill.py` to break the per-episode cost budget on purpose "
         "(offline provider, no API spend). The drill reverts it. See docs/runbooks/cost-budget.md."),
        ["drill", "area/gitops"])
    say(f"  opened #{bad.number} {bad.url}")
    gh.merge_after_ci(bad, args.auto_merge)
    record = DrillRecord(repo=args.repo, environment="dev", budget_usd=budget, bad_pr=bad, baseline=base,
                         change={"MAX_CLAIMS": ("8", args.claims), "MAX_STEPS_PER_CLAIM": ("4", args.steps)})

    say("3. detect")
    deployed = record.deployed_at = wait_for(f"Argo CD to deploy {bad.merge_sha[:7]}",
                                             lambda: lab.deployed_at(bad.merge_sha), 10 * 60, 5)
    say(f"  Argo CD synced {bad.merge_sha[:7]} at {iso(record.deployed_at)}: impact starts")
    alert = wait_for(f"{ALERT} to fire", lab.alert, 20 * 60, 5)
    record.alert_fired_at = parse_time(alert["startsAt"])
    say(f"  {ALERT} firing since {iso(record.alert_fired_at)}: MTTD {duration(record.mttd_s)}")

    say("4. notify")
    issue = wait_for("the incident issue", lambda: gh.incident_since(deployed - timedelta(minutes=1)), 10 * 60, 10)
    incident: int = issue["number"]
    record.issue_number, record.issue_url = incident, issue["html_url"]
    record.issue_opened_at = parse_time(issue["created_at"])
    say(f"  #{issue['number']} opened {duration(record.ticket_s)} after the alert: {issue['html_url']}")
    record.ticket_checks = check_ticket(issue, bad)
    for ok, what in record.ticket_checks:
        say(f"  {'ok  ' if ok else 'MISS'} {what}")

    say("5. mitigate: revert the suspect pull request")
    revert = gh.open_pr(
        f"drill/revert-{bad.number}", {VALUES: values}, f'Revert "{title}"\n\nThis reverts #{bad.number}.',
        f'Revert "{title}"',
        f"Reverts #{bad.number}, which put dev's episodes over the cost budget. Mitigates #{record.issue_number}.\n\n"
        "> **Drill.** Opened by `tools/drill/cost_drill.py`.",
        ["drill", "area/gitops"])
    record.revert_pr = revert
    gh.comment(incident, f"Mitigation: #{revert.number} reverts #{bad.number}; merging once CI passes.")
    say(f"  opened #{revert.number} {revert.url}")
    gh.merge_after_ci(revert, args.auto_merge)
    mitigated = record.mitigated_at = wait_for(f"Argo CD to deploy {revert.merge_sha[:7]}",
                                               lambda: lab.deployed_at(revert.merge_sha), 10 * 60, 5)
    say(f"  Argo CD synced {revert.merge_sha[:7]} at {iso(record.mitigated_at)}: mitigated "
        f"{duration(record.time_to_mitigate_s)} after detection")

    say("6. recover")
    good = wait_for("a canary episode under budget after the revert",
                    lambda: [e for e in lab.canaries(mitigated, datetime.now(UTC), over=False) if e.start >= mitigated],
                    10 * 60, 10)
    first = good[0]
    record.recovered_at = first.start
    say(f"  canary back under budget at {iso(first.start)} ({money(first.cost)}): MTTR {duration(record.mttr_s)}")
    wait_for(f"{ALERT} to resolve", lambda: True if lab.alert() is None else None, 20 * 60, 10)
    record.resolved_at = datetime.now(UTC)
    say(f"  {ALERT} resolved by {iso(record.resolved_at)}")
    closed = wait_for(f"the bridge to close #{incident}",
                      lambda: (lambda i: i if i["state"] == "closed" else None)(gh.issue(incident)), 5 * 60, 10)
    record.issue_closed_at = parse_time(closed["closed_at"])
    for c in gh.comments(incident):
        m = re.search(r"<!-- incident-bridge-resolution (\{.*?\}) -->", c["body"] or "", re.S)
        if m:
            record.bridge = json.loads(m.group(1))
            if record.bridge.get("resolved"):
                record.resolved_at = parse_time(record.bridge["resolved"])
    say(f"  #{record.issue_number} closed by the bridge: its MTTD {duration(record.bridge.get('mttd_s'))}, "
        f"MTTR {duration(record.bridge.get('mttr_s'))}")
    record.over_budget = lab.canaries(deployed, record.recovered_at or datetime.now(UTC), over=True)
    record.live_over_budget = lab.value(
        'sum(increase(podcast_agent_budget_violations_total{podcast_agent_traffic="live"}[1h]))')

    say("7. learn: postmortem and follow-ups")
    day = (record.deployed_at or datetime.now(UTC)).date().isoformat()
    path = f"docs/postmortems/{day}-cost-budget-drill.md"
    record.followups = plan_followups(record, path)
    if not args.no_followups:
        for f in record.followups:
            issue = gh.create_issue(f.title, f.body, f.labels)
            f.number, f.url = issue["number"], issue["html_url"]
            say(f"  follow-up #{f.number}: {f.title}")
    postmortem = render(record, path)
    say(f"  MTTD {duration(record.mttd_s)} · MTTR {duration(record.mttr_s)} · ticket {duration(record.ticket_s)} "
        f"after the alert · {sum(ok for ok, _ in record.ticket_checks)}/{len(record.ticket_checks)} evidence checks")
    (ROOT / path).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / path).write_text(postmortem, encoding="utf-8")
    say(f"  wrote {path}")
    if args.no_publish:
        (ROOT / PROOF).write_text("\n".join(LOG) + "\n", encoding="utf-8")
        return 0
    say("  publishing the postmortem and this log in a pull request")
    files = {path: postmortem, PROOF: "\n".join(LOG) + "\n"}
    pm = gh.open_pr(
        f"drill/postmortem-{stamp}", files, f"Add the postmortem for the cost drill (#{record.issue_number})",
        f"Add the postmortem for the cost drill (#{record.issue_number})",
        (f"The postmortem for #{record.issue_number}: MTTD {duration(record.mttd_s)}, MTTR {duration(record.mttr_s)}. "
         f"Fault #{bad.number}, revert #{revert.number}. Follow-ups: "
         + ", ".join(f"#{f.number}" for f in record.followups if f.number) + ".\n\n"
         "Written by `tools/drill/cost_drill.py`; `samples/proof/drill-cost.txt` is the drill's log."),
        ["drill", "documentation", "area/ops"])
    say(f"  opened #{pm.number} {pm.url}")
    if args.auto_merge:
        gh.merge_after_ci(pm, auto=True)
    (ROOT / PROOF).write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "Organized-AI/podcast-agent"))
    ap.add_argument("--lab-host", default=os.getenv("LAB_HOST", "127.0.0.1"))
    ap.add_argument("--claims", default="40", help="the claim budget the fault sets (default 40)")
    ap.add_argument("--steps", default="12", help="the tool calls per claim the fault sets (default 12)")
    ap.add_argument("--preflight", action="store_true", help="only check that the lab is ready")
    ap.add_argument("--no-followups", action="store_true", help="write the postmortem without filing follow-ups")
    ap.add_argument("--no-publish", action="store_true", help="write the postmortem locally; open no pull request")
    ap.add_argument("--auto-merge", action="store_true",
                    help="merge each pull request once CI passes (default: wait for a person to merge it)")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
