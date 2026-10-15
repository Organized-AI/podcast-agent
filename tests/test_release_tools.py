"""The helpers the publish and release workflows call."""

from __future__ import annotations

import importlib.util
import shutil

import pytest
import yaml
from conftest import ROOT


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DIGEST = "sha256:" + "ab" * 32


def test_set_image_rewrites_only_the_image_and_keeps_the_comment(tmp_path):
    env = tmp_path / "deploy" / "gitops" / "envs" / "dev"
    env.mkdir(parents=True)
    shutil.copy(ROOT / "deploy" / "gitops" / "envs" / "dev" / "values.yaml", env / "values.yaml")
    path = _load("set_image").set_image("dev", "sha-1a2b3c4", DIGEST, root=tmp_path)
    text = path.read_text()
    assert text.startswith("# Written by CI")
    assert yaml.safe_load(text) == {"image": {"tag": "sha-1a2b3c4", "digest": DIGEST}}


@pytest.mark.parametrize(("tag", "digest"), [("sha-1", "sha256:nope"), ("bad tag", DIGEST), ("x", "")])
def test_set_image_rejects_bad_input(tmp_path, tag, digest):
    with pytest.raises(ValueError):
        _load("set_image").set_image("dev", tag, digest, root=tmp_path)


def test_changelog_section_extracts_one_version():
    module = _load("changelog_section")
    text = (ROOT / "CHANGELOG.md").read_text()
    notes = module.section("0.1.0", text)
    assert notes.startswith("The take-home as submitted.") and "## [" not in notes and "[0.1.0]:" not in notes
    with pytest.raises(LookupError):
        module.section("9.9.9", text)


def test_releases_attach_the_signed_provenance():
    """Each GitHub Release carries the image's SLSA provenance, so verification does not need the attestations API."""
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())["jobs"]
    job = jobs["github-release"]
    assert job["permissions"] == {"contents": "write", "attestations": "read"}
    runs = [s.get("run", "") for s in job["steps"]]
    assert any("tools/ci/release_provenance.sh" in r for r in runs)
    script = (ROOT / "tools" / "ci" / "release_provenance.sh").read_text()
    assert '"https://slsa.dev/provenance/v1"' in script
    assert ".sigstore.json" in script and ".intoto.jsonl" in script


@pytest.mark.parametrize(("workflow", "job"), [("publish.yml", "bump-dev"), ("release.yml", "bump-prod")])
def test_deploy_pull_requests_are_opened_as_the_github_app(workflow, job):
    """App-authored pull requests run their checks; the workflow token is only the fallback, with dispatch."""
    steps = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text())["jobs"][job]["steps"]
    app = next(s for s in steps if s.get("id") == "app")
    assert app["uses"].startswith("actions/create-github-app-token@") and app["if"] == "vars.DEPLOY_APP_ID != ''"
    pr = next(s for s in steps if s.get("id") == "pr")
    assert pr["with"]["token"] == "${{ steps.app.outputs.token || github.token }}"
    dispatch = next(s for s in steps if "gh workflow run" in s.get("run", ""))
    assert dispatch["if"].startswith("steps.app.outputs.token == ''")

