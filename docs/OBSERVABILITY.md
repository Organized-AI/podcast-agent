# Observability

The agent emits OpenTelemetry traces and metrics, and keeps its own reasoning trace (`logs/run.log`, `logs/trace.jsonl`). The two are linked: every `trace.jsonl` line carries the `trace_id` and `span_id` of the span it was written in, and `run_manifest.json` records the run's `otel_trace_id`.

## Turning it on

Telemetry is off unless configured, through the standard OpenTelemetry variables:

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Export traces and metrics over OTLP/HTTP, e.g. `http://localhost:4318` (a Collector) |
| `OTEL_TRACES_EXPORTER=console` | Print spans and metrics to stdout instead (debugging) |
| `OTEL_SDK_DISABLED=true` | Off, whatever else is set |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | Resource attributes; the default service name is `podcast-agent`, and `DEPLOY_ENV` sets `deployment.environment.name` |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | Sampling, e.g. `parentbased_traceidratio` with `0.1` |
| `OTEL_METRICS_EXPORTER=none` | Traces only |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` | Add prompts, completions, tool queries and trace messages to spans (off by default) |

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 podcast-agent run data/transcripts --out output
OTEL_TRACES_EXPORTER=console podcast-agent run data/transcripts/ep004_pdf_example.txt --provider offline --quiet
```

## Local stack

```bash
make observe        # Collector, Jaeger, MLflow, Prometheus and Grafana; runs every transcript with telemetry; checks it arrived
make observe-outage # then: stop MLflow, check runs still reach Jaeger, restart it, check the queued spans arrive
make observe-down   # stop it and drop its data
```

| Component | Where | Config |
|---|---|---|
| OpenTelemetry Collector | OTLP on 127.0.0.1:4317 (gRPC) and :4318 (HTTP) | [deploy/observability/otel-collector.yaml](../deploy/observability/otel-collector.yaml) |
| Jaeger (traces) | [127.0.0.1:16686](http://127.0.0.1:16686/search?service=podcast-agent) | in-memory storage |
| MLflow (the same traces, as agent runs) | [127.0.0.1:5050](http://127.0.0.1:5050/#/experiments/0/traces) | SQLite in the `mlflow` volume; OTLP in at `/v1/traces` |
| Prometheus (metrics, alerts) | [127.0.0.1:9090](http://127.0.0.1:9090) | [prometheus.yml](../deploy/observability/prometheus/prometheus.yml), [alerts.yml](../deploy/observability/prometheus/alerts.yml) |
| Grafana (dashboard) | [127.0.0.1:3000](http://127.0.0.1:3000) | provisioned data sources and the [Podcast Agent dashboard](../deploy/observability/grafana/dashboards/podcast-agent.json) |

The Compose profile `observability` binds every port to 127.0.0.1 and pins each image by digest. `make observe` runs the offline provider by default; `make observe OBSERVE_PROVIDER=anthropic` uses the key in `.env`. [e2e/check_telemetry.py](../e2e/check_telemetry.py) then confirms that the run's trace is in Jaeger (one root span, an `invoke_agent` span per episode, a `verify_claim` span per fact-check, no content attributes), that MLflow holds the same trace with every span Jaeger has, and that its metrics and alert rules are in Prometheus. CI runs the same target on every pull request.

### Two trace backends from one export

The agent exports OTLP once, to the Collector. The Collector's traces pipeline has two exporters, so every span goes to both backends and the agent knows about neither:

```
agent ──OTLP/HTTP──▶ Collector ─┬─ otlp_grpc/jaeger ──▶ Jaeger    latency, span tree, which step was slow
                                ├─ otlp_http/mlflow ──▶ MLflow    the run as an agent trace: AGENT, CHAT_MODEL and TOOL spans, token totals
                                └─ prometheus (metrics) ─▶ Prometheus ─▶ Grafana, alerts
```

MLflow reads the `gen_ai.*` attributes: `invoke_agent` spans become AGENT spans, `chat` spans CHAT_MODEL, `execute_tool` spans TOOL, and it adds up `gen_ai.usage.*` into the trace's token usage. With content capture on (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`, local debugging only), it shows each model call's messages as the span's inputs and outputs. With it off, the default, it shows the same ids, counts and verdicts as Jaeger and no text.

Each exporter has its own queue and retries, so one backend being down does not hold up the other. `make observe-outage` (after `make observe`; CI runs both) stops MLflow, checks that a new run still reaches Jaeger in full, starts MLflow again and checks that the Collector's retry delivers every span it queued ([samples/proof/otel-e2e-mac.txt](../samples/proof/otel-e2e-mac.txt) has both runs on a MacBook). Retries give up after five minutes, the Collector's default, so a longer outage loses MLflow's copy of those traces but never Jaeger's.

MLflow needs a SQL backend store for OTLP ingest (3.6 or later); Compose uses SQLite. It rejects Host headers it doesn't recognize, to guard against DNS rebinding, so `--allowed-hosts` lists `mlflow:5000`, the name the Collector calls it by. Traces land in experiment `0` (Default); set `MLFLOW_EXPERIMENT_ID` for the Collector to file them elsewhere. Its port on the host is 5050 because macOS's AirPlay Receiver holds 5000.

The idea came from Joseph Fluckiger's [AIMUG talk](https://www.youtube.com/watch?v=YPJd-RXsWI0) (October 2026), which showed MLflow tracing LangGraph agent runs alongside a Jev decision model.

In Kubernetes, set `otel.enabled=true` and `otel.endpoint` in the Helm values to point pods at a Collector; sampling and `deployment.environment.name` come from the same values. The fan-out lives in that Collector, not in the chart: add the `otlp_http/mlflow` exporter with your MLflow service's URL and experiment id, and the pods don't change. Where transcripts must never reach a tracing backend, add the Collector's `attributes/drop-content` processor to the traces pipeline as a second guard behind the client default; it then applies to every exporter in that pipeline.

## Alerts

| Alert | Fires when | Severity |
|---|---|---|
| `PodcastAgentEpisodesFailing` | any episode finished `failed` or `partial` in 15 minutes | page |
| `PodcastAgentModelErrors` | more than 3 failed model calls in 10 minutes | page |
| `PodcastAgentModelLatencyHigh` | p95 model latency above 30 s for 10 minutes | ticket |
| `PodcastAgentUnverifiableShareHigh` | over 60% of at least 10 verdicts in an hour were ❓, for 30 minutes | ticket |
| `PodcastAgentToolErrors` | more than 5 tool errors in 15 minutes | ticket |
| `PodcastAgentSelfCorrectionsHigh` | over 25% of at least 20 model outputs in an hour were rejected and retried, for 30 minutes | ticket |

`make observability-check` validates the Collector and Prometheus configs and runs the alert unit tests in [alerts_test.yml](../deploy/observability/prometheus/alerts_test.yml) with promtool.

## Traces

One trace per run. Span names and `gen_ai.*` attributes follow the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

```
run podcast-agent                              podcast_agent.run.id, inputs, episodes, errors
└── invoke_agent podcast-agent                 one per episode: status (ok | partial | failed), claims, tokens
    ├── step plan                              plan steps, whether the validator repaired the plan
    │   └── chat claude-haiku-5-5              CLIENT span per model call
    ├── step summarize · step extract_notes · step extract_claims
    │   └── chat claude-sonnet-5-5
    └── step verify_claims
        └── verify_claim C1                    verdict, calibrated and raw confidence, tool calls
            ├── chat claude-sonnet-5-5
            └── execute_tool search_knowledge_base | web_search
```

| Span | Key attributes |
|---|---|
| `chat {model}` | `gen_ai.operation.name=chat`, `gen_ai.provider.name` (`anthropic`, `aws.bedrock`, `openai`, `podcast_agent.ollama`, `podcast_agent.offline`), `gen_ai.request.model`, `gen_ai.request.max_tokens`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.response.finish_reasons`, `podcast_agent.llm.task`, `podcast_agent.llm.response_cache_hit`; `error.type` on failure |
| `execute_tool {tool}` | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `podcast_agent.tool.results` |
| `verify_claim {id}` | `podcast_agent.claim.type`, `podcast_agent.verdict`, `podcast_agent.verdict.confidence`, `podcast_agent.verdict.raw_confidence`, `podcast_agent.claim.tool_calls` |
| `step {name}` | `podcast_agent.step.name`, `podcast_agent.step.attempts`; ERROR status with the exception when a step fails |
| `invoke_agent podcast-agent` | `gen_ai.agent.name`, `podcast_agent.episode.id`, `podcast_agent.episode.status`, `podcast_agent.steps.failed` |

Self-corrections, warnings, errors and verdicts from the reasoning trace also appear as span events (`podcast_agent.self_correct`, …) on the span they happened in.

## Across the API and the worker

In production the API queues work and stateless workers run it. The trace follows the job:

```
POST /v1/jobs                      SERVER span (FastAPI's native OpenTelemetry), continues the caller's traceparent
└── fastapi.endpoint
        ↓  the job carries W3C trace context (traceparent, tracestate)
    process <queue>                CONSUMER span in the worker: messaging.system, queue name, job id, attempt, outcome
    └── invoke_agent podcast-agent
        └── step … → chat / execute_tool
```

- `POST /v1/jobs` validates the transcript, queues it on `QUEUE_URI` and returns `202` with the `job_id` and `trace_id`. `POST /v1/process` runs synchronously in the request's trace (local use).
- Directory queues (`dir://`) keep the context in a `<file>.tracecontext` sidecar; SQS jobs carry it in the message body as `trace_context`. A job returned to the queue for a retry keeps its context, so every attempt lands in the same trace.
- FastAPI (0.143+) creates the SERVER spans and records `http.server.request.duration` and `http.server.active_requests` through the same providers. `/healthz` is excluded so probes don't flood the backend.

## Metrics

| Metric | Type | Attributes |
|---|---|---|
| `gen_ai.client.token.usage` | histogram, `{token}` | `gen_ai.token.type` (input, output), provider, request and response model |
| `gen_ai.client.operation.duration` | histogram, seconds | provider, model, `error.type` on failure |
| `podcast_agent.episodes` | counter | `podcast_agent.episode.status` |
| `podcast_agent.verdicts` | counter | `podcast_agent.verdict`, `podcast_agent.claim.type` |
| `podcast_agent.self_corrections` | counter | `podcast_agent.step.name` |
| `podcast_agent.tool_calls` | counter | `gen_ai.tool.name`, `outcome` (ok, empty, error) |
| `http.server.request.duration`, `http.server.active_requests` | histogram, gauge | from FastAPI: method, route, status code |

Responses served from the local response cache get a span (with `podcast_agent.llm.response_cache_hit=true`) but no token or duration metrics, because no model call was made.

## What stays out of telemetry

Podcast content can be confidential. By default spans and metrics carry ids, counts, model names, durations, verdicts and confidence only: no transcript text, claims, quotes, prompts, completions or search queries. `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` adds them for debugging; `tests/test_telemetry.py` checks that a default run puts no transcript or claim text in any span attribute or event.
