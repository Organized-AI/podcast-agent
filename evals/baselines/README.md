# evals/baselines/

The gated metrics from the last accepted live run, one JSON file per suite (`live.json`). The live suite fails if a gated metric is worse than its baseline by more than the suite's `tolerance`, even when it still clears the threshold.

To accept a new baseline, review the nightly report (artifact `live-evals`) and then run:

```bash
podcast-agent eval --suite evals/suites/live.yaml --out evals/reports/live --update-baseline
```

Commit the updated file in its own pull request, with the report linked.
