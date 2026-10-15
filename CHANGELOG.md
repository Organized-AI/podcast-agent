# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow `X.Y.Z` ([Semantic Versioning](https://semver.org/)).

## [Unreleased]

### Added
- Guide: a **Walkthrough** tab that tells the project's story in eleven stops, from the brief to production. **Present** shows one stop at a time, with what to say, what to show (each link jumps to the figure or proof card and offers a way back) and commands to run live; arrow keys and presenter clickers move between stops, and N hides the speaker notes. **Write-up** shows every stop on one page, each ending with what was learned and why. The content lives in `website/walkthrough.yaml`; `make walkthrough` rebuilds the tab and `docs/WRITEUP.md`, and `make drift` (in CI) fails if they are out of date or a link points nowhere. Proof cards on the Audit trail now have ids, so `#pf-<proof>` links land on one.
- Traces fan out to MLflow as well as Jaeger. The Collector's traces pipeline has a second exporter, `otlp_http/mlflow`, to MLflow's OTLP endpoint; the agent is unchanged. MLflow 3.17 joins the Compose `observability` profile (pinned by digest, SQLite store, 127.0.0.1:5050), and `make observe` now also checks that MLflow holds the run's trace with every span Jaeger has, the GenAI spans typed as AGENT, CHAT_MODEL and TOOL, the token usage rolled up, and no content. `make observe-outage`, which CI runs after it, stops MLflow, checks that a run still reaches Jaeger in full, restarts MLflow and checks that the Collector's retry delivers every queued span.
- Guide: the v0.2.0 page now shows what has closed since release and what is still open, each with its evidence. The Audit trail adds the live Claude run (gates and per-episode judge scores), the lab's recorded sync on merge, and the public verification of v0.2.0's provenance.
- The OpenSSF Best Practices **passing** badge: [project 15516](https://www.bestpractices.dev/projects/15516), with all 67 criteria met. The badge is in the README, and `docs/OPENSSF-BEST-PRACTICES.md` holds the same answers, with a URL where the criterion requires one.
- Fuzzing: Atheris fuzzers for the TXT and JSON transcript parsers and the episode-id sanitizer (`fuzz/`), built and run by ClusterFuzzLite on every pull request that touches the code (5 minutes) and weekly (30 minutes). Base images are pinned by digest and dependencies are installed from `uv.lock` with hashes.
- The first live eval run with Claude (Sonnet 5.5 + Haiku 5.5) met all 14 gates: 25 of 25 fact-check verdicts, every citation valid, Brier 0.033, every quote verbatim, every expected claim found, judge 0.83. It is the baseline in `evals/baselines/live.json`, and its report is in `samples/proof/evals-live/`. The judge's two invented facts, both in ep004's summary, are #51.
- `podcast-agent eval --baseline-from <eval.json>` writes a suite's baseline from a reviewed, passing report, such as the nightly artifact, without running the suite again.
- Deploy pull requests are opened as the `organized-ai-gitops` GitHub App (`actions/create-github-app-token`), so their checks run as on any pull request, with no manual approval. Without the App, the workflows fall back to `GITHUB_TOKEN` and dispatch the checks themselves.
- Every GitHub Release carries the image's Sigstore-signed SLSA provenance as assets (`podcast-agent-<tag>.sigstore.json` and `.intoto.jsonl`), attached by `tools/ci/release_provenance.sh` from the attestation `publish` created. v0.2.0 was backfilled.
- A standing lab cluster (`make lab`): Argo CD on kind tracking `main`, with dev running the digest Git names, pulled from GHCR, so a merged deploy pull request rolls the pods without anyone touching the cluster. It uses its own kubeconfig, listens on localhost, and can be published to the tailnet only. The chart can expose the API as a NodePort for it (`api.service.type`, `api.service.nodePort`).
- `/healthz` reports the commit the running image was built from, so a rollout is visible from outside the cluster.
- Guide: a v0.2.0 view that answers the six review points on v0.1.0 with evidence, summarizes what the release added and measured, shows how it shipped (one digest from dev to prod) and lists what is not done yet. The Audit trail adds proof cards for the release run, the dev and prod digest with its SLSA provenance, and the OpenSSF Scorecard.

### Changed
- The Collector's OTLP exporters use their current names, `otlp_grpc` and `otlp_http`; Collector 0.161 logs the old `otlp` and `otlphttp` names as deprecated.

### Fixed
- Following the Docker quickstart with an API key (`cp .env.example .env`, then `docker run --env-file .env`) failed at the first model call: `.env.example` set `LLM_CACHE_DIR=.cache/llm`, which overrode the image's `/cache/llm` with a directory under `/app`, where the container's user can't write. It is now commented out, and CI checks that the cache directory `.env.example` gives the image can be created. Offline runs never touched the cache, so they hid it.
- With telemetry on (prod's values), the chart's NetworkPolicy blocked the pods' OTLP export: it allowed egress on 53 and 443 only, and the Collector listens on 4318. The policy now also allows the port in `otel.endpoint` whenever `otel.enabled` is true, and `make helm` checks prod's policy for it.
- The rubric judge now sees the transcript the way the agent's steps do, with each speaker's role. It was given names only, so it flagged roles the transcript states ("Chris Bell (host)") as invented: the root cause of #51, which the agent's summary did not cause.
- Malformed JSON transcripts (an utterance that is not an object, `guests` or `host` of the wrong type, a nested value where text belongs) raised `AttributeError` or `TypeError`, so the API answered 500. They now raise `TranscriptError`, a 422 naming the field. A string `guests` value is split on commas instead of into characters, and negative timestamps count as 0 seconds (#49, found by the new JSON fuzzer).

### Security
- `SECURITY.md` adds guidance for government and regulated deployments: the secure-development practices mapped to NIST SP 800-218 (SSDF 1.1) with the evidence for each, and how to run inside an authorization boundary (provider choice, air-gapped operation, telemetry, egress, identity, FIPS and hardened base images, deploy-by-digest, audit retention).
- `SECURITY.md` no longer overstates the NetworkPolicy: it limits egress to DNS and HTTPS, not to named hosts; pinning destinations needs an egress gateway or an FQDN-aware CNI policy.

## [0.2.0] - 2026-10-15

The production revamp: OpenTelemetry tracing and metrics with a local observability stack, an eval suite that gates every pull request, Argo CD GitOps with dev and prod Helm environments, images built once and promoted by digest with signed provenance, and supply-chain checks mapped to the OpenSSF Best Practices criteria. Tracked in the [v0.2.0 milestone](https://github.com/Organized-AI/podcast-agent/milestone/1).

### Added
- Phase 0, foundation: `uv.lock` with strict gates (ruff with bandit rules, mypy, warnings as errors, a coverage floor), hermetic tests that block the network, `config/settings.yaml` generated from the code with a drift check, a version agreement check, `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue forms, a PR template, CODEOWNERS and pre-commit hooks.
- `docs/OPENSSF-BEST-PRACTICES.md` maps each of the 67 OpenSSF Best Practices passing criteria to its evidence; `SECURITY.md` adds secure-design principles and the common errors the code guards against; the README adds badges, who the project is for and a support section with response times.
- Repository rulesets: `main` only accepts squash-merged pull requests whose required checks pass, and `v*` tags cannot be moved or deleted.
- OpenTelemetry tracing with the GenAI semantic conventions: one trace per run (`run` → `invoke_agent` per episode → `step` → `verify_claim` → `chat` and `execute_tool`), with token usage, finish reasons and verdicts as attributes. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_TRACES_EXPORTER` is set; transcript and model text stay out of spans unless content capture is turned on.
- OpenTelemetry metrics: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, and counters for episodes, verdicts, self-corrections and tool calls.
- Trace context crosses the API and the queue: `POST /v1/jobs` queues a transcript with the request's W3C trace context, and the worker's `process <queue>` CONSUMER span joins that trace, retries included. FastAPI's native OpenTelemetry provides the SERVER spans and HTTP metrics; `/healthz` is excluded.
- `logs/trace.jsonl` lines carry `trace_id` and `span_id`, and the run manifest (schema 1.2) records `otel_trace_id`, so the audit trail links to the trace.
- Local observability stack (Compose profile `observability`): OpenTelemetry Collector, Jaeger, Prometheus with six alert rules and promtool unit tests, and a provisioned Grafana dashboard. `make observe` runs every transcript with telemetry on and `e2e/check_telemetry.py` confirms the trace and metrics arrived; CI runs it on every pull request. The Helm chart gets `otel.*` values.
- Live evals with Claude, nightly when the repository has an `ANTHROPIC_API_KEY` secret: the fact-check cases, a fresh run of every transcript scored against the expectations, and a rubric judge on each summary (faithfulness, coverage, concision, with a penalty per invented fact). Gated metrics are compared with `evals/baselines/live.json`; `--update-baseline` refreshes it.
- `podcast-agent eval` and `make evals`: suites in `evals/suites/` run the labeled fact-check cases through the verifier and score committed run folders without a model (verdict accuracy, macro-F1, citation validity, knowledge-base recall, Brier/ECE calibration, summary coverage, quote fidelity, claim recall, cost). Thresholds gate CI, and the report goes to the job summary. Scoring `samples/claude-run` pinpoints the #13 miss.
- Labeled eval datasets: `evals/datasets/factcheck.jsonl` (25 claims with the expected verdict on a fixed date, acceptable alternatives and why: stale facts, passed and future deadlines, open-ended predictions, contradicted claims, anecdotes, verified facts) and `evals/datasets/episodes.yaml` (per-transcript expectations for summary themes, note counts and the claims each run should check). A schema test runs in CI.
- Build once, promote by digest: every merge to `main` builds one multi-arch image in GHCR (`sha-<commit>`, `main`) with an SBOM and signed build provenance, then opens a pull request pointing dev at its digest. A `vX.Y.Z` tag checks the version and changelog, re-tags that same digest (no rebuild), publishes the GitHub Release from `CHANGELOG.md` and opens the prod pull request. The bot's pull requests dispatch the required checks themselves.
- Argo CD GitOps: an AppProject limited to this repository, two namespaces and the chart's kinds; an ApplicationSet with one Application per environment (dev syncs itself with prune and self-heal, prod syncs by hand); `deploy/gitops/envs/<env>/values.yaml` holds only the image. `docs/GITOPS.md` covers the flow and bootstrap; `e2e/gitops-kind.sh` and the `gitops-e2e` workflow prove on kind that Argo CD deploys dev from a commit and the API comes up healthy.
- Guide: a sixth chapter, Operate, with modules on OpenTelemetry (FIG. 5, a run's spans from the API to Grafana), evals and gates, and the path from merge to cluster (FIG. 6), plus their quizzes, flip cards and glossary terms. The Audit trail tab adds an Evals section (every gate, both committed runs and the 25 labeled cases) and proof cards for the eval suite and the telemetry end-to-end check.
- Helm environments: `values-dev.yaml` (offline provider, no cloud dependencies) and `values-prod.yaml` (External Secrets, KEDA, OpenTelemetry, alert rules); `values.schema.json` rejects unknown keys and bad values; images can be pinned by digest; an optional `PrometheusRule` ships the same alert rules as the local stack. CI lints every environment against the schema and validates the rendered manifests and CRDs with kubeconform.

### Changed
- Repository layout follows vllm-project/semantic-router: the guide moved to `website/`, Compose to `deploy/compose/`, the embed script to `tools/guide/`, and the Makefile is split into `tools/make/*.mk`.
- The Docker image installs from `uv.lock`.

### Security
- Security scanning on every pull request, push to `main` and weekly: CodeQL (Python and Actions), gitleaks over the full history, zizmor on workflows, Trivy on `uv.lock`, the Dockerfile, the Helm chart and the built image, and dependency review on pull requests. Results appear in the Security tab.
- OpenSSF Scorecard runs weekly and on `main`; Dependabot proposes uv, Actions and Docker updates weekly after a 7-day cooldown.
- Helm pods run with group 10001 and a CPU limit.
- The Dockerfile pins `python:3.12-slim` and the uv image by digest (OpenSSF Scorecard *Pinned-Dependencies*).
- The TXT header pattern could backtrack quadratically on a line like `host:` followed by tens of thousands of spaces (CodeQL `py/polynomial-redos`, flagged once `POST /v1/jobs` made transcript text API input). The value now has to start with a non-space.
- An `episode_id` inside a transcript could name output files outside the output folder (`../../x` wrote `x.md` two levels up). Ids are now reduced to `[A-Za-z0-9._-]`, at most 64 characters, before they name a file.

### Fixed
- A prediction whose deadline has not passed is now always ❓ unverifiable, enforced in code after the model's verdict and logged as a self-correction. `samples/claude-run` had "Humans will walk on Mars before 2040" as ⚠️ 0.60 because only the prompt stated the rule.
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

[Unreleased]: https://github.com/Organized-AI/podcast-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Organized-AI/podcast-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Organized-AI/podcast-agent/releases/tag/v0.1.0
