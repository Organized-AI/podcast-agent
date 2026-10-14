# evals/

Eval datasets, suites and baselines live here as data, so a change in quality shows up in review like any other diff. The runner is `podcast-agent eval` (tracked in [#10](https://github.com/Organized-AI/podcast-agent/issues/10)); datasets in [#9](https://github.com/Organized-AI/podcast-agent/issues/9).

| Path | Holds |
|---|---|
| `datasets/` | Labeled cases: claims with the expected verdict, per-transcript expectations for notes |
| `suites/` | Which metrics to compute and the thresholds a run must meet |
| `baselines/` | Last accepted scores per provider, for the regression check |

Results are written to `output/evals/` and uploaded as CI artifacts; they are never committed.
