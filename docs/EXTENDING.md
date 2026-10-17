# Extending the agent

How to add a feature and prove it end to end. The checklist for a new pipeline step comes from a full rehearsal: an optional `detect_ad_reads` step, built, tested, run offline and audited. Each gotcha listed was actually hit, not guessed.

## Run it end to end

```bash
uv sync --locked --extra aws                                     # once
uv run pytest -q tests/test_<yours>.py                           # the new tests, under a second
make check                                                       # lint, types, every test, drift, offline evals (~15 s)
uv run podcast-agent run data/transcripts --provider offline --out output    # whole pipeline, no key, under a second
uv run podcast-agent audit output                                # re-checks every guarantee from the files
```

With Claude, put `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`), then:

```bash
make run && make audit                                           # same as above with the model; writes output/
docker build -t podcast-agent . && docker run --rm -t --env-file .env -v "$PWD/output:/app/output" podcast-agent
uv run podcast-agent serve --host 127.0.0.1                      # HTTP: POST /v1/process returns the full report
make observe                                                     # the run as a trace in Jaeger and MLflow
```

`output/logs/run.log` shows each step's plan, model calls, self-corrections and verdicts as they happen. Read it first when something looks wrong.

## Add a pipeline step (a new output)

A step's name appears in five places, so a new step touches seven source files. `tests/test_step_registry.py` fails if the name is missing from any of the registries below, or if the offline provider can't run the step.

| # | File | What to add |
|---|---|---|
| 1 | `models.py` | The name in `StepName`. An output model. A field on `EpisodeReport`: `list[X] \| None = None`, where `None` means the step didn't run and `[]` means it ran and found nothing. |
| 2 | `prompts.py` | A system prompt and a task prompt asking for JSON only, with no reasoning field (AGENTS.md rule 4). Bump `PROMPT_VERSION`. |
| 3 | `agent/steps.py` | `@register("<name>")` on a function that calls `call_structured(task=..., validate=...)`. `validate` returns a list of specific problems, which go back to the model. After the call, ground the output: copy exact timestamps and speakers from the transcript line, and drop anything that doesn't match. Store the result on `state`. |
| 4 | `agent/pipeline.py` | A field on `EpisodeState`. Pass it into `EpisodeReport(...)` in `_process_episode`. |
| 5 | `agent/planner.py` | The name in `CANONICAL_ORDER`, at its dependency position. An entry in `capability_catalog()` with `required`, `requires` and `params`. If the step only makes sense sometimes, add a rule to the planner prompt and the signal it uses to `profile_episode()`. |
| 6 | `heuristics.py` | An offline handler in `HANDLERS`, keyed by the `task=` name you passed to `call_structured`. If the planner prompt gained a rule, repeat it in `plan()`. |
| 7 | `report.py` | Render the field in `render_markdown()`. The combined `report.md` reuses it, and JSON output picks up the field on its own. Add an `index.md` column if a count is useful. |

Then write tests with `ScriptedProvider` (`tests/conftest.py`). It returns your scripted responses per task and falls back to the offline heuristics. Cover three things:
- a bad response is rejected and the problem is fed back (`provider.calls[1]["messages"][-1]`);
- a good response lands in the report;
- `run_batch` writes it to the Markdown and JSON.

Optional, for consistency:
- `audit.py`: a check that re-verifies the new output from the files.
- `trace.py`: a short `STEP_LABEL` for the console.
- `pipeline.process_episode`: a span attribute holding counts only, never transcript text.
- A `CHANGELOG.md` line.

These need nothing:
- **HTTP API and worker:** the API returns the whole report, and the worker doesn't change.
- **Cost:** tracked per step automatically.
- **Settings:** `config/settings.yaml` doesn't change unless you add a setting.

### Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| The step never appears in a model-written plan, with no warning | Name missing from `StepName`, so the plan fails schema validation and the retry drops it | Add it to the `Literal` |
| `ValueError: '<name>' is not in list` from `repair_plan` | Name missing from `CANONICAL_ORDER` (only hit when a plan is out of order three times) | Add it; it is also the fallback plan's order |
| "offline provider has no heuristic for task '<task>'" in offline runs and tests | No `HANDLERS` entry for the task | Add the handler, keyed by `task=`, not by the step name |
| The offline planner never schedules an optional step | The planner rule lives in the prompt but not in `heuristics.plan()` | Repeat the rule in `plan()` |
| A resubmitted episode comes back without the new output | The worker's idempotency key is the input hash + `PROMPT_VERSION` + model ids | Bump `PROMPT_VERSION` |
| An exact-timestamp check fails on `0:30` vs `00:30` | `find_timestamp` matches by seconds | Copy the matched line's own `timestamp` string into the output |
| The new output shows in `steps` but not in the report | Field set on `state` but not passed to `EpisodeReport(...)` | Item 4; mypy flags it |

## Other changes

| To | Where |
|---|---|
| Give the fact-checker a new tool | `agent/verifier.py`: `TOOLS`, the `action` literal, and `run_tool()`. Its description goes in `prompts.verify_system()`; the offline `heuristics.verify_step()` chooses tools. Evidence needs a source and a date so confidence can be capped. |
| Add a setting | A field on `config.Settings` read with `_env(...)`, the variable with a comment in `.env.example`, then `make config` to regenerate `config/settings.yaml` (CI checks it) |
| Add a model provider | A class implementing `LLMProvider._complete` in `llm/`, and a branch in `llm.build_provider()` |
| Add an HTTP endpoint | `server.py` (FastAPI), tested with `fastapi.testclient.TestClient` as in `tests/test_server.py` |
| Add a CLI command | `cli.py`: `sub.add_parser(...)` and a `cmd_*` function |
| Change a prompt | `prompts.py`, bump `PROMPT_VERSION`, then run the live evals (`nightly-evals` workflow, or `podcast-agent eval --suite evals/suites/live.yaml` with a key) |
| Expect a new claim or summary detail | `evals/datasets/episodes.yaml`. Match on words only that claim contains: a broad match can catch a neighbouring claim first. |

Before you finish, read `git diff`. Ruff doesn't flag every formatting slip.
