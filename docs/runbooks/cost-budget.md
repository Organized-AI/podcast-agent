# Runbook: episodes over the cost budget

**Alert:** `PodcastAgentCostBudgetExceeded` (severity `ticket`). It fires when at least one episode, canary or live, cost more than `COST_BUDGET_USD` in the last 5 minutes. The incident bridge opens a GitHub issue labeled `incident`. Start from that issue: it already holds most of what this page asks you to look up.

## What it means

Every model call is priced at list prices (`src/podcast_agent/cost.py`). The episode's total is compared with its budget when the episode finishes.

- `warn` mode (the default): an episode over budget finishes normally and is counted.
- `enforce` mode: once the budget is spent, the claims left are marked ❓ and flagged for review, so the episode stops costing more.

Either way the episode's report has a warning, and its `invoke_agent` span has a `podcast_agent.budget_exceeded` event.

The canary is a CronJob in dev that runs one long, claim-dense episode every minute (`data/canary/canary_roundtable.json`). It has more check-worthy claims than the default claim budget allows, so it is the first episode to get more expensive when a budget is raised.

The issue's **Episodes over budget** line says who is affected:

- **canary only**: no customer episode has gone over yet. Fix it before one does.
- **live**: customers' episodes are costing more. If the budget is enforced, they are also getting ❓ verdicts for claims that were not checked.

## Triage

1. **What changed?** Most cost jumps follow a deploy. The issue names the Argo CD sync just before the alert and the pull request that touched `deploy/` or `config/`, with its diff. Settings that drive cost are `MAX_CLAIMS`, `MAX_STEPS_PER_CLAIM`, the models (`ANTHROPIC_MODEL`, `ANTHROPIC_FAST_MODEL`), `PROMPT_CACHE` and `MAP_REDUCE_THRESHOLD_WORDS`.
2. **Where does the money go?** Use the issue's spend-by-step table. Verification is normally most of the bill. More claims or more tool calls per claim show up there. A model change shows up in the spend-by-model table.
3. **Which episode?** Open the most expensive episode's trace (Jaeger or MLflow) and compare it with the last canary under budget. Look at the number of `verify_claim` spans, the model calls per claim and the tokens per call.
4. **No deploy?** A different kind of episode may be the cause: a long or claim-dense one. Then the budget, not the code, may need to change. Read the episode's plan step to see the claim budget it chose.

## Mitigate

- **A config change caused it:** revert the pull request. Use the Revert button on the PR, or `git revert <sha>` on a branch and open a pull request. Merge it once CI passes. Argo CD syncs dev within a minute (prod waits for someone to sync it). No one runs `kubectl` or `helm` against the cluster.
- **A new kind of episode caused it, and it has to finish:** set `COST_BUDGET_MODE=enforce` for that environment, in a pull request, so episodes stop spending at the budget.
- **The budget is wrong:** change `COST_BUDGET_USD` in a pull request, and say in it what the new number is based on.

## Confirm and close

1. The next canary episode comes back under budget: its `invoke_agent` span has `podcast_agent.cost.over_budget=false`.
2. The alert resolves after 5 minutes with no overrun.
3. The bridge comments the timeline on the issue (impact start, first episode over budget, alert, issue, fix live, recovery) with MTTD and MTTR, then closes the issue.
4. Write the postmortem in `docs/postmortems/` from that timeline. File each follow-up as an issue and link it from the postmortem.

## Practice

`tools/drill/cost_drill.py` runs this incident on purpose against the lab, with the offline provider, so it spends no API credits. It:

1. Opens a pull request that raises dev's claim budget. Once CI passes it waits for you to merge it, and that wait counts toward MTTR. `--auto-merge` merges on green instead.
2. Waits for the alert and for the issue.
3. Opens the revert and waits for it to be merged the same way.
4. Waits for recovery and for the issue to close.
5. Writes the postmortem.

See `samples/proof/drill-cost.txt` for the last run.
