"""OpenTelemetry: the span tree, GenAI attributes, metrics, and what must never leave the process."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from conftest import make_ctx
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from rich.console import Console

from podcast_agent import telemetry
from podcast_agent.llm.base import LLMError, LLMProvider
from podcast_agent.runtime import run_batch
from podcast_agent.trace import Tracer
from podcast_agent.transcript import load_episode


@pytest.fixture(scope="session")
def otel() -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    """One in-memory SDK for the session: OpenTelemetry allows a global provider to be set only once."""
    saved = os.environ.pop("OTEL_SDK_DISABLED", None)  # providers read it when they are created
    try:
        exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    finally:
        if saved is not None:
            os.environ["OTEL_SDK_DISABLED"] = saved
    return exporter, reader


def _metric_points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                out.setdefault(m.name, []).extend(m.data.data_points)
    return out


def _counter_total(points: dict[str, list[Any]], name: str) -> float:
    return sum(p.value for p in points.get(name, []))


@pytest.fixture
def traced_run(otel, tmp_path, data_dir):
    exporter, reader = otel
    exporter.clear()
    before = _metric_points(reader)
    ctx = make_ctx()
    ctx.trace = Tracer("t", log_dir=tmp_path / "logs", console=Console(quiet=True))
    transcript = data_dir / "transcripts" / "ep004_pdf_example.txt"
    reports, errors = run_batch(ctx, [transcript], tmp_path, "otel-test")
    ctx.trace.close()
    assert not errors
    return {"spans": list(exporter.get_finished_spans()), "reports": reports, "out": tmp_path,
            "before": before, "after": _metric_points(reader), "transcript": transcript}


def _by_name(spans: list[ReadableSpan], prefix: str) -> list[ReadableSpan]:
    return [s for s in spans if s.name.startswith(prefix)]


def test_one_trace_with_the_documented_span_tree(traced_run):
    spans = traced_run["spans"]
    by_id = {s.context.span_id: s for s in spans}
    (root,) = _by_name(spans, "run podcast-agent")
    assert root.parent is None
    assert {s.context.trace_id for s in spans} == {root.context.trace_id}

    def parent(s: ReadableSpan) -> ReadableSpan:
        assert s.parent is not None
        return by_id[s.parent.span_id]

    (episode,) = _by_name(spans, "invoke_agent podcast-agent")
    assert parent(episode) is root
    assert episode.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert episode.attributes["podcast_agent.episode.status"] == "ok"
    steps = _by_name(spans, "step ")
    assert {s.name for s in steps} >= {"step plan", "step summarize", "step extract_notes", "step verify_claims"}
    assert all(parent(s) is episode for s in steps)

    claims = _by_name(spans, "verify_claim ")
    assert claims and all(parent(c).name == "step verify_claims" for c in claims)
    assert len(claims) == len(traced_run["reports"][0].fact_checks)
    for c in claims:
        assert c.attributes["podcast_agent.verdict"] in {"verified", "possibly_outdated_or_inaccurate", "unverifiable"}
        assert 0.0 <= c.attributes["podcast_agent.verdict.confidence"] <= 0.95

    tools = _by_name(spans, "execute_tool ")
    assert tools and all(parent(t).name.startswith("verify_claim ") for t in tools)
    assert {t.attributes["gen_ai.tool.name"] for t in tools} <= {"search_knowledge_base", "web_search"}

    chats = _by_name(spans, "chat ")
    assert chats and all(parent(c).name.startswith(("step ", "verify_claim ")) for c in chats)
    for c in chats:
        a = c.attributes
        assert a["gen_ai.operation.name"] == "chat" and a["gen_ai.provider.name"] == "podcast_agent.offline"
        assert a["gen_ai.request.model"] and a["gen_ai.usage.input_tokens"] > 0
        assert c.kind == trace.SpanKind.CLIENT


def test_spans_carry_no_transcript_or_model_text_by_default(traced_run):
    episode = load_episode(traced_run["transcript"])
    fragments = [u.text[:40] for u in episode.utterances if len(u.text) >= 40]
    fragments += [fc.claim.claim[:40] for fc in traced_run["reports"][0].fact_checks]
    values: list[str] = []
    for s in traced_run["spans"]:
        values += [str(v) for v in (s.attributes or {}).values()]
        values += [str(v) for e in s.events for v in (e.attributes or {}).values()]
    assert fragments
    leaked = [f for f in fragments if any(f in v for v in values)]
    assert not leaked, leaked
    assert not any("gen_ai.input.messages" in (s.attributes or {}) for s in traced_run["spans"])


def test_content_capture_is_opt_in(otel, tmp_path, data_dir, monkeypatch):
    monkeypatch.setenv(telemetry.CAPTURE_CONTENT_ENV, "true")
    exporter, _ = otel
    exporter.clear()
    ctx = make_ctx()
    ctx.trace = Tracer("t", console=Console(quiet=True))
    run_batch(ctx, [data_dir / "transcripts" / "ep004_pdf_example.txt"], tmp_path, "otel-capture")
    spans = exporter.get_finished_spans()
    chat = _by_name(list(spans), "chat ")[0]
    assert json.loads(chat.attributes["gen_ai.input.messages"])[0]["role"] == "system"
    assert json.loads(chat.attributes["gen_ai.output.messages"])[0]["role"] == "assistant"
    tool = _by_name(list(spans), "execute_tool ")[0]
    assert tool.attributes["gen_ai.tool.call.arguments"]


def test_audit_trail_and_manifest_link_to_the_trace(traced_run):
    (root,) = _by_name(traced_run["spans"], "run podcast-agent")
    trace_id = format(root.context.trace_id, "032x")
    lines = [json.loads(line) for line in (traced_run["out"] / "logs" / "trace.jsonl").read_text().splitlines()]
    events = [e for e in lines if e["kind"] != "section"]
    assert events and all(e.get("trace_id") == trace_id for e in events)
    manifest = json.loads((traced_run["out"] / "run_manifest.json").read_text())
    assert manifest["otel_trace_id"] == trace_id


def test_metrics_count_tokens_verdicts_and_episodes(traced_run):
    before, after = traced_run["before"], traced_run["after"]
    report = traced_run["reports"][0]
    verdicts = _counter_total(after, "podcast_agent.verdicts") - _counter_total(before, "podcast_agent.verdicts")
    assert verdicts == len(report.fact_checks)
    episodes = _counter_total(after, "podcast_agent.episodes") - _counter_total(before, "podcast_agent.episodes")
    assert episodes == 1
    token_types = {p.attributes["gen_ai.token.type"] for p in after["gen_ai.client.token.usage"]}
    assert token_types == {"input", "output"}
    assert after["gen_ai.client.operation.duration"]
    assert after["podcast_agent.tool_calls"]


class _Broken(LLMProvider):
    name = "anthropic"

    def _complete(self, **kwargs: Any) -> Any:
        raise LLMError("upstream 529")


def test_a_failed_model_call_is_an_error_span(otel):
    exporter, _ = otel
    exporter.clear()
    with pytest.raises(LLMError):
        _Broken("claude-sonnet-5-5").complete(system="s", messages=[{"role": "user", "content": "x"}], task="t")
    (span,) = exporter.get_finished_spans()
    assert span.name == "chat claude-sonnet-5-5" and span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == "LLMError" and span.attributes["gen_ai.provider.name"] == "anthropic"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"}, True),
        ({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces"}, True),
        ({"OTEL_TRACES_EXPORTER": "console"}, True),
        ({"OTEL_TRACES_EXPORTER": "none", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x"}, False),
        ({"OTEL_SDK_DISABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x"}, False),
    ],
)
def test_telemetry_is_off_unless_configured(env, expected):
    assert telemetry.enabled_by_env(env) is expected


def test_setup_changes_nothing_when_disabled():
    assert os.environ["OTEL_SDK_DISABLED"] == "true"  # set by the hermetic fixture
    assert telemetry.setup() is False


def test_context_round_trips_through_a_carrier(otel):
    exporter, _ = otel
    exporter.clear()
    with telemetry.span("producer"):
        carrier = telemetry.inject()
    assert carrier["traceparent"].startswith("00-")
    with telemetry.attached(carrier), telemetry.span("consumer"):
        pass
    producer, consumer = sorted(exporter.get_finished_spans(), key=lambda s: s.name, reverse=True)
    assert consumer.context.trace_id == producer.context.trace_id
    assert consumer.parent is not None and consumer.parent.span_id == producer.context.span_id


def test_setup_exports_spans_and_metrics_with_the_console_exporter(tmp_path):
    """setup() installs real providers; run it in a child process so this session's providers stay untouched."""
    import subprocess
    import sys

    script = (
        "from podcast_agent import telemetry\n"
        "assert telemetry.setup(environment='test') is True\n"
        "with telemetry.span('probe', attributes={'podcast_agent.run.id': 'r1'}):\n"
        "    telemetry.EPISODES.add(1, {'podcast_agent.episode.status': 'ok'})\n"
        "telemetry.shutdown()\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env.update({"OTEL_TRACES_EXPORTER": "console", "OTEL_SERVICE_NAME": "pa-test"})
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60,  # noqa: S603
                         check=True, cwd=tmp_path).stdout
    assert '"name": "probe"' in out and '"service.name": "pa-test"' in out
    assert '"deployment.environment.name": "test"' in out and "podcast_agent.episodes" in out
