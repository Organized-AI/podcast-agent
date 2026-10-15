# evals/

Labeled data and thresholds that say whether the agent is good, not just whether it runs.

| Path | What it holds |
|---|---|
| [datasets/factcheck.jsonl](datasets/factcheck.jsonl) | Claims with the verdict a careful fact-checker would give on a fixed date, the acceptable alternatives, why, and the knowledge-base entries that decide it. Covers stale facts, passed and future deadlines, open-ended predictions, contradicted claims, anecdotes and verified facts |
| [datasets/episodes.yaml](datasets/episodes.yaml) | Per-transcript expectations for a run: summary length and must-mention themes, note counts, and the claims that should be extracted with their acceptable verdicts |
| [suites/offline.yaml](suites/offline.yaml) | The suite every pull request runs: fact-check cases through the offline heuristics and the committed runs scored from their files, with thresholds |
| `baselines/` | Scores from the last accepted live run, for regression checks ([#12](https://github.com/Organized-AI/podcast-agent/issues/12)) |

`tests/test_eval_datasets.py` validates both datasets on every pull request: unique ids, known verdicts, an expected verdict among the acceptable ones, knowledge-base ids that exist, and transcripts that exist.

## Running

```bash
make evals                                   # the offline suite; exits non-zero on any threshold miss
podcast-agent eval --suite evals/suites/offline.yaml --out evals/reports/offline   # same, writes eval.json + eval.md
podcast-agent eval --score output            # score any run folder against episodes.yaml, no model calls
podcast-agent eval --provider anthropic      # the fact-check cases through Claude (uses the key in the environment)
```

| Metric | Section | Meaning |
|---|---|---|
| `verdict_accuracy` | factcheck | Share of cases whose verdict is one of the acceptable ones |
| `macro_f1` | factcheck | Mean F1 over ✅ / ⚠️ / ❓, so a model that says ❓ to everything cannot score well |
| `citation_validity` | factcheck | Share of ✅ / ⚠️ verdicts that cite knowledge-base or search evidence |
| `kb_recall` | factcheck | Share of decidable cases whose citations include the knowledge-base entry that decides them |
| `brier`, `ece` | factcheck | Calibration of the confidence on ✅ / ⚠️ verdicts (lower is better); ❓ carries low confidence by design and is left out |
| `accuracy.<tag>` | factcheck | Accuracy within a tag, e.g. `future-deadline` (#13) |
| `summary_in_range`, `summary_coverage` | run | Summary length within bounds; share of required themes mentioned |
| `notes_shape_ok`, `quote_fidelity` | run | 5 takeaways and quote and topic counts in range; share of quotes found verbatim in the transcript |
| `claim_recall`, `claim_verdict_accuracy` | run | Expected claims that were checked; of those, verdicts within the acceptable set |
| `llm_calls_per_episode`, `tokens_per_episode` | run | Cost |

Thresholds live in the suite file. A number is a minimum and `{max: x}` is a maximum. They work as a ratchet: raise them when a change improves a score, and never lower them to make a change pass. CI runs `make evals` in the quality job and adds the report to the job summary.

## Adding a case

1. Add a line to `factcheck.jsonl` (or a claim to `episodes.yaml`) with the verdict you expect and `why`.
2. If the case needs evidence the agent cannot find, add a dated entry to `data/knowledge_base.json` in the same pull request.
3. A case that the agent gets wrong today is still worth adding: it is how a regression or a fix shows up in the scores.
