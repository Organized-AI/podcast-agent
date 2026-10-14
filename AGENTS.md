# AGENTS.md

Instructions for coding agents (and humans in a hurry) working in this repository.

## Repo map

| Path | What lives there |
|---|---|
| `src/podcast_agent/` | The package: `agent/` (planner, steps, verifier), `llm/` (providers, structured output), `tools/` (knowledge base, web search), `telemetry.py`, `evals/`, `cli.py`, `server.py`, `worker.py` |
| `tests/` | Hermetic unit tests; `conftest.py` blocks the network and scrubs credentials |
| `config/settings.yaml` | Generated reference of every setting; never edit by hand (`make config`) |
| `data/` | Transcripts, the dated knowledge base and the mock web index |
| `evals/` | Eval datasets, suites with thresholds, baselines |
| `e2e/` | End-to-end run against the Compose stack |
| `deploy/` | Deployable assets only: `helm/`, `gitops/` (Argo CD), `compose/`, `observability/` |
| `tools/` | `make/*.mk`, CI checks (`ci/`), the guide's embed script (`guide/`) |
| `website/` | The interactive guide, deployed as Cloudflare Worker assets |
| `docs/` | Deployment, observability, evals and GitOps write-ups |
| `samples/` | Committed runs and captured proof; regenerate, never hand-edit |

## Commands

```bash
uv sync --locked --extra aws   # set up
make check                     # lint + types + tests + drift; run before every push
make evals                     # offline eval suite and committed-run scoring
make help                      # everything else
```

## Rules

1. Every behavior change ships with a test that fails before and passes after; verdict or quality changes also add an eval case.
2. Generated files change only with their source: `config/settings.yaml` (`make config`), the guide's audit data (`make guide`), `uv.lock` (`uv lock`).
3. Never commit secrets, `.env`, or model output that was not produced by a real run. Samples come from `podcast-agent run`, not from editing.
4. Prompts never ask the model to write out its reasoning; use a short `purpose` field, and read reasoning from the API's thinking summaries.
5. Telemetry carries ids, counts, models and verdicts, never transcript or model text unless content capture is explicitly enabled.
6. Keep every existing entry point working: `podcast-agent run`, `serve`, `worker`, `audit`, `eval`, and `docker run`.
7. One concern per pull request; add a `CHANGELOG.md` line under `[Unreleased]`. Commits are authored by the maintainer; do not add tool attribution trailers.
