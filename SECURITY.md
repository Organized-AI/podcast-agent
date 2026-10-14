# Security policy

## Supported versions

| Version | Supported |
|---|---|
| Latest release on `main` | Yes: fixes ship as a new patch release |
| Older releases | No: upgrade to the latest release |

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub:
[Report a vulnerability](https://github.com/Organized-AI/podcast-agent/security/advisories/new)
(Security tab → Advisories → Report a vulnerability). Do not open a public issue.

- We acknowledge every report within **7 days** and send a first assessment within 14 days.
- A confirmed critical issue gets a patch release as soon as a fix is verified; others ship in the next release, at most 60 days after confirmation.
- We credit reporters in the release notes unless you ask us not to.

## Threat model

The agent reads untrusted text (transcripts, web search results) and calls a hosted model with tools. The risks we design against:

| Risk | What could happen | Mitigation |
|---|---|---|
| Prompt injection through a transcript or a search result | Text tells the model to change a verdict or leak data | Tools are read-only and allow-listed; every verdict must cite evidence ids that exist; confidence is capped by the evidence in code; quotes and claims are re-matched against the transcript; a human reviews before publishing |
| Secrets exposure | API keys end up in logs, images, traces or the repo | Keys come only from the environment or a secret store (External Secrets in Kubernetes); `.env` is git-ignored and not copied into the image; traces carry no model or transcript text by default; gitleaks and GitHub secret scanning with push protection run on every push |
| Confidential podcast content | Transcript text leaves the process through telemetry | OpenTelemetry spans hold ids, counts, models and verdicts only; content capture is off unless `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` is set for debugging |
| Supply chain | A dependency, action or base image is compromised | `uv.lock` pins every Python dependency; actions are pinned to commit SHAs; Dependabot and CodeQL watch for known issues; images carry SBOM and build provenance attestations; Trivy scans the image and rendered manifests |
| Runtime privilege | A compromised pod reaches more than it needs | Non-root container (uid 10001), read-only root filesystem in Kubernetes, NetworkPolicy egress limited to the model and search endpoints, IRSA for scoped AWS access |

## Secure design

| Principle | How it shows up |
|---|---|
| Least privilege | Tools are read-only (knowledge-base lookup, web search); the container runs as uid 10001 with a read-only root filesystem; every CI job declares only the permissions it uses |
| Fail-safe defaults | A step that fails is recorded and its dependents skipped; a verifier that cannot finish defaults to ❓ unverifiable at low confidence; telemetry content capture is off by default; the HTTP API has no login of its own, so it is deployed as an internal service behind the cluster network policy (`make serve` binds 127.0.0.1 for local use) |
| Untrusted input is data | Transcripts and search results never become instructions: model output is parsed as JSON, validated against a schema and business rules, and evidence ids must exist |
| Complete mediation | Every model output is checked before use: quotes and claims are re-matched against the transcript, confidence is capped in code by the evidence gathered |
| Economy of mechanism | One pipeline for the CLI, the API and the worker; no plugins, no dynamic code loading, no shell |
| Auditability | Every run writes a manifest with SHA-256 hashes of its inputs and outputs; `podcast-agent audit` re-checks a run from its files |

## Common errors we guard against

| Error | Guard |
|---|---|
| Prompt injection (OWASP LLM01) | See the threat model above; verdicts need cited evidence and pass code checks the model cannot override |
| Path traversal (CWE-22) | Episode ids from transcript content are reduced to `[A-Za-z0-9._-]` before they name an output file, so an id like `../../etc/x` cannot leave the output folder |
| Command injection (CWE-78) | The only subprocess is `git rev-parse` with an argument list and no shell |
| Unsafe deserialization (CWE-502) | Inputs are JSON or plain text, parsed as data; nothing is unpickled, evaluated or imported at run time |
| Secrets in logs (CWE-532) | Keys are read from the environment and never logged; traces and spans carry ids and counts, not prompts |
| Vulnerable dependencies | `uv.lock` pins versions; Dependabot (with a 7-day cooldown), dependency review and Trivy watch them |

Static analysis for these runs on every pull request: CodeQL, ruff with bandit rules, mypy, zizmor and Trivy. See [docs/OPENSSF-BEST-PRACTICES.md](docs/OPENSSF-BEST-PRACTICES.md) for how the project meets the OpenSSF Best Practices criteria.

## Known vulnerabilities

None at this time. Fixed vulnerabilities are listed under **Security** in [CHANGELOG.md](CHANGELOG.md).

## Verifying a release

Images are published to `ghcr.io/organized-ai/podcast-agent` with build provenance:

```bash
gh attestation verify oci://ghcr.io/organized-ai/podcast-agent:v0.2.0 --repo Organized-AI/podcast-agent
```
