# Postmortem: episodes over the cost budget after a claim-budget change (drill)

| | |
|---|---|
| Date | 2026-10-15 |
| Type | Drill: a planned fault, injected with `tools/drill/cost_drill.py` |
| Environment | dev (lab on claws-mac-mini, offline provider: no API spend). This first run targeted `lab/claws`, the branch the lab tracked while the lab stack (#70) was in review; the fault and the revert merged there. The same flow now runs on `main`. |
| Severity | ticket (`PodcastAgentCostBudgetExceeded`) |
| Incident | [#78](https://github.com/Organized-AI/podcast-agent/issues/78) |
| **MTTD** | **1m 1s** (bad config live → alert) |
| **MTTR** | **10m 33s** (bad config live → canary back under budget) |

This postmortem is blameless: it is about how the system let a change through and how fast we saw and undid it, not about who made the change.

## Summary

A pull request (`MAX_CLAIMS` 8 → 40, `MAX_STEPS_PER_CLAIM` 4 → 12) raised dev's claim budget. It passed CI and Argo CD rolled it out. The synthetic canary, a long episode with more check-worthy claims than the old budget allowed, began checking 35 claims instead of 8 and went over its $0.15 budget. The alert fired 1m 1s after the rollout, the incident bridge opened #78 2m 11s later with the suspect pull request in it, and reverting that pull request restored the canary 10m 33s after the rollout.

## Impact

- **Customers: none.** 0 live episodes went over budget; only the canary did. This is the outcome the canary exists for: it runs the most expensive kind of episode every minute, so a costlier config shows on it before a customer's episode.
- **Canary:** 10 episodes over budget, worst $0.21 against a $0.15 budget; $1.41 above the canary's normal $0.07 in total (list prices; the lab's offline provider spent nothing).

## Timeline (UTC)

| Event | Time | |
|---|---|---|
| #77 opened: "Raise dev's claim budget so long episodes get every claim checked" | 2026-10-15T21:43:42Z |  |
| #77 merged after CI | 2026-10-15T21:57:44Z | CI 2m 41s, merged 11m 21s after |
| Argo CD synced dev: **impact starts** | 2026-10-15T21:58:30Z | 46s after merge |
| First canary over budget | 2026-10-15T21:59:03Z | $0.21 |
| `PodcastAgentCostBudgetExceeded` firing: **detected** | 2026-10-15T21:59:30Z | MTTD 1m 1s |
| Incident issue #78 opened by the bridge | 2026-10-15T22:01:42Z | 2m 11s after the alert |
| Revert #80 opened | 2026-10-15T22:02:04Z | 22s after the issue |
| #80 merged after CI | 2026-10-15T22:07:48Z | CI 3m 11s, merged 2m 33s after |
| Argo CD synced the revert: **mitigated** | 2026-10-15T22:08:00Z | 8m 29s after detection |
| First canary back under budget: **recovered** | 2026-10-15T22:09:03Z | MTTR 10m 33s |
| Alert resolved | 2026-10-15T22:13:15Z | 4m 12s after recovery |
| #78 closed by the bridge with its timeline | 2026-10-15T22:13:42Z |  |

## Where the time went

| Phase | Duration |
|---|---|
| Detect (impact → alert) | 1m 1s |
| Notify (alert → issue) | 2m 11s |
| Mitigate (alert → revert live) | 8m 29s |
| Recover (revert live → canary under budget) | 1m 3s |
| **Impact start → recovered (MTTR)** | **10m 33s** |

The bridge measured the same incident independently from Argo CD, Jaeger and Alertmanager: MTTD 1m 0s, MTTR 10m 33s (its comment on #78).

## Detection

- **What caught it:** the canary, not a customer. It runs every minute and is labeled `podcast_agent.traffic=canary`, so the issue could say no customer episode had gone over yet.
- **Why it was fast:** each episode is priced as it runs, the violations counter starts at zero (so Prometheus sees the first overrun as an increase), and Alertmanager groups for only 10 seconds.
- **The ticket:** 8 of 8 evidence checks passed on #78:
  - ✅ says who is affected: the canary only, no live episode
  - ✅ cost per episode now and an hour earlier
  - ✅ spend by step, with verification on top
  - ✅ the most expensive episode, linked in Jaeger and MLflow
  - ✅ the suspect pull request, #77, and its diff
  - ✅ the setting that changed, in the diff
  - ✅ the mitigation: revert the suspect
  - ✅ the runbook

## Root cause

The claim budget is the main control on what an episode costs: verification is most of the bill, and each claim adds a short tool-using loop. Raising it (`MAX_CLAIMS` 8 → 40, `MAX_STEPS_PER_CLAIM` 4 → 12) let the planner check every check-worthy claim in long episodes. For the canary that meant 35 claims and 92 model calls instead of 8 and 28, $0.21 instead of $0.07. Nothing between the pull request and production asked what the change would cost: CI checks the chart renders and the agent is correct, not what it spends.

## What went well

- The canary took the hit before any customer episode did.
- The issue named the deploy, the pull request and its diff, so the fix (a revert) needed no investigation.
- Mitigation was an ordinary pull request: Argo CD rolled it out, nobody touched the cluster.
- The bridge closed the issue with its own timeline, which agrees with the drill's.

## What the drill found wrong with its own tooling

These are not in the timeline's root cause, but they changed the numbers, and two of them would matter in a real incident. They were added by hand: the generated postmortem sees only the measurements.

- **A false start.** The first fault pull request (#76) failed its PR Gate. A unit test of the drill's fault injection read the live `values-dev.yaml`, and on the fault's own pull request that file already carried the fault. The test now uses a fixture; #76 was closed and the drill restarted as #77.
- **The ticket took 2m 11s instead of about 10 s, and the bridge logged its token.** The bridge's GitHub token had been loaded from `gh auth token` with its trailing newline, so every GitHub call failed with an illegal header value. The server logged that error with a traceback that included the token. Alertmanager retried, the Secret was reloaded without the newline, and the next delivery opened #78. The bridge now strips the token and logs webhook errors redacted, without a traceback (PR: *Bridge: strip the token, keep tokens out of logs*). The token should be rotated.
- **A duplicate ticket.** Alertmanager delivered the group again 8 s after #78 was opened, before GitHub's issue list showed #78, and the bridge opened #79. #79 was closed as a duplicate; the bridge now remembers which issue it opened for each group.
- **The bad change waited 11m 21s for a merge** after CI passed. That is the drill waiting for a person, not part of the incident: impact starts when Argo CD deploys the change.

## What could be better

- The change passed every CI check: nothing between the pull request and production asks what it costs.
- The alert stayed open 4m 12s after the canary recovered.
- Canary and live overruns share one severity, so a customer-facing overrun would only open a ticket.
- The bridge's first deliveries failed silently from the alert's point of view: nothing alerts when the bridge itself cannot open an issue.

## Follow-ups

- [#82](https://github.com/Organized-AI/podcast-agent/issues/82) Check a config change's cost in CI before it merges
- [#83](https://github.com/Organized-AI/podcast-agent/issues/83) Resolve the cost alert when the canary recovers, not five minutes later
- [#84](https://github.com/Organized-AI/podcast-agent/issues/84) Page when live episodes go over budget; keep the canary at ticket

## Evidence

- Fault: [#77](https://github.com/Organized-AI/podcast-agent/pull/77) · revert: [#80](https://github.com/Organized-AI/podcast-agent/pull/80) · incident: [#78](https://github.com/Organized-AI/podcast-agent/issues/78)
- Worst canary trace: `56fc63b21237d81fa5f4742d7b200acc` (Jaeger and MLflow links in #78)
- The drill's log: `samples/proof/drill-cost.txt`
