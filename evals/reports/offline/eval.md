# Eval suite: offline

Fact-check cases through the offline heuristics, plus the committed runs scored without a model.

Provider: provider=offline · main=offline-heuristic · fast=offline-heuristic · **PASS** (19/19 thresholds met)

## factcheck (factcheck)

| Metric | Value | Threshold | Result |
|---|---|---|---|
| `cases` | 25 |  |  |
| `verdict_accuracy` | 0.96 | ≥ 0.96 | ✅ |
| `macro_f1` | 0.9158 | ≥ 0.91 | ✅ |
| `citation_validity` | 1 | ≥ 1 | ✅ |
| `kb_recall` | 0.8824 | ≥ 0.88 | ✅ |
| `brier` | 0.0273 | ≤ 0.05 | ✅ |
| `ece` | 0.1507 |  |  |
| `tool_calls_per_case` | 1.08 |  |  |
| `llm_calls` | 52 |  |  |
| `input_tokens` | 39698 |  |  |
| `output_tokens` | 2877 |  |  |
| `accuracy.ambiguous` | 1 |  |  |
| `accuracy.anecdote` | 1 | ≥ 1 | ✅ |
| `accuracy.contradicted` | 0 |  |  |
| `accuracy.future-deadline` | 1 | ≥ 1 | ✅ |
| `accuracy.issue-13` | 1 |  |  |
| `accuracy.no-deadline` | 1 |  |  |
| `accuracy.passed-deadline` | 1 | ≥ 1 | ✅ |
| `accuracy.stale-fact` | 1 |  |  |
| `accuracy.verified-fact` | 1 |  |  |

| Case | Expected | Verdict | Confidence | KB cited | |
|---|---|---|---|---|---|
| `nasa-msr-2026` | possibly_outdated_or_inaccurate | possibly_outdated_or_inaccurate | 0.74 | KB-030 | ✅ |
| `bitcoin-200k-2025` | possibly_outdated_or_inaccurate | possibly_outdated_or_inaccurate | 0.85 | KB-032 | ✅ |
| `mars-humans-2040` | unverifiable | unverifiable | 0.35 | — | ✅ |
| `bitcoin-500k-2030` | unverifiable | unverifiable | 0.35 | — | ✅ |
| `nvidia-10t-2028` | unverifiable | unverifiable | 0.35 | — | ✅ |
| `async-standard` | unverifiable | unverifiable | 0.35 | — | ✅ |
| `offices-optional-hubs` | unverifiable | unverifiable | 0.35 | — | ✅ |
| `remote-first-dominate-2025` | possibly_outdated_or_inaccurate / unverifiable | unverifiable | 0.30 | — | ✅ |
| `perseverance-landing` | verified | verified | 0.90 | KB-031 | ✅ |
| `perseverance-samples` | verified | verified | 0.90 | KB-031 | ✅ |
| `nvidia-5t-first` | verified | verified | 0.78 | KB-033 | ✅ |
| `fda-autonomy-not-soon` | possibly_outdated_or_inaccurate | unverifiable | 0.30 | — | ❌ |
| `fda-reviews-ai` | verified | verified | 0.78 | KB-010 | ✅ |
| `hospitals-ai-xrays` | verified | verified | 0.90 | KB-011 | ✅ |
| `triage-chatbots` | verified | verified | 0.78 | KB-013 | ✅ |
| `pandemic-digital-health` | verified | verified | 0.90 | KB-015 | ✅ |
| `ai-bias-clinical-data` | verified | verified | 0.90 | KB-014 | ✅ |
| `gitlab-handbook` | verified | verified | 0.90 | KB-001 | ✅ |
| `automattic-distributed` | verified | verified | 0.90 | KB-002 | ✅ |
| `hybrid-most-common` | verified | verified | 0.90 | KB-004 | ✅ |
| `vc-dilutes-control` | verified | verified | 0.71 | KB-021, KB-020 | ✅ |
| `bootstrapping-tradeoff` | verified | verified | 0.90 | KB-022 | ✅ |
| `samir-saas-breakeven` | unverifiable | unverifiable | 0.30 | — | ✅ |
| `emily-fintech-seed` | unverifiable | unverifiable | 0.30 | — | ✅ |
| `emily-raised-rounds` | unverifiable | unverifiable | 0.30 | — | ✅ |

## claude-run (run)

| Metric | Value | Threshold | Result |
|---|---|---|---|
| `episodes` | 4 |  |  |
| `summary_in_range` | 1 | ≥ 1 | ✅ |
| `summary_coverage` | 1 | ≥ 1 | ✅ |
| `notes_shape_ok` | 1 | ≥ 1 | ✅ |
| `quote_fidelity` | 1 | ≥ 1 | ✅ |
| `claim_recall` | 1 | ≥ 1 | ✅ |
| `claim_verdict_accuracy` | 1 | ≥ 0.9 | ✅ |
| `llm_calls_per_episode` | 16.75 |  |  |
| `tokens_per_episode` | 26288.5 |  |  |

| Episode | Summary words | Coverage | Quotes verbatim | Claims found / right | Judge |
|---|---|---|---|---|---|
| ep001 | 318 | 1.00 | 5/5 | 2/2 found, 2 right | — |
| ep002 | 288 | 1.00 | 5/5 | 3/3 found, 3 right | — |
| ep003 | 254 | 1.00 | 5/5 | 2/2 found, 2 right | — |
| ep004 | 208 | 1.00 | 5/5 | 5/5 found, 5 right | — |

## offline-mode (run)

| Metric | Value | Threshold | Result |
|---|---|---|---|
| `episodes` | 4 |  |  |
| `summary_in_range` | 1 | ≥ 1 | ✅ |
| `summary_coverage` | 1 |  |  |
| `notes_shape_ok` | 1 | ≥ 1 | ✅ |
| `quote_fidelity` | 1 | ≥ 1 | ✅ |
| `claim_recall` | 0.8333 | ≥ 0.8 | ✅ |
| `claim_verdict_accuracy` | 0.9 | ≥ 0.9 | ✅ |
| `llm_calls_per_episode` | 15 |  |  |
| `tokens_per_episode` | 13314.2 |  |  |

| Episode | Summary words | Coverage | Quotes verbatim | Claims found / right | Judge |
|---|---|---|---|---|---|
| ep001 | 212 | 1.00 | 4/4 | 2/2 found, 2 right | — |
| ep002 | 208 | 1.00 | 4/4 | 3/3 found, 2 right | — |
| ep003 | 201 | 1.00 | 4/4 | 1/2 found, 1 right | — |
| ep004 | 150 | 1.00 | 4/4 | 4/5 found, 4 right | — |

Claims that missed:
- ep002: FDA + autonom → unverifiable
- ep003: break-even → not extracted
- ep004: Nvidia + trillion → not extracted
