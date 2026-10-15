"""The Argo CD manifests point at files that exist, and every environment renders the same way Argo CD will."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ARGO = ROOT / "deploy" / "gitops" / "argocd"
CHART = ROOT / "deploy" / "helm" / "podcast-agent"


def _appset() -> dict:
    return yaml.safe_load((ARGO / "applicationset.yaml").read_text())


def test_every_environment_has_its_values_files_and_a_destination():
    appset = _appset()
    project = yaml.safe_load((ARGO / "project.yaml").read_text())
    source = appset["spec"]["template"]["spec"]["source"]
    assert (ROOT / source["path"]) == CHART
    allowed = {d["namespace"] for d in project["spec"]["destinations"]}
    envs = [e["env"] for e in appset["spec"]["generators"][0]["list"]["elements"]]
    assert envs == ["dev", "prod"]
    for env in envs:
        for template in source["helm"]["valueFiles"]:
            path = (CHART / template.replace("{{ .env }}", env)).resolve()
            assert path.exists(), path
            assert ROOT in path.parents, f"{path} is outside the repository; Argo CD would refuse it"
        assert f"podcast-agent-{env}" in allowed
    assert source["repoURL"] in project["spec"]["sourceRepos"]


def test_only_dev_syncs_itself():
    elements = {e["env"]: e["autoSync"] for e in _appset()["spec"]["generators"][0]["list"]["elements"]}
    assert elements == {"dev": True, "prod": False}
    assert "automated" in _appset()["spec"]["templatePatch"]
    assert "automated" not in _appset()["spec"]["template"]["spec"]["syncPolicy"]


def test_the_project_permits_the_namespace_argo_cd_creates_and_nothing_else_cluster_wide():
    """CreateNamespace=true is checked against clusterResourceWhitelist; an empty list fails every sync."""
    project = yaml.safe_load((ARGO / "project.yaml").read_text())
    options = _appset()["spec"]["template"]["spec"]["syncPolicy"]["syncOptions"]
    assert "CreateNamespace=true" in options
    assert project["spec"]["clusterResourceWhitelist"] == [{"group": "", "kind": "Namespace"}]


def test_environment_values_only_pin_the_image():
    """Environment shape lives in the chart's values-<env>.yaml; gitops/envs holds only what CI writes."""
    for env in ("dev", "prod"):
        values = yaml.safe_load((ROOT / "deploy" / "gitops" / "envs" / env / "values.yaml").read_text())
        assert set(values) == {"image"} and set(values["image"]) <= {"tag", "digest"}


def test_e2e_render_targets_one_environment_and_the_local_image():
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_appset", ROOT / "e2e" / "render_appset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    doc = module.render("abc123", "dev", "podcast-agent:e2e")
    assert [e["env"] for e in doc["spec"]["generators"][0]["list"]["elements"]] == ["dev"]
    source = doc["spec"]["template"]["spec"]["source"]
    params = {p["name"]: p["value"] for p in source["helm"]["parameters"]}
    assert source["targetRevision"] == "abc123"
    assert params == {"image.repository": "podcast-agent", "image.tag": "e2e", "image.digest": "", "image.pullPolicy": "Never"}
