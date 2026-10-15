# evals/baselines/

The gated metrics from the last accepted live run, one JSON file per suite (`live.json`). The live suite fails if a gated metric is worse than its baseline by more than the suite's `tolerance`, even when it still clears the threshold.

To accept a new baseline, review the nightly report (artifact `live-evals`), then write the baseline from that report. The suite does not run again, so the API isn't billed twice:

```bash
gh run download <run-id> -n live-evals -D /tmp/live-evals
podcast-agent eval --suite evals/suites/live.yaml --baseline-from /tmp/live-evals/eval.json
```

Only a passing report of the same suite is accepted. `--update-baseline` instead runs the suite and writes its result. Commit the updated file in its own pull request, with the run linked.

`live.json` comes from [run 38732222622](https://github.com/Organized-AI/podcast-agent/actions/runs/38732222622), the first live run, which met 14 of 14 gates. Its report is in `samples/proof/evals-live/`.
