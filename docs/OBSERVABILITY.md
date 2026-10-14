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

## Metrics

| Metric | Type | Attributes |
|---|---|---|
| `gen_ai.client.token.usage` | histogram, `{token}` | `gen_ai.token.type` (input, output), provider, request and response model |
| `gen_ai.client.operation.duration` | histogram, seconds | provider, model, `error.type` on failure |
| `podcast_agent.episodes` | counter | `podcast_agent.episode.status` |
| `podcast_agent.verdicts` | counter | `podcast_agent.verdict`, `podcast_agent.claim.type` |
| `podcast_agent.self_corrections` | counter | `podcast_agent.step.name` |
| `podcast_agent.tool_calls` | counter | `gen_ai.tool.name`, `outcome` (ok, empty, error) |

Responses served from the local response cache get a span (with `podcast_agent.llm.response_cache_hit=true`) but no token or duration metrics, because no model call was made.

## What stays out of telemetry

Podcast content can be confidential. By default spans and metrics carry ids, counts, model names, durations, verdicts and confidence only: no transcript text, claims, quotes, prompts, completions or search queries. `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` adds them for debugging; `tests/test_telemetry.py` checks that a default run puts no transcript or claim text in any span attribute or event.
