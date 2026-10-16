# Audit of run `20261016-010820-9471da`

36 passed · 0 warnings · 0 failed · audited 2026-10-16T01:12:25+00:00

| Scope | Area | Check | Result | Detail |
|---|---|---|---|---|
| run | run | Required files present | ✅ PASS | all present: run_manifest.json, report.json, report.md, logs/trace.jsonl |
| run | run | Output files match the manifest hashes | ✅ PASS | 12 files re-hashed with SHA-256, all match |
| run | run | Trace belongs to this run, in order and complete | ✅ PASS | 377 events, all run_id 20261016-010820-9471da, timestamps in order, count matches the manifest (377) |
| run | run | Source transcripts unchanged since the run | ✅ PASS | 4 transcripts re-hashed, all match |
| ep001 | plan | Plan has every required step, in order, and none failed | ✅ PASS | 4 planned steps; 5 executed, all ok |
| ep001 | notes | Summary length (spec mode) | ✅ PASS | 318 words, accepted range 180–321 |
| ep001 | notes | Exactly 5 takeaways | ✅ PASS | 5 takeaways |
| ep001 | notes | Quotes verbatim at their timestamp and speaker | ✅ PASS | 5 quotes re-matched against the transcript, all ≥ 0.90 |
| ep001 | notes | Topic tags well formed | ✅ PASS | 8 kebab-case tags |
| ep001 | claims | Every claim is grounded in its transcript line | ✅ PASS | 4 claims re-matched at ≥ 0.85 |
| ep001 | claims | Confidence within the evidence cap; evidence ids unique | ✅ PASS | 4 verdicts re-calibrated from their evidence, all within cap |
| ep001 | claims | Every verdict has a complete trace | ✅ PASS | 4 claims: tool calls in the trace match the report, each ends in a verdict |
| ep002 | plan | Plan has every required step, in order, and none failed | ✅ PASS | 4 planned steps; 5 executed, all ok |
| ep002 | notes | Summary length (spec mode) | ✅ PASS | 288 words, accepted range 180–321 |
| ep002 | notes | Exactly 5 takeaways | ✅ PASS | 5 takeaways |
| ep002 | notes | Quotes verbatim at their timestamp and speaker | ✅ PASS | 5 quotes re-matched against the transcript, all ≥ 0.90 |
| ep002 | notes | Topic tags well formed | ✅ PASS | 8 kebab-case tags |
| ep002 | claims | Every claim is grounded in its transcript line | ✅ PASS | 5 claims re-matched at ≥ 0.85 |
| ep002 | claims | Confidence within the evidence cap; evidence ids unique | ✅ PASS | 5 verdicts re-calibrated from their evidence, all within cap |
| ep002 | claims | Every verdict has a complete trace | ✅ PASS | 5 claims: tool calls in the trace match the report, each ends in a verdict |
| ep003 | plan | Plan has every required step, in order, and none failed | ✅ PASS | 4 planned steps; 5 executed, all ok |
| ep003 | notes | Summary length (spec mode) | ✅ PASS | 254 words, accepted range 180–321 |
| ep003 | notes | Exactly 5 takeaways | ✅ PASS | 5 takeaways |
| ep003 | notes | Quotes verbatim at their timestamp and speaker | ✅ PASS | 5 quotes re-matched against the transcript, all ≥ 0.90 |
| ep003 | notes | Topic tags well formed | ✅ PASS | 8 kebab-case tags |
| ep003 | claims | Every claim is grounded in its transcript line | ✅ PASS | 2 claims re-matched at ≥ 0.85 |
| ep003 | claims | Confidence within the evidence cap; evidence ids unique | ✅ PASS | 2 verdicts re-calibrated from their evidence, all within cap |
| ep003 | claims | Every verdict has a complete trace | ✅ PASS | 2 claims: tool calls in the trace match the report, each ends in a verdict |
| ep004 | plan | Plan has every required step, in order, and none failed | ✅ PASS | 5 planned steps; 6 executed, all ok |
| ep004 | notes | Summary length (spec mode) | ✅ PASS | 208 words, accepted range 180–321 |
| ep004 | notes | Exactly 5 takeaways | ✅ PASS | 5 takeaways |
| ep004 | notes | Quotes verbatim at their timestamp and speaker | ✅ PASS | 5 quotes re-matched against the transcript, all ≥ 0.90 |
| ep004 | notes | Topic tags well formed | ✅ PASS | 8 kebab-case tags |
| ep004 | claims | Every claim is grounded in its transcript line | ✅ PASS | 5 claims re-matched at ≥ 0.85 |
| ep004 | claims | Confidence within the evidence cap; evidence ids unique | ✅ PASS | 5 verdicts re-calibrated from their evidence, all within cap |
| ep004 | claims | Every verdict has a complete trace | ✅ PASS | 5 claims: tool calls in the trace match the report, each ends in a verdict |

Re-run with `podcast-agent audit <run_dir>`. A WARN means the run recorded the issue itself (for example a summary accepted with a warning) or the check could not be performed.
