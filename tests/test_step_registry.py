"""A pipeline step is named in several places; these tests fail when a new one is missing from any of them.

See docs/EXTENDING.md. A step missing from StepName silently drops out of every model-written plan; one missing from
CANONICAL_ORDER crashes plan repair; one without an offline heuristic fails in offline runs and in tests.
"""

from __future__ import annotations

from typing import get_args

from conftest import ScriptedProvider, make_ctx

from podcast_agent.agent.pipeline import REGISTRY, process_episode
from podcast_agent.agent.planner import CANONICAL_ORDER, capability_catalog
from podcast_agent.models import StepName
from podcast_agent.transcript import load_episode


def test_every_step_is_declared_in_every_registry():
    names = set(get_args(StepName))
    assert set(CANONICAL_ORDER) == names, "add the step to planner.CANONICAL_ORDER (repair order, fallback plan)"
    assert set(capability_catalog(make_ctx())) == names, "add the step to planner.capability_catalog()"


def test_a_plan_with_every_step_runs_offline_with_none_failed(data_dir):
    """The offline provider (and so every test's fallback) must handle each step's model task."""
    plan = {"goal": "every capability", "steps": [{"step": s, "reason": "registry test", "params": {}}
                                                  for s in CANONICAL_ORDER]}
    ctx = make_ctx(ScriptedProvider({"plan": [plan]}))
    report = process_episode(ctx, load_episode(data_dir / "transcripts" / "ep004_pdf_example.txt"), "registry")
    # Running the pipeline imports the step modules, so REGISTRY is complete now.
    assert set(REGISTRY) == set(CANONICAL_ORDER), "decorate the step with @register(...) in a module the pipeline imports"
    ran = {r.step: r.status for r in report.steps if r.step != "plan"}
    assert set(ran) == set(CANONICAL_ORDER)
    assert all(status == "ok" for status in ran.values()), ran
