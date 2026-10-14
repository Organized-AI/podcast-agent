# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow `X.Y.Z` ([Semantic Versioning](https://semver.org/)).

## [Unreleased]

Work toward v0.2.0, tracked in the [v0.2.0 milestone](https://github.com/Organized-AI/podcast-agent/milestone/1).

### Added
- Phase 0, foundation: `uv.lock` with strict gates (ruff with bandit rules, mypy, warnings as errors, a coverage floor), hermetic tests that block the network, `config/settings.yaml` generated from the code with a drift check, a version agreement check, `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue forms, a PR template, CODEOWNERS and pre-commit hooks.

- `docs/OPENSSF-BEST-PRACTICES.md` maps each of the 67 OpenSSF Best Practices passing criteria to its evidence; `SECURITY.md` adds secure-design principles and the common errors the code guards against; the README adds badges, who the project is for and a support section with response times.
- Repository rulesets: `main` only accepts squash-merged pull requests whose required checks pass, and `v*` tags cannot be moved or deleted.
- OpenTelemetry tracing with the GenAI semantic conventions: one trace per run (`run` → `invoke_agent` per episode → `step` → `verify_claim` → `chat` and `execute_tool`), with token usage, finish reasons and verdicts as attributes. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_TRACES_EXPORTER` is set; transcript and model text stay out of spans unless content capture is turned on.
- OpenTelemetry metrics: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, and counters for episodes, verdicts, self-corrections and tool calls.
- Trace context crosses the API and the queue: `POST /v1/jobs` queues a transcript with the request's W3C trace context, and the worker's `process <queue>` CONSUMER span joins that trace, retries included. FastAPI's native OpenTelemetry provides the SERVER spans and HTTP metrics; `/healthz` is excluded.
- `logs/trace.jsonl` lines carry `trace_id` and `span_id`, and the run manifest (schema 1.2) records `otel_trace_id`, so the audit trail links to the trace.

### Changed
- Repository layout follows vllm-project/semantic-router: the guide moved to `website/`, Compose to `deploy/compose/`, the embed script to `tools/guide/`, and the Makefile is split into `tools/make/*.mk`.
- The Docker image installs from `uv.lock`.

### Security
- Security scanning on every pull request, push to `main` and weekly: CodeQL (Python and Actions), gitleaks over the full history, zizmor on workflows, Trivy on `uv.lock`, the Dockerfile, the Helm chart and the built image, and dependency review on pull requests. Results appear in the Security tab.
- OpenSSF Scorecard runs weekly and on `main`; Dependabot proposes uv, Actions and Docker updates weekly after a 7-day cooldown.
- Helm pods run with group 10001 and a CPU limit.
- The TXT header pattern could backtrack quadratically on a line like `host:` followed by tens of thousands of spaces (CodeQL `py/polynomial-redos`, flagged once `POST /v1/jobs` made transcript text API input). The value now has to start with a non-space.
- An `episode_id` inside a transcript could name output files outside the output folder (`../../x` wrote `x.md` two levels up). Ids are now reduced to `[A-Za-z0-9._-]`, at most 64 characters, before they name a file.

### Fixed
- `.env.example` documents `ANTHROPIC_THINKING_SUMMARIES` and `LLM_CACHE_DIR`, which the code read but the example never listed.

## [0.1.0] - 2026-10-14

The take-home as submitted.

### Added
- Summary, key notes (takeaways, timestamped verbatim quotes, topic tags) and a fact-check table with ✅ / ⚠️ / ❓ verdicts and evidence-capped confidence.
- A tool-using verification loop over a dated knowledge base and a mock web search, with structured outputs that are validated and corrected.
- Providers: Anthropic, Amazon Bedrock, OpenAI-compatible, Ollama and an offline heuristic provider.
- Readable and JSONL reasoning traces, a run manifest with SHA-256 hashes, and `podcast-agent audit`.
- Docker image, FastAPI server, queue worker, Helm chart and the deployment strategy.
- The interactive guide with an Audit trail tab, and a committed Claude run in `samples/claude-run/`.

[Unreleased]: https://github.com/Organized-AI/podcast-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Organized-AI/podcast-agent/releases/tag/v0.1.0
