"""The Collector fans every trace out to Jaeger and MLflow, and the end-to-end check holds both to the same trace."""

import importlib.util
import json
import re

import pytest
import yaml
from conftest import ROOT

COLLECTOR = ROOT / "deploy" / "observability" / "otel-collector.yaml"
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"
OBSERVE_MK = ROOT / "tools" / "make" / "observability.mk"


def _check_telemetry():
    spec = importlib.util.spec_from_file_location("check_telemetry", ROOT / "e2e" / "check_telemetry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_traces_pipeline_exports_to_jaeger_and_mlflow():
    config = yaml.safe_load(COLLECTOR.read_text())
    assert config["service"]["pipelines"]["traces"]["exporters"] == ["otlp_grpc/jaeger", "otlp_http/mlflow"]
    mlflow = config["exporters"]["otlp_http/mlflow"]
    # MLflow ingests OTLP over HTTP only, and files traces under the experiment named in this header.
    assert "x-mlflow-experiment-id" in mlflow["headers"]
    assert mlflow["endpoint"].endswith("http://mlflow:5000}")
    # Metrics still go to Prometheus alone: MLflow's OTLP endpoint takes traces.
    assert config["service"]["pipelines"]["metrics"]["exporters"] == ["prometheus"]


def test_mlflow_service_is_pinned_local_and_reachable_from_the_collector():
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    mlflow = services["mlflow"]
    assert mlflow["profiles"] == ["observability"]
    assert re.fullmatch(r"ghcr\.io/mlflow/mlflow:v3\.\d+\.\d+@sha256:[0-9a-f]{64}", mlflow["image"])
    assert all(port.startswith("127.0.0.1:") for port in mlflow["ports"])
    args = dict(arg.removeprefix("--").split("=", 1) for arg in mlflow["command"] if arg.startswith("--"))
    assert args["backend-store-uri"].startswith("sqlite:"), "OTLP ingest needs a SQL backend store"
    # MLflow rejects Host headers it doesn't know (DNS-rebinding guard); the Collector calls it as mlflow:5000.
    assert "mlflow:5000" in args["allowed-hosts"].split(",")
    assert "mlflow" in services["otel-collector"]["depends_on"]
    assert "healthcheck" in mlflow


def test_make_observe_starts_and_checks_mlflow():
    target = OBSERVE_MK.read_text().split("observe:", 1)[1].split("observe-down:", 1)[0]
    assert "up -d --wait otel-collector jaeger mlflow" in target
    assert "e2e/check_telemetry.py" in target  # whose --mlflow default is the Compose service
    assert _check_telemetry().__doc__.startswith("End-to-end check: a run's trace reached Jaeger and MLflow")


def test_make_observe_outage_proves_one_backend_down_does_not_stop_the_other():
    target = OBSERVE_MK.read_text().split("observe-outage:", 1)[1].split("observe-down:", 1)[0]
    stop, down_check, start, up_check = (target.index(s) for s in (
        "stop mlflow", '--mlflow ""', "up -d --wait mlflow", "--timeout 180"))
    assert stop < down_check < start < up_check
    assert "make observe-outage" in (ROOT / ".github" / "workflows" / "ci.yml").read_text()


def _span(name: str, span_type: str | None = None, **extra: str) -> dict:
    attributes = [{"key": "mlflow.traceRequestId", "value": {"string_value": "tr-abc"}}]
    if span_type:
        attributes.append({"key": "mlflow.spanType", "value": {"string_value": span_type}})
    attributes += [{"key": k, "value": {"string_value": v}} for k, v in extra.items()]
    return {"name": name, "attributes": attributes}


def _trace(spans: list[dict], tokens: int = 1200) -> dict:
    return {
        "trace_info": {
            "trace_id": "tr-abc",
            "state": "OK",
            "trace_location": {"type": "MLFLOW_EXPERIMENT", "mlflow_experiment": {"experiment_id": "0"}},
            "trace_metadata": {"mlflow.trace.tokenUsage": json.dumps({"total_tokens": tokens})},
        },
        "spans": spans,
    }


GOOD = [
    _span("run podcast-agent"),
    _span("invoke_agent podcast-agent", "AGENT"),
    _span("chat claude-haiku-5-5", "CHAT_MODEL"),
    _span("execute_tool search_knowledge_base", "TOOL"),
]


def test_mlflow_check_passes_when_it_has_the_same_trace_as_jaeger():
    results = _check_telemetry().mlflow_results(_trace(GOOD), expected_spans=4, episodes=1, capture=False)
    assert all(ok for ok, _ in results), results


@pytest.mark.parametrize(
    ("trace", "expected_spans", "failing"),
    [
        (_trace(GOOD[1:]), 4, "MLflow got every span Jaeger got (3/4)"),
        (_trace(GOOD, tokens=0), 4, "token usage rolled up on the trace (0 tokens)"),
        (_trace([*GOOD[:2], _span("chat m", "CHAT_MODEL", **{"mlflow.spanInputs": "[...]"}), GOOD[3]]), 4,
         "no prompt, completion or query text in MLflow (found ['mlflow.spanInputs'])"),
    ],
    ids=["dropped-span", "no-token-usage", "content-leak"],
)
def test_mlflow_check_fails_on_a_partial_or_leaky_copy(trace, expected_spans, failing):
    results = _check_telemetry().mlflow_results(trace, expected_spans=expected_spans, episodes=1, capture=False)
    assert [line for ok, line in results if not ok] == [failing]
