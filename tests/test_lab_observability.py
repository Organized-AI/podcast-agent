"""The lab's observability stack (deploy/gitops/observability): the pieces agree with each other and with the chart."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from conftest import ROOT

OBS = ROOT / "deploy" / "gitops" / "observability"
APPS = ROOT / "deploy" / "gitops" / "argocd" / "observability.yaml"
BOOTSTRAP = ROOT / "deploy" / "gitops" / "lab" / "bootstrap.sh"
VALUES_DEV = ROOT / "deploy" / "helm" / "podcast-agent" / "values-dev.yaml"


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _manifests() -> list[dict]:
    kustomization = yaml.safe_load((OBS / "kustomization.yaml").read_text())
    return [d for f in kustomization["resources"] for d in _docs(OBS / f)]


def _pod_spec(doc: dict) -> dict:
    if doc["kind"] in ("Deployment", "StatefulSet", "Job"):
        return doc["spec"]["template"]["spec"]
    if doc["kind"] == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return {}


def _pod_labels(doc: dict) -> dict:
    template = (doc["spec"]["jobTemplate"]["spec"]["template"] if doc["kind"] == "CronJob"
                else doc["spec"].get("template", {}))
    return template.get("metadata", {}).get("labels", {})


def _config(name: str) -> dict:
    return yaml.safe_load((OBS / "config" / name).read_text())


def test_the_lab_collector_runs_the_compose_pipelines_plus_mlflow_auth():
    lab, compose = _config("collector.yaml"), yaml.safe_load(
        (ROOT / "deploy" / "observability" / "otel-collector.yaml").read_text())
    assert lab["service"]["pipelines"] == compose["service"]["pipelines"]
    mlflow = lab["exporters"]["otlp_http/mlflow"]
    assert mlflow["endpoint"] == "http://mlflow:5000"
    assert mlflow["headers"] == {"x-mlflow-experiment-id": "0", "Authorization": "Basic ${env:MLFLOW_BASIC_AUTH}"}
    assert lab["exporters"]["otlp_grpc/jaeger"]["endpoint"] == "jaeger:4317"
    # The names the Collector calls are Services in this stack (MLflow's from its chart's fullnameOverride).
    services = {d["metadata"]["name"] for d in _manifests() if d["kind"] == "Service"}
    assert {"jaeger", "otel-collector", "prometheus", "alertmanager", "postgres"} <= services
    assert yaml.safe_load((OBS / "mlflow-values.yaml").read_text())["fullnameOverride"] == "mlflow"


def test_prometheus_evaluates_the_repos_alert_rules_and_sends_them_to_alertmanager():
    kustomization = yaml.safe_load((OBS / "kustomization.yaml").read_text())
    prometheus = next(g for g in kustomization["configMapGenerator"] if g["name"] == "prometheus")
    assert "alerts.yml=../../observability/prometheus/alerts.yml" in prometheus["files"]
    config = _config("prometheus.yml")
    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]
    assert config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    assert config["scrape_configs"][0]["static_configs"][0]["targets"] == ["otel-collector:8889"]
    # Argo CD may read outside the kustomization's directory only because bootstrap.sh allows it.
    assert "--load-restrictor LoadRestrictionsNone" in BOOTSTRAP.read_text()


def test_alertmanager_posts_to_the_bridge_the_chart_deploys_in_dev():
    receiver = _config("alertmanager.yml")["receivers"][0]["webhook_configs"][0]
    assert receiver["send_resolved"] is True
    dev = yaml.safe_load(VALUES_DEV.read_text())
    port = dev.get("incidentBridge", {}).get("port", 8080)
    assert receiver["url"] == (f"http://podcast-agent-incident-bridge.podcast-agent-dev.svc.cluster.local:{port}"
                               "/alertmanager")


def test_the_tailnet_ports_line_up_from_the_node_to_the_issue_links():
    node_ports = {p["nodePort"] for d in _manifests() if d["kind"] == "Service" and d["spec"].get("type") == "NodePort"
                  for p in d["spec"]["ports"]}
    obs_ports = set(map(int, re.search(r'OBS_PORTS="([0-9 ]+)"', BOOTSTRAP.read_text()).group(1).split()))
    assert node_ports == obs_ports == {30686, 30500, 30909, 30903}
    env = yaml.safe_load(VALUES_DEV.read_text())["incidentBridge"]["env"]
    linked = {int(env[k].rsplit(":", 1)[1]) for k in ("JAEGER_UI_URL", "MLFLOW_UI_URL", "PROMETHEUS_UI_URL",
                                                      "ALERTMANAGER_UI_URL")}
    assert linked == obs_ports


def test_every_image_is_pinned_by_digest():
    images = [c["image"] for d in _manifests() for c in _pod_spec(d).get("containers", [])]
    images.append("ghcr.io/mlflow/mlflow:" + yaml.safe_load((OBS / "mlflow-values.yaml").read_text())["image"]["tag"])
    assert images and all(re.search(r"@sha256:[0-9a-f]{64}$", i) for i in images), images


def test_secrets_the_stack_reads_are_the_ones_bootstrap_creates():
    text = (OBS / "mlflow-values.yaml").read_text() + "".join(p.read_text() for p in OBS.glob("*.yaml"))
    read = set(re.findall(r"name: mlflow-secrets, key: ([A-Z_]+)", text)) | set(re.findall(r"key: (basic_auth\.ini)", text))
    created = set(re.findall(r"--from-literal=([A-Z_]+)=", BOOTSTRAP.read_text())) | {"basic_auth.ini"}
    assert read and read <= created, read - created
    assert "secretKeyRef: { name: mlflow-secrets, key: MLFLOW_BACKEND_STORE_URI }" in (OBS / "mlflow-values.yaml").read_text()


def test_argo_cd_may_deploy_everything_the_stack_contains():
    project, _secret, obs_app, mlflow_app = _docs(APPS)
    allowed = {(k["group"], k["kind"]) for k in project["spec"]["namespaceResourceWhitelist"]}
    kinds = {(d["apiVersion"].split("/")[0] if "/" in d["apiVersion"] else "", d["kind"]) for d in _manifests()}
    kinds |= {("", "ConfigMap")}  # the generated ones
    assert kinds <= allowed, kinds - allowed
    assert obs_app["spec"]["source"]["path"] == "deploy/gitops/observability"
    chart, values = mlflow_app["spec"]["sources"]
    assert (chart["repoURL"], chart["chart"], chart["targetRevision"]) == ("ghcr.io/mlflow/charts", "mlflow", "3.17.1")
    assert chart["helm"]["valueFiles"] == ["$values/deploy/gitops/observability/mlflow-values.yaml"]
    assert values["ref"] == "values" and chart["repoURL"] in project["spec"]["sourceRepos"]
    assert all(app["spec"]["destination"]["namespace"] == "observability" for app in (obs_app, mlflow_app))


def test_ingress_is_denied_unless_a_policy_allows_it():
    policies = {d["metadata"]["name"]: d for d in _manifests() if d["kind"] == "NetworkPolicy"}
    deny = policies["default-deny-ingress"]["spec"]
    assert deny["podSelector"] == {} and deny["policyTypes"] == ["Ingress"] and "ingress" not in deny
    # Every workload has a policy of its own, and every pod label a policy admits belongs to a workload here.
    workloads = {_pod_labels(d).get("app.kubernetes.io/name") for d in _manifests() if _pod_spec(d)}
    workloads |= {"mlflow"}  # from its chart
    selected = {p["spec"]["podSelector"].get("matchLabels", {}).get("app.kubernetes.io/name") for p in policies.values()}
    assert workloads - {"postgres-backup", "mlflow-bootstrap"} <= selected
    admitted = {peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"]
                for p in policies.values() for rule in p["spec"].get("ingress", []) for peer in rule.get("from", [])
                if "podSelector" in peer and "namespaceSelector" not in peer}
    assert admitted <= workloads, admitted - workloads


def test_only_mlflow_and_its_backup_reach_postgres():
    (policy,) = [d for d in _manifests() if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "postgres"]
    (rule,) = policy["spec"]["ingress"]
    assert [peer["podSelector"]["matchLabels"]["app.kubernetes.io/name"] for peer in rule["from"]] == [
        "mlflow", "postgres-backup"]
    assert rule["ports"] == [{"port": 5432, "protocol": "TCP"}]


def test_new_mlflow_users_start_with_no_permissions():
    text = BOOTSTRAP.read_text()
    assert "default_permission = NO_PERMISSIONS" in text and "default_permission = READ" not in text
    assert "--from-literal=USER_NAME=" in text  # the personal account, so day to day nobody signs in as admin


def test_argo_cd_reruns_the_mlflow_bootstrap_on_each_sync():
    (job,) = [d for d in _manifests() if d["kind"] == "Job"]
    # A Job's pod template is immutable: only delete-and-recreate picks up a new script or spec.
    assert job["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] == "Force=true,Replace=true"
    # A Job that deletes itself is recreated by self-heal and runs again a day later.
    assert "ttlSecondsAfterFinished" not in job["spec"]


def _mlflow_bootstrap(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "unused")
    spec = importlib.util.spec_from_file_location("mlflow_bootstrap", OBS / "config" / "mlflow_bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMlflow:
    """Answers the bootstrap's permission calls as MLflow 3.17 does: a second grant is 400 RESOURCE_ALREADY_EXISTS."""

    def __init__(self, grants):
        self.grants, self.calls = dict(grants), []

    def __call__(self, method, path, body=None, auth=None):
        self.calls.append(path.split("?")[0].rsplit("/", 1)[-1])
        if path.endswith("/grant"):
            if body["username"] in self.grants:
                return 400, '{"error_code": "RESOURCE_ALREADY_EXISTS"}'
            self.grants[body["username"]] = body["permission"]
            return 200, "{}"
        if path.endswith("/revoke"):
            del self.grants[body["username"]]
            return 200, "{}"
        user = path.split("username=")[1].split("&")[0]
        return (200, json.dumps({"permission": self.grants[user]})) if user in self.grants else (404, "{}")


@pytest.mark.parametrize(("before", "calls"), [
    ({}, ["grant", "get"]),
    ({"jordan": "READ"}, ["grant", "get", "get"]),
    ({"jordan": "EDIT"}, ["grant", "get", "revoke", "grant", "get"]),
], ids=["new", "same", "different"])
def test_a_rerun_of_the_mlflow_bootstrap_leaves_exactly_the_grant_it_asks_for(monkeypatch, before, calls):
    module = _mlflow_bootstrap(monkeypatch)
    mlflow = FakeMlflow(before)
    monkeypatch.setattr(module, "call", mlflow)
    module.grant("jordan", "READ")
    assert mlflow.grants == {"jordan": "READ"} and mlflow.calls == calls


def test_the_mlflow_bootstrap_stops_on_any_other_error(monkeypatch):
    module = _mlflow_bootstrap(monkeypatch)
    monkeypatch.setattr(module, "call", lambda *args, **kwargs: (403, '{"error_code": "PERMISSION_DENIED"}'))
    with pytest.raises(SystemExit, match="HTTP 403"):
        module.grant("jordan", "READ")
