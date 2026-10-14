# Contributing

Thanks for helping. This project turns podcast transcripts into show notes and an audited fact-check; changes are judged by whether the output stays correct and checkable.

## Set up

```bash
git clone https://github.com/Organized-AI/podcast-agent && cd podcast-agent
uv sync --locked --extra aws        # Python 3.11+, installs the dev group too
uv run pre-commit install           # optional: run the hooks on every commit
make help                           # every target, grouped by area
```

No API key is needed for development: `LLM_PROVIDER=offline` runs the whole pipeline on deterministic heuristics, and the test suite never touches the network.

## Quality gates

Every pull request must pass `make check` locally and the **PR Gate** check in CI:

| Gate | Command | Fails on |
|---|---|---|
| Lint | `make lint` | Any ruff finding (includes bandit security rules) |
| Types | `make types` | Any mypy error |
| Tests | `make test` | A failing test, any warning, coverage below the floor in `pyproject.toml` |
| Drift | `make drift` | Stale `config/settings.yaml`, a variable missing from `.env.example`, versions that disagree |
| Evals | `make evals` | A metric below its threshold in `evals/suites/` |
| Manifests | `make helm` | A Helm render or Argo CD manifest that does not validate |

The coverage floor is a ratchet: raise it when coverage goes up, never lower it.

## Test policy

Every behavior change ships with a test that fails before the change and passes after it. Bug fixes add a regression test; new verdict or eval behavior adds an eval case in `evals/datasets/`. Tests are hermetic: `tests/conftest.py` blocks outbound network and scrubs credentials.

## Pull requests

1. Open or pick an issue first, so the change has a stated purpose.
2. Branch from `main`, keep the PR to one concern, and fill in the template (purpose, test plan, test result).
3. Add a line under `[Unreleased]` in `CHANGELOG.md` for anything a user would notice.
4. PRs are squash-merged once the required checks are green. Commit and PR titles are short and imperative ("Add trace context to the worker").

`main` is protected by a repository ruleset: no direct pushes, force pushes or deletion; changes land only through squash-merged pull requests whose required checks pass (PR Gate, gitleaks, zizmor, Trivy, CodeQL for Python and Actions, dependency review) and that add no high-severity CodeQL alert.

## Response times

We respond to new issues and pull requests within 7 days. Security reports follow [SECURITY.md](SECURITY.md).

## Releases

Releases follow `X.Y.Z` with tags `vX.Y.Z`:

1. Move the `[Unreleased]` entries into a `## [X.Y.Z] - YYYY-MM-DD` section of `CHANGELOG.md`.
2. Bump `pyproject.toml`, `src/podcast_agent/__init__.py` and `deploy/helm/podcast-agent/Chart.yaml` together; `make drift` checks they agree.
3. Check that [docs/OPENSSF-BEST-PRACTICES.md](docs/OPENSSF-BEST-PRACTICES.md) still holds.
4. Merge, then tag `vX.Y.Z` on `main` (a tag ruleset stops `v*` tags from being moved or deleted). The release workflow re-tags the already-tested image, creates the GitHub Release from the changelog and opens the production bump PR.

## Out of scope

Hosting a public transcription service, storing transcripts beyond a run, and fact-checking against sources that require scraping behind logins.
