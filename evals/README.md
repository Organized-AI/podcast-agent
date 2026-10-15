# evals/

Labeled data and thresholds that say whether the agent is good, not just whether it runs.

| Path | What it holds |
|---|---|
| [datasets/factcheck.jsonl](datasets/factcheck.jsonl) | Claims with the verdict a careful fact-checker would give on a fixed date, the acceptable alternatives, why, and the knowledge-base entries that decide it. Covers stale facts, passed and future deadlines, open-ended predictions, contradicted claims, anecdotes and verified facts |
| [datasets/episodes.yaml](datasets/episodes.yaml) | Per-transcript expectations for a run: summary length and must-mention themes, note counts, and the claims that should be extracted with their acceptable verdicts |
| `suites/` | Which datasets to run with which provider, and the thresholds a run must meet ([#10](https://github.com/Organized-AI/podcast-agent/issues/10)) |
| `baselines/` | Scores from the last accepted live run, for regression checks ([#12](https://github.com/Organized-AI/podcast-agent/issues/12)) |

`tests/test_eval_datasets.py` validates both datasets on every pull request: unique ids, known verdicts, an expected verdict among the acceptable ones, knowledge-base ids that exist, and transcripts that exist.

## Adding a case

1. Add a line to `factcheck.jsonl` (or a claim to `episodes.yaml`) with the verdict you expect and `why`.
2. If the case needs evidence the agent cannot find, add a dated entry to `data/knowledge_base.json` in the same pull request.
3. A case that the agent gets wrong today is still worth adding: it is how a regression or a fix shows up in the scores.
