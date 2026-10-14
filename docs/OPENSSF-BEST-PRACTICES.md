# OpenSSF Best Practices: passing criteria

This maps every criterion of the [OpenSSF Best Practices](https://www.bestpractices.dev/en/criteria/0) **passing** level to where this repository meets it. It is the source for the project's entry on bestpractices.dev; when a practice changes, update the row here in the same pull request.

Reference entry: [colinmcnamara/gh-upstream-watch](https://www.bestpractices.dev/en/projects/15235/passing), which meets all 67 criteria.

Status: **Met** unless marked N/A. 67 criteria: 13 basics, 9 change control, 8 reporting, 13 quality, 16 security, 8 analysis.

## Basics

| Criterion | Evidence |
|---|---|
| `description_good` | The [README](../README.md) opens with what the system does (show notes and an audited fact-check from podcast transcripts) and who it is for (ad agencies and producers publishing show notes). |
| `interact` | The README links the issue tracker, issue forms for bugs and features ([.github/ISSUE_TEMPLATE](../.github/ISSUE_TEMPLATE)), private vulnerability reporting and the interactive guide. |
| `contribution` | [CONTRIBUTING.md](../CONTRIBUTING.md) covers setup, quality gates, the test policy, pull requests and releases. |
| `contribution_requirements` | CONTRIBUTING.md lists the requirements: `make check` and the PR Gate pass, a test that fails before the change and passes after, a CHANGELOG line, one concern per pull request. |
| `floss_license` | MIT ([LICENSE](../LICENSE)). |
| `floss_license_osi` | MIT is approved by the Open Source Initiative. |
| `license_location` | [LICENSE](../LICENSE) at the repository root. |
| `documentation_basics` | The README (quickstart, configuration, how the agent works, project layout, making changes), [docs/DEPLOYMENT.md](DEPLOYMENT.md) and the [interactive guide](https://podcast-agent-guide.jordan-691.workers.dev). |
| `documentation_interface` | The README documents every command (`run`, `serve`, `worker`, `audit`, `kb`) and the HTTP API; [config/settings.yaml](../config/settings.yaml) lists every setting, generated from the code; output formats are typed in `src/podcast_agent/models.py`. |
| `sites_https` | The repository, releases, container registry (GHCR) and the guide are served only over HTTPS. |
| `discussion` | GitHub issues and pull requests: searchable, addressable by URL, open to anyone with a GitHub account. |
| `english` | Documentation, issues and code review are in English, and reports in English are accepted. |
| `maintained` | Active: see the [commit history](https://github.com/Organized-AI/podcast-agent/commits/main), the [v0.2.0 milestone](https://github.com/Organized-AI/podcast-agent/milestone/1) and [releases](https://github.com/Organized-AI/podcast-agent/releases). |

## Change control

| Criterion | Evidence |
|---|---|
| `repo_public` | Public git repository at [github.com/Organized-AI/podcast-agent](https://github.com/Organized-AI/podcast-agent). |
| `repo_track` | git records every change, its author and its date. |
| `repo_interim` | Every change reaches `main` through a pull request that must pass the required checks (repository ruleset on `main`); interim work is visible on branches and pull requests between releases. |
| `repo_distributed` | git is distributed. |
| `version_unique` | Each release has a unique `X.Y.Z` version, set in `pyproject.toml`, `__version__` and the Helm chart; `tools/ci/check_version.py` fails CI if they disagree. |
| `version_semver` | Versions follow Semantic Versioning (stated in [CHANGELOG.md](../CHANGELOG.md) and CONTRIBUTING.md). |
| `version_tags` | Every release is a git tag `vX.Y.Z` on the merged commit; a tag ruleset blocks deleting or moving `v*` tags. |
| `release_notes` | [CHANGELOG.md](../CHANGELOG.md) in Keep a Changelog format; each GitHub Release is created from its section. |
| `release_notes_vulns` | No publicly known vulnerability (CVE or similar) has been fixed in a release. Security fixes are listed under a **Security** heading in the changelog. |

## Reporting

| Criterion | Evidence |
|---|---|
| `report_process` | Bugs and features go through GitHub Issues with forms; vulnerabilities through [SECURITY.md](../SECURITY.md). |
| `report_tracker` | GitHub Issues with labels, a milestone and issue forms. |
| `report_responses` | The README and CONTRIBUTING.md commit to a response to every issue and pull request within 7 days. |
| `enhancement_responses` | Feature requests have their own issue form and the same 7-day response commitment. |
| `report_archive` | GitHub Issues and pull requests keep every report and response public and searchable, open and closed. |
| `vulnerability_report_process` | SECURITY.md publishes the process: report privately through GitHub private vulnerability reporting. |
| `vulnerability_report_private` | Private vulnerability reporting is enabled on the repository, so reports reach only the maintainers. |
| `vulnerability_report_response` | SECURITY.md commits to acknowledging a report within 7 days, inside the 14-day limit. |

## Quality

| Criterion | Evidence |
|---|---|
| `build` | `uv sync --locked` builds and installs from source; CI and the Docker build rebuild on every change. |
| `build_common_tools` | Standard Python packaging: uv, a PEP 517 setuptools backend and Docker. |
| `build_floss_tools` | Every build tool is free software: Python, uv, setuptools, Docker, Helm. |
| `test` | An automated pytest suite in `tests/`, MIT-licensed like the rest of the repository. |
| `test_invocation` | `make test` (or `uv run pytest`), configured in `pyproject.toml`. |
| `test_most` | Branch coverage is measured in CI and the build fails below the floor in `pyproject.toml`; the floor is a ratchet that only goes up. An offline end-to-end run, the audit of two committed runs and a Docker run of every transcript also run on every change. |
| `test_continuous_integration` | GitHub Actions runs the PR Gate on every pull request and every push to `main`. |
| `test_policy` | CONTRIBUTING.md: every behavior change ships with a test that fails before the change and passes after it; verdict or quality changes also add an eval case. |
| `tests_are_added` | Pull requests follow the policy; the PR template asks for the failing-before test and its result. |
| `tests_documented_added` | The test policy is in CONTRIBUTING.md, AGENTS.md and the PR template checklist. |
| `warnings` | ruff with a wide rule set (pyflakes, pycodestyle, isort, bugbear, pyupgrade, bandit security rules, simplify, ruff-specific) and mypy. |
| `warnings_fixed` | CI fails on any ruff finding, mypy error or pytest warning, so none reach `main`. |
| `warnings_strict` | pytest runs with `--strict-markers`, `--strict-config` and `filterwarnings = ["error", ...]`; each ruff ignore in `pyproject.toml` carries its reason. |

## Security

| Criterion | Evidence |
|---|---|
| `know_secure_design` | The design applies secure-design principles; see [SECURITY.md → Secure design](../SECURITY.md#secure-design): least privilege, fail-safe defaults, untrusted input treated as data, complete mediation of model output. |
| `know_common_errors` | SECURITY.md → Common errors we guard against: prompt injection, path traversal from transcript-supplied ids, command injection, unsafe deserialization, secrets in logs and telemetry. |
| `crypto_published` | The only cryptography is SHA-256 (FIPS 180-4) for run-manifest integrity, through Python's `hashlib`. |
| `crypto_call` | Calls `hashlib`; implements no cryptography of its own. TLS is provided by the HTTP client libraries. |
| `crypto_floss` | `hashlib` (OpenSSL) and the TLS stack are free software. |
| `crypto_keylength` | N/A: the software creates and stores no keys. |
| `crypto_working` | No broken algorithms: SHA-256 only, no MD5 or SHA-1 for security. |
| `crypto_weaknesses` | No algorithms with known serious weaknesses. |
| `crypto_pfs` | N/A: the software implements no key agreement protocol; outbound TLS uses the client library defaults, which negotiate forward-secret ciphers. |
| `crypto_password_storage` | N/A: the software stores no passwords. |
| `crypto_random` | N/A: no cryptographic keys or nonces are generated; run ids use `uuid4` only to be unique, not secret. |
| `delivery_mitm` | Source, releases and images are delivered over HTTPS (GitHub, GHCR); images are referenced by digest in the GitOps environments. |
| `delivery_unsigned` | No hash is fetched over HTTP; CI verifies the gitleaks download against a SHA-256 committed in the workflow. |
| `vulnerabilities_fixed_60_days` | No unpatched vulnerability of medium or higher severity has been public for more than 60 days. Dependabot and Trivy watch the dependencies and the image. |
| `vulnerabilities_critical_fixed` | SECURITY.md: a confirmed critical issue gets a patch release as soon as a fix is verified. |
| `no_leaked_credentials` | No credentials are in the repository: gitleaks scans the full history on every change and as a pre-commit hook, and GitHub secret scanning with push protection is on. `.env` is git-ignored. |

## Analysis

| Criterion | Evidence |
|---|---|
| `static_analysis` | CodeQL (Python and Actions), ruff with bandit rules, mypy, zizmor and Trivy config scanning. |
| `static_analysis_common_vulnerabilities` | CodeQL `security-extended` queries, ruff's bandit (`S`) rules, zizmor for workflow injection and permissions, Trivy for Dockerfile and Kubernetes misconfiguration. |
| `static_analysis_fixed` | CI fails on ruff, mypy, zizmor and Trivy (high or critical) findings, so they are fixed before merge; CodeQL alerts are triaged in the Security tab. |
| `static_analysis_often` | On every pull request and every push to `main`, plus a weekly scheduled run. |
| `dynamic_analysis` | Every change runs the test suite with branch coverage, an offline end-to-end run with its audit, re-audits of the committed runs and a Docker run of every transcript. |
| `dynamic_analysis_unsafe` | N/A: the project is Python, a memory-safe language, with no C extensions of its own. |
| `dynamic_analysis_enable_assertions` | Tests run with assertions on (never `python -O`) and treat every warning as an error. |
| `dynamic_analysis_fixed` | A failing test, audit or end-to-end run blocks the merge, so problems are fixed first. |

## Keeping the badge

- Run through this table when cutting a release (`CONTRIBUTING.md → Releases`).
- A practice that stops being true is a bug: open an issue labelled `area/security`.
