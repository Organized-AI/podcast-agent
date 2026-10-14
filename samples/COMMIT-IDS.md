# Commit IDs before and after the history rewrite

On 2026-10-14 the history was rewritten to set every commit's author to Jordan Hill and drop tool-generated
trailer lines. File contents did not change: each new commit has exactly the same tree as the old one.
Run manifests and proof files captured before the rewrite record the old short IDs; use this table to find
the matching commit (`git rev-parse <new>^{tree}` equals the old commit's tree).

| Old | New | Subject |
|---|---|---|
| `07366cb` | `38f729f` | Add `podcast-agent audit` and an auditable run manifest |
| `0812ee5` | `167461a` | Dockerfile: give the runtime user ownership of data/ so restrictive file modes on the host can't block it |
| `69ed8de` | `0845c57` | Support API keys that aren't scoped to a workspace (ANTHROPIC_WORKSPACE_ID) |
| `6d2eb6a` | `462c0ab` | Add an Audit trail tab to the guide, with captured proof |
| `9951206` | `f2b657a` | Add the first Claude run as a sample and show it in the guide |
| `a8ab82e` | `a2fe26f` | Verifier: accept near-miss turns and tell the model exactly what was wrong |
| `b02edfd` | `932dd06` | Podcast agent: summaries, key notes and fact-checking with a reasoning trace |
| `b16ede3` | `fd798af` | Add the interactive architecture guide |
| `b5afb03` | `5fc1cd5` | Fact-checker: ask for a purpose, not reasoning; log thinking summaries |
| `b73c48e` | `227d3b6` | Make the Claude provider work with Claude 5.5 models and SDK 1.13 |
| `bcb7afb` | `4665c9c` | Guide: open run files in a new tab instead of download links |
| `d8c2d80` | `05b53fd` | Link the live guide and the public repo |
| `d91fd9e` | `c0aedbb` | Match the assignment's output format; add Bedrock and a combined report |
| `f3a96e0` | `a99da06` | Fix .env.example for `docker run --env-file` |
