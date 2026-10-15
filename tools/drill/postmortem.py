"""Turn a cost drill's record into a blameless postmortem and the follow-up issues it argues for.

Pure functions over a DrillRecord, so the postmortem's wording and arithmetic are tested without a lab
(tests/test_drill.py). tools/drill/cost_drill.py fills the record as the drill runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from podcast_agent.ops.incident import between, duration, iso, money


@dataclass
class PullRequest:
    number: int
    title: str
    url: str
    head_sha: str = ""
    opened_at: datetime | None = None
    ci_passed_at: datetime | None = None
    merged_at: datetime | None = None
    merge_sha: str = ""


@dataclass
class Episode:
    start: datetime
    cost: float
    claims: int | None
    llm_calls: int | None
    trace_id: str


@dataclass
class FollowUp:
    title: str
    body: str
    labels: list[str]
    problem: str = ""  # what the drill showed, for the postmortem's "what could be better"
    number: int | None = None
    url: str = ""


@dataclass
class DrillRecord:
    repo: str
    environment: str
    budget_usd: float
    change: dict[str, tuple[str, str]]  # setting → (before, after)
    bad_pr: PullRequest
    revert_pr: PullRequest | None = None
    issue_number: int | None = None
    issue_url: str = ""
    issue_opened_at: datetime | None = None
    issue_closed_at: datetime | None = None
    deployed_at: datetime | None = None  # Argo CD synced the bad config
    alert_fired_at: datetime | None = None
    mitigated_at: datetime | None = None  # Argo CD synced the revert
    recovered_at: datetime | None = None  # first canary under budget after the revert finished
    resolved_at: datetime | None = None
    baseline: Episode | None = None
    over_budget: list[Episode] = field(default_factory=list)
    live_over_budget: float = 0.0
    ticket_checks: list[tuple[bool, str]] = field(default_factory=list)
    bridge: dict[str, Any] = field(default_factory=dict)  # the bridge's own timeline (its resolution comment)
    followups: list[FollowUp] = field(default_factory=list)

    # ---------------------------------------------------------------- derived numbers

    @property
    def mttd_s(self) -> float | None:
        return between(self.deployed_at, self.alert_fired_at)

    @property
    def mttr_s(self) -> float | None:
        return between(self.deployed_at, self.recovered_at)

    @property
    def ticket_s(self) -> float | None:
        return between(self.alert_fired_at, self.issue_opened_at)

    @property
    def revert_ci_s(self) -> float | None:
        pr = self.revert_pr
        return between(pr.opened_at, pr.ci_passed_at) if pr else None

    @property
    def time_to_mitigate_s(self) -> float | None:
        return between(self.alert_fired_at, self.mitigated_at)

    @property
    def resolve_lag_s(self) -> float | None:
        return between(self.recovered_at, self.resolved_at)

    @property
    def extra_spend(self) -> float | None:
        if not self.baseline:
            return None
        return sum(max(0.0, e.cost - self.baseline.cost) for e in self.over_budget)

    @property
    def worst(self) -> Episode | None:
        return max(self.over_budget, key=lambda e: e.cost, default=None)


def _changes(record: DrillRecord) -> str:
    return ", ".join(f"`{k}` {a} → {b}" for k, (a, b) in record.change.items())


def plan_followups(record: DrillRecord, postmortem_path: str) -> list[FollowUp]:
    """What this drill's numbers argue for. Each item cites the measurement behind it."""
    ref = f"From the cost drill (incident #{record.issue_number}, postmortem `{postmortem_path}`)."
    worst, base = record.worst, record.baseline
    items = [FollowUp(
        title="Check a config change's cost in CI before it merges",
        labels=["type/feature", "area/ci", "area/ops"],
        problem="The change passed every CI check: nothing between the pull request and production asks what it costs",
        body=(f"{ref}\n\nThe drill's change ({_changes(record)}) passed every CI check and merged; the cost alert "
              f"caught it {duration(record.mttd_s)} after it reached dev. Nothing in CI asks what a change costs.\n\n"
              "**Proposal:** a CI job that renders the environment's config from the pull request, runs the canary "
              "episode offline with it (no API spend, about a second) and fails when the episode costs more than "
              "that environment's `COST_BUDGET_USD`, printing the cost by step.\n\n"
              + (f"Measured in the drill: the canary went from {money(base.cost)} ({base.claims} claims) to "
                 f"{money(worst.cost)} ({worst.claims} claims) against a {money(record.budget_usd)} budget."
                 if worst and base else "")),
    )]
    ci, mitigate = record.revert_ci_s, record.time_to_mitigate_s
    if ci is not None and mitigate and ci / mitigate >= 0.4:
        items.append(FollowUp(
            title="Revert deploy config faster: CI was most of the time to mitigate",
            labels=["type/feature", "area/ci", "area/ops"],
            problem=f"CI on the revert took {duration(ci)} of the {duration(mitigate)} from alert to fix",
            body=(f"{ref}\n\nFrom the alert to the fix being live took {duration(mitigate)}; "
                  f"{duration(ci)} of that was the revert pull request waiting for CI ({ci / mitigate:.0%}). "
                  "The revert only restored a file that had passed CI before the bad change.\n\n"
                  "**Proposal:** a fast path for pull requests that only revert `deploy/` changes: run the required "
                  "PR Gate on the Helm render and the config checks, and leave the full suite to the push to main."),
        ))
    lag = record.resolve_lag_s
    if lag is not None and lag >= 180:
        items.append(FollowUp(
            title="Resolve the cost alert when the canary recovers, not five minutes later",
            labels=["type/feature", "area/telemetry", "area/ops"],
            problem=f"The alert stayed open {duration(lag)} after the canary recovered",
            body=(f"{ref}\n\nThe canary was back under budget at {iso(record.recovered_at)}, but the alert stayed "
                  f"open for another {duration(lag)} because the rule counts overruns over a 5-minute window. "
                  "Anyone watching the alert sees the incident last longer than it did.\n\n"
                  "**Proposal:** keep the 5-minute rule for live traffic, and resolve the canary's alert on the "
                  "canary's latest episode (for example a gauge of its last cost), so recovery shows as soon as one "
                  "canary episode is under budget."),
        ))
    items.append(FollowUp(
        title="Page when live episodes go over budget; keep the canary at ticket",
        labels=["type/feature", "area/telemetry", "area/ops"],
        problem="Canary and live overruns share one severity, so a customer-facing overrun would only open a ticket",
        body=(f"{ref}\n\n`PodcastAgentCostBudgetExceeded` is a ticket whoever went over. In the drill only the canary "
              f"did ({len(record.over_budget)} episodes, {money(record.extra_spend)} over its normal cost; "
              f"{record.live_over_budget:.0f} live), which is the case a ticket is for. A live episode over budget "
              "means a customer is paying for it now.\n\n**Proposal:** split the rule by `podcast_agent_traffic`: "
              "canary overruns open a ticket, live overruns page, with promtool tests for both."),
    ))
    return items


def render(record: DrillRecord, postmortem_path: str) -> str:
    worst, base, bad, revert = record.worst, record.baseline, record.bad_pr, record.revert_pr
    day = (record.deployed_at or record.alert_fired_at or datetime.now()).date().isoformat()
    title = "Postmortem: episodes over the cost budget after a claim-budget change (drill)"
    passed = sum(1 for ok, _ in record.ticket_checks if ok)

    def row(event: str, at: datetime | None, note: str = "") -> str:
        return f"| {event} | {iso(at) or '–'} | {note} |"

    timeline = [
        row(f"#{bad.number} opened: \"{bad.title}\"", bad.opened_at),
        row(f"#{bad.number} merged after CI", bad.merged_at, f"CI {duration(between(bad.opened_at, bad.ci_passed_at))}"),
        row(f"Argo CD synced {record.environment}: **impact starts**", record.deployed_at,
            f"{duration(between(bad.merged_at, record.deployed_at))} after merge"),
        row("First canary over budget", record.over_budget[0].start if record.over_budget else None,
            money(record.over_budget[0].cost) if record.over_budget else ""),
        row("`PodcastAgentCostBudgetExceeded` firing: **detected**", record.alert_fired_at,
            f"MTTD {duration(record.mttd_s)}"),
        row(f"Incident issue #{record.issue_number} opened by the bridge", record.issue_opened_at,
            f"{duration(record.ticket_s)} after the alert"),
    ]
    if revert:
        timeline += [
            row(f"Revert #{revert.number} opened", revert.opened_at,
                f"{duration(between(record.issue_opened_at, revert.opened_at))} after the issue"),
            row(f"#{revert.number} merged after CI", revert.merged_at,
                f"CI {duration(record.revert_ci_s)}"),
        ]
    timeline += [
        row("Argo CD synced the revert: **mitigated**", record.mitigated_at,
            f"{duration(record.time_to_mitigate_s)} after detection"),
        row("First canary back under budget: **recovered**", record.recovered_at, f"MTTR {duration(record.mttr_s)}"),
        row("Alert resolved", record.resolved_at, f"{duration(record.resolve_lag_s)} after recovery"),
        row(f"#{record.issue_number} closed by the bridge with its timeline", record.issue_closed_at),
    ]

    phases = [("Detect (impact → alert)", record.mttd_s), ("Notify (alert → issue)", record.ticket_s),
              ("Mitigate (alert → revert live)", record.time_to_mitigate_s),
              ("Recover (revert live → canary under budget)", between(record.mitigated_at, record.recovered_at))]
    bridge_mttd, bridge_mttr = record.bridge.get("mttd_s"), record.bridge.get("mttr_s")
    out = [
        f"# {title}", "",
        "| | |", "|---|---|",
        f"| Date | {day} |",
        "| Type | Drill: a planned fault, injected with `tools/drill/cost_drill.py` |",
        f"| Environment | {record.environment} (lab on claws-mac-mini, offline provider: no API spend) |",
        "| Severity | ticket (`PodcastAgentCostBudgetExceeded`) |",
        f"| Incident | [#{record.issue_number}]({record.issue_url}) |",
        f"| **MTTD** | **{duration(record.mttd_s)}** (bad config live → alert) |",
        f"| **MTTR** | **{duration(record.mttr_s)}** (bad config live → canary back under budget) |",
        "", "This postmortem is blameless: it is about how the system let a change through and how fast we "
        "saw and undid it, not about who made the change.", "",
        "## Summary", "",
        f"A pull request ({_changes(record)}) raised {record.environment}'s claim budget. It passed CI and Argo CD "
        f"rolled it out. The synthetic canary, a long episode with more check-worthy claims than the old budget "
        f"allowed, began checking {worst.claims if worst else '?'} claims instead of {base.claims if base else '?'} "
        f"and went over its {money(record.budget_usd)} budget. The alert fired {duration(record.mttd_s)} after "
        f"the rollout, the incident bridge opened #{record.issue_number} {duration(record.ticket_s)} later with the "
        f"suspect pull request in it, and reverting that pull request restored the canary "
        f"{duration(record.mttr_s)} after the rollout.", "",
        "## Impact", "",
        f"- **Customers: none.** {record.live_over_budget:.0f} live episodes went over budget; only the canary did. "
        "This is the outcome the canary exists for: it runs the most expensive kind of episode every minute, so a "
        "costlier config shows on it before a customer's episode.",
        f"- **Canary:** {len(record.over_budget)} episodes over budget, worst {money(worst.cost) if worst else '–'} "
        f"against a {money(record.budget_usd)} budget; {money(record.extra_spend)} above the canary's normal "
        f"{money(base.cost) if base else '–'} in total (list prices; the lab's offline provider spent nothing).",
        "",
        "## Timeline (UTC)", "", "| Event | Time | |", "|---|---|---|", *timeline, "",
        "## Where the time went", "", "| Phase | Duration |", "|---|---|",
        *[f"| {name} | {duration(s)} |" for name, s in phases],
        f"| **Impact start → recovered (MTTR)** | **{duration(record.mttr_s)}** |", "",
    ]
    if bridge_mttd is not None:
        out += [f"The bridge measured the same incident independently from Argo CD, Jaeger and Alertmanager: "
                f"MTTD {duration(bridge_mttd)}, MTTR {duration(bridge_mttr)} (its comment on #{record.issue_number}).",
                ""]
    out += [
        "## Detection", "",
        "- **What caught it:** the canary, not a customer. It runs every minute and is labeled "
        "`podcast_agent.traffic=canary`, so the issue could say no customer episode had gone over yet.",
        "- **Why it was fast:** each episode is priced as it runs, the violations counter starts at zero (so "
        "Prometheus sees the first overrun as an increase), and Alertmanager groups for only 10 seconds.",
        f"- **The ticket:** {passed} of {len(record.ticket_checks)} evidence checks passed on #{record.issue_number}:",
        *[f"  - {'✅' if ok else '❌'} {what}" for ok, what in record.ticket_checks], "",
        "## Root cause", "",
        f"The claim budget is the main control on what an episode costs: verification is most of the bill, and "
        f"each claim adds a short tool-using loop. Raising it ({_changes(record)}) let the planner check every "
        f"check-worthy claim in long episodes. For the canary that meant "
        + (f"{worst.claims} claims and {worst.llm_calls} model calls instead of {base.claims} and {base.llm_calls}, "
           f"{money(worst.cost)} instead of {money(base.cost)}." if worst and base else "more model calls.")
        + " Nothing between the pull request and production asked what the change would cost: CI checks the "
        "chart renders and the agent is correct, not what it spends.", "",
        "## What went well", "",
        "- The canary took the hit before any customer episode did.",
        "- The issue named the deploy, the pull request and its diff, so the fix (a revert) needed no investigation.",
        "- Mitigation was an ordinary pull request: Argo CD rolled it out, nobody touched the cluster.",
        "- The bridge closed the issue with its own timeline, which agrees with the drill's.", "",
        "## What could be better", "",
        *[f"- {f.problem or f.title}." for f in record.followups], "",
        "## Follow-ups", "",
        *[f"- [#{f.number}]({f.url}) {f.title}" if f.number else f"- {f.title}" for f in record.followups], "",
        "## Evidence", "",
        f"- Fault: [#{bad.number}]({bad.url}) · revert: "
        + (f"[#{revert.number}]({revert.url})" if revert else "–")
        + f" · incident: [#{record.issue_number}]({record.issue_url})",
        f"- Worst canary trace: `{worst.trace_id}` (Jaeger and MLflow links in #{record.issue_number})" if worst
        else "- No over-budget trace was recorded",
        "- The drill's log: `samples/proof/drill-cost.txt`",
    ]
    return "\n".join(out) + "\n"
