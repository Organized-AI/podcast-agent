"""Generated files and stated versions must agree with their sources (the same checks `make drift` runs)."""

import importlib.util
import sys

from conftest import ROOT


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_settings_reference_is_current(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gen_config.py", "--check"])
    assert _load("gen_config").main() == 0


def test_versions_agree(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_version.py"])
    assert _load("check_version").main() == 0
