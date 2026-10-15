"""OpenTelemetry traces and metrics, in one place.

Telemetry is off unless it is configured through the standard ``OTEL_*`` variables, so the CLI,
tests and the committed samples behave exactly as before:

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` (for example ``http://localhost:4318``) turns on OTLP/HTTP export
  of traces and metrics; ``OTEL_TRACES_EXPORTER=console`` prints spans instead.
* ``OTEL_SDK_DISABLED=true`` turns everything off; ``OTEL_TRACES_SAMPLER``, ``OTEL_SERVICE_NAME`` and
  ``OTEL_RESOURCE_ATTRIBUTES`` work as the specification says.
* ``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`` adds prompts, completions and tool
  queries to spans. It is off by default: spans carry ids, counts, models and verdicts, never
  transcript or model text.

Span tree for one run (names follow the OpenTelemetry GenAI semantic conventions)::

    run podcast-agent
    └── invoke_agent podcast-agent          one per episode
        ├── step plan · step summarize · …   one per plan step
        │   └── chat claude-sonnet-5-5       one per model call
        └── step verify_claims
            └── verify_claim C1
                ├── chat …
                └── execute_tool web_search

The instrumentation always goes through the OpenTelemetry API. Until ``setup()`` installs an SDK the
API is a no-op, so instrumented code costs almost nothing when telemetry is off.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import context as otel_context
from opentelemetry import metrics, propagate, trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from podcast_agent import __version__

if TYPE_CHECKING:
    from podcast_agent.llm.base import LLMResponse

INSTRUMENTATION = "podcast_agent"
SERVICE_NAME = "podcast-agent"
CAPTURE_CONTENT_ENV = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"

# Well-known gen_ai.provider.name values from the semantic conventions; others are namespaced.
PROVIDER_NAMES = {"anthropic": "anthropic", "bedrock": "aws.bedrock", "openai": "openai"}

_tracer = trace.get_tracer(INSTRUMENTATION, __version__)
_meter = metrics.get_meter(INSTRUMENTATION, __version__)

TOKEN_USAGE = _meter.create_histogram(
    "gen_ai.client.token.usage", unit="{token}", description="Tokens used per model call, by direction",
    explicit_bucket_boundaries_advisory=[1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576],
)
OPERATION_DURATION = _meter.create_histogram(
    "gen_ai.client.operation.duration", unit="s", description="Duration of each model call",
    explicit_bucket_boundaries_advisory=[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92],
)
EPISODES = _meter.create_counter("podcast_agent.episodes", unit="{episode}", description="Episodes processed, by status")
VERDICTS = _meter.create_counter("podcast_agent.verdicts", unit="{verdict}", description="Fact-check verdicts, by kind")
SELF_CORRECTIONS = _meter.create_counter(
    "podcast_agent.self_corrections", unit="{correction}", description="Model outputs rejected and retried, by step"
)
TOOL_CALLS = _meter.create_counter("podcast_agent.tool_calls", unit="{call}", description="Tool calls, by tool and outcome")
# Spend is an estimate at list prices (cost.py). "{USD}" is an annotation, so Prometheus names keep the "usd" in the name.
COST = _meter.create_counter(
    "podcast_agent.cost.usd", unit="{USD}", description="Estimated model spend at list prices, by step and model"
)
EPISODE_COST = _meter.create_histogram(
    "podcast_agent.episode.cost.usd", unit="{USD}", description="Estimated model spend per episode",
    explicit_bucket_boundaries_advisory=[0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1, 2, 5],
)
BUDGET_VIOLATIONS = _meter.create_counter(
    "podcast_agent.budget.violations", unit="{episode}", description="Episodes that cost more than their budget"
)
TRAFFIC = ("live", "canary")

_providers: list[Any] = []


# --------------------------------------------------------------------------- setup


def enabled_by_env(env: Mapping[str, str] | None = None) -> bool:
    """Whether the environment asks for telemetry: an exporter or an OTLP endpoint, and the SDK not disabled."""
    env = os.environ if env is None else env
    if env.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return False
    exporter = env.get("OTEL_TRACES_EXPORTER", "").strip().lower()
    if exporter:
        return exporter != "none"
    return bool(env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def setup(environment: str | None = None) -> bool:
    """Install the SDK with OTLP (or console) exporters. Returns False and changes nothing when telemetry is off."""
    if _providers or not enabled_by_env():
        return bool(_providers)
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, MetricExporter, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter

    attributes: dict[str, str] = {"service.version": __version__}
    if not os.getenv("OTEL_SERVICE_NAME"):
        attributes["service.name"] = SERVICE_NAME
    env_name = environment or os.getenv("DEPLOY_ENV")
    if env_name:
        attributes["deployment.environment.name"] = env_name
    resource = Resource.create(attributes)  # merges OTEL_RESOURCE_ATTRIBUTES and OTEL_SERVICE_NAME

    span_exporter: SpanExporter
    metric_exporter: MetricExporter
    if os.getenv("OTEL_TRACES_EXPORTER", "otlp").strip().lower() == "console":
        span_exporter, metric_exporter = ConsoleSpanExporter(), ConsoleMetricExporter()
    else:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        span_exporter, metric_exporter = OTLPSpanExporter(), OTLPMetricExporter()

    tracer_provider = TracerProvider(resource=resource)  # sampler from OTEL_TRACES_SAMPLER
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    _providers.append(tracer_provider)
    if os.getenv("OTEL_METRICS_EXPORTER", "").strip().lower() != "none":
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15_000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        _providers.append(meter_provider)
    return True


def shutdown() -> None:
    """Flush and stop the exporters; call once before the process exits."""
    for provider in _providers:
        provider.shutdown()  # flushes pending spans and a final metrics export
    _providers.clear()


def capture_content() -> bool:
    return os.getenv(CAPTURE_CONTENT_ENV, "").strip().lower() == "true"


# --------------------------------------------------------------------------- spans


@contextmanager
def span(name: str, *, kind: SpanKind = SpanKind.INTERNAL, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
    """Start a span as the current span; exceptions are recorded and mark it as an error."""
    with _tracer.start_as_current_span(name, kind=kind, attributes=_clean(attributes or {})) as current:
        yield current


def mark_error(current: Span, exc: BaseException) -> None:
    """Record an exception the caller handles (so the span would otherwise look successful)."""
    current.record_exception(exc)
    current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
    current.set_attribute("error.type", type(exc).__name__)


def add_event(kind: str, step: str, message: str) -> None:
    """Mirror a reasoning-trace event onto the current span; the message is kept only with content capture on."""
    current = trace.get_current_span()
    if not current.is_recording():
        return
    attributes: dict[str, Any] = {"podcast_agent.step.name": step}
    if capture_content():
        attributes["podcast_agent.message"] = message
    current.add_event(f"podcast_agent.{kind}", attributes)


def current_ids() -> tuple[str | None, str | None]:
    """Hex trace and span ids of the current span, or (None, None) when nothing is being recorded."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


def inject() -> dict[str, str]:
    """W3C trace context (``traceparent``, ``tracestate``) for the current span, to send with a job."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def attached(carrier: Mapping[str, str] | None) -> Iterator[None]:
    """Make the trace context in ``carrier`` current, so spans started inside join that trace."""
    if not carrier:
        yield
        return
    token = otel_context.attach(propagate.extract(dict(carrier)))
    try:
        yield
    finally:
        otel_context.detach(token)


# --------------------------------------------------------------------------- GenAI helpers


def provider_name(name: str) -> str:
    return PROVIDER_NAMES.get(name, f"podcast_agent.{name}")


def chat_attributes(*, provider: str, model: str, max_tokens: int, temperature: float | None,
                    task: str, tier: str) -> dict[str, Any]:
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider_name(provider),
        "gen_ai.request.model": model,
        "gen_ai.request.max_tokens": max_tokens,
        "gen_ai.request.temperature": temperature,
        "podcast_agent.llm.task": task,
        "podcast_agent.llm.tier": tier,
    }


def record_chat(current: Span, resp: LLMResponse, *, provider: str, request_model: str, duration_s: float,
                messages: list[dict[str, str]] | None = None) -> None:
    """Attach the response to its span and record the GenAI client metrics."""
    current.set_attributes(_clean({
        "gen_ai.response.model": resp.model,
        "gen_ai.usage.input_tokens": resp.input_tokens,
        "gen_ai.usage.output_tokens": resp.output_tokens,
        "gen_ai.usage.cache_read.input_tokens": resp.cache_read_tokens or None,
        "gen_ai.response.finish_reasons": [resp.stop_reason] if resp.stop_reason else None,
        "podcast_agent.llm.response_cache_hit": resp.cached,
        "podcast_agent.llm.tokens_estimated": resp.estimated or None,
    }))
    if capture_content():
        if messages is not None:
            current.set_attribute("gen_ai.input.messages", json.dumps(messages, ensure_ascii=False))
        current.set_attribute("gen_ai.output.messages",
                              json.dumps([{"role": "assistant", "content": resp.text}], ensure_ascii=False))
    if resp.cached:  # no model call was made, so there is nothing to meter
        return
    base = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": provider_name(provider),
            "gen_ai.request.model": request_model, "gen_ai.response.model": resp.model}
    OPERATION_DURATION.record(duration_s, base)
    TOKEN_USAGE.record(resp.input_tokens, {**base, "gen_ai.token.type": "input"})
    TOKEN_USAGE.record(resp.output_tokens, {**base, "gen_ai.token.type": "output"})


def record_cost(usd: float, *, model: str, priced_as: str, step: str, tier: str) -> None:
    COST.add(usd, _clean({"gen_ai.request.model": model, "podcast_agent.cost.priced_as": priced_as,
                          "podcast_agent.step.name": step or None, "podcast_agent.llm.tier": tier}))


def traffic(episode_id: str) -> str:
    """Synthetic canary episodes (ids starting with "canary") are counted apart from live traffic."""
    return "canary" if episode_id.startswith("canary") else "live"


def budget_attributes(budget: float | None, mode: str, kind: str) -> dict[str, str]:
    return {"podcast_agent.budget.usd": "off" if budget is None else f"{budget:.2f}",
            "podcast_agent.budget.mode": mode, "podcast_agent.traffic": kind}


def start_budget_series(budget: float | None, mode: str) -> None:
    """Export the violations counter at zero from the start.

    Prometheus's increase() needs two samples, so a series that first appears at 1 hides the first episode that
    went over budget; starting every series at 0 lets the alert fire on that first episode.
    """
    for kind in TRAFFIC:
        BUDGET_VIOLATIONS.add(0, budget_attributes(budget, mode, kind))


def record_episode_cost(current: Span, usd: float, *, budget: float | None, mode: str, over: bool, kind: str) -> None:
    """Put the episode's spend and budget on its span; count it, and count it again if it went over budget."""
    current.set_attributes(_clean({
        "podcast_agent.cost.usd": round(usd, 6),
        "podcast_agent.cost.budget_usd": budget,
        "podcast_agent.cost.over_budget": over,
        "podcast_agent.traffic": kind,
    }))
    attributes = budget_attributes(budget, mode, kind)
    EPISODE_COST.record(usd, {"podcast_agent.traffic": kind})
    if over:
        BUDGET_VIOLATIONS.add(1, attributes)
        current.add_event("podcast_agent.budget_exceeded",
                          {**attributes, "podcast_agent.cost.usd": round(usd, 6)})


def record_chat_error(provider: str, request_model: str, duration_s: float, exc: BaseException) -> None:
    OPERATION_DURATION.record(duration_s, {"gen_ai.operation.name": "chat", "gen_ai.provider.name": provider_name(provider),
                                           "gen_ai.request.model": request_model, "error.type": type(exc).__name__})


def _clean(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Drop None values: OpenTelemetry attributes cannot be null."""
    return {k: v for k, v in attributes.items() if v is not None}
