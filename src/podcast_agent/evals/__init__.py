"""Evals: score the agent against labeled data, with thresholds that gate a change.

* ``datasets``: the labeled fact-check cases and per-episode expectations under ``evals/datasets``.
* ``metrics``: pure scoring functions (accuracy, macro-F1, calibration, citations, coverage, quotes).
* ``runner``: runs a suite (``evals/suites/*.yaml``): fact-check cases through the verifier with the
  configured provider, and committed run folders scored without calling a model.
"""
