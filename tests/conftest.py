from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from rich.console import Console

from podcast_agent.agent.context import RunContext
from podcast_agent.config import Settings
from podcast_agent.llm.base import LLMProvider, LLMResponse
from podcast_agent.llm.offline import OfflineProvider
from podcast_agent.tools.knowledge_base import KnowledgeBase
from podcast_agent.tools.web_search import MockWebSearch
from podcast_agent.trace import Tracer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID", "OPENAI_API_KEY", "TAVILY_API_KEY",
              "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "OTEL_EXPORTER_OTLP_ENDPOINT",
              "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "LLM_PROVIDER")


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    """Tests never reach the network or real credentials: a test that tries fails loudly."""
    import socket

    for name in SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    real_connect = socket.socket.connect

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if sock.family == socket.AF_UNIX or host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(sock, address)
        raise RuntimeError(f"tests must not open network connections (tried {address!r})")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


class ScriptedProvider(LLMProvider):
    """Returns queued responses per task; falls back to the offline heuristics when the queue is empty."""

    name = "scripted"

    def __init__(self, script: dict[str, list[Any]] | None = None):
        super().__init__("scripted", "scripted")
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[dict[str, Any]] = []
        self._fallback = OfflineProvider()

    def _complete(self, *, system, messages, model, max_tokens, temperature, cache_prefix, task, context):
        self.calls.append({"task": task, "messages": [dict(m) for m in messages], "context": context})
        queue = self.script.get(task)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            text = item if isinstance(item, str) else json.dumps(item)
            return LLMResponse(text=text, model=model, input_tokens=10, output_tokens=10)
        return self._fallback._complete(
            system=system, messages=messages, model=model, max_tokens=max_tokens, temperature=temperature,
            cache_prefix=cache_prefix, task=task, context=context,
        )


def make_ctx(provider: LLMProvider | None = None, **overrides) -> RunContext:
    settings = Settings(
        provider="offline",
        kb_path=str(DATA / "knowledge_base.json"),
        mock_web_path=str(DATA / "mock_web_index.json"),
        today="2026-10-13",
        cache_enabled=False,
    )
    for k, v in overrides.items():
        setattr(settings, k, v)
    return RunContext(
        settings=settings,
        llm=provider or OfflineProvider(),
        trace=Tracer("test", console=Console(quiet=True)),
        kb=KnowledgeBase.load(settings.kb_path),
        search=MockWebSearch(settings.mock_web_path),
    )


@pytest.fixture
def data_dir() -> Path:
    return DATA


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
