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
| Runtime privilege | A compromised pod reaches more than it needs | Non-root container (uid 10001), read-only root filesystem in Kubernetes, a NetworkPolicy that admits traffic only to the API port and allows egress only to DNS and HTTPS (Kubernetes NetworkPolicy cannot filter by hostname, so pin destinations with an egress gateway or an FQDN-aware CNI policy), IRSA for scoped AWS access |

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

## Government and regulated deployments

Podcast Agent is open-source software, not a hosted service. It holds no FedRAMP authorization or agency ATO; a deploying organization runs it inside its own authorization boundary and assesses it there. This section is the evidence and the configuration that assessment needs.

### Secure development practices (NIST SP 800-218, SSDF 1.1)

| Practice | Evidence in this repository |
|---|---|
| PO.1 Define security requirements | The threat model, secure-design principles and common errors above; `AGENTS.md` and `CONTRIBUTING.md` set the rules every change follows (no secrets, every check green) |
| PO.3 Implement supporting toolchains | CI on every pull request: ruff with bandit rules, mypy, 100+ tests with a coverage floor, the offline eval suite, CodeQL, gitleaks, zizmor, Trivy and dependency review ([.github/workflows](.github/workflows)) |
| PO.5 Secure the development environment | `main` takes only squash-merged pull requests whose seven required checks pass and that add no high-severity CodeQL alert; force-pushes and deletion are blocked; every workflow sets its token permissions explicitly (read-only or none at the top) and each job asks for only what it uses; actions are pinned to commit SHAs |
| PS.1 Protect code from unauthorized change | Repository rulesets above; secret scanning with push protection; release tags `v*` cannot be moved or deleted |
| PS.2 Let users verify release integrity | Every image carries a signed SLSA build-provenance attestation naming the workflow and commit that built it (see [Verifying a release](#verifying-a-release)) |
| PS.3 Archive and protect each release | Images are addressed by digest; a release re-tags the digest `main` already built and tested instead of rebuilding; each image has an SBOM; the GitHub Release records the digest |
| PW.1 Design to meet security requirements | Read-only, allow-listed tools; model output validated against schemas and business rules; confidence capped by evidence in code |
| PW.4 Reuse well-secured components | `uv.lock` pins every Python dependency with hashes; base images are pinned by digest; Dependabot proposes updates after a 7-day cooldown |
| PW.7 Review and analyze code | Every change is a pull request; CodeQL (Python and Actions), ruff/bandit, mypy and zizmor run on each one |
| PW.8 Test executable code | Unit and integration tests, the Docker image run end to end, the Helm chart rendered and schema-checked for every environment, Argo CD deploying the chart on a kind cluster, and labeled evals of the agent's verdicts |
| PW.9 Secure settings by default | Telemetry content capture off, the API bound to localhost outside Kubernetes, non-root read-only containers, NetworkPolicy on, failed steps degrade to ❓ unverifiable |
| RV.1 Find vulnerabilities continuously | CodeQL and Trivy on every pull request, every push to `main` and weekly; dependency review on every pull request; Dependabot and OpenSSF Scorecard weekly |
| RV.2 Assess and fix them | Private reporting with the response times above; fixes ship as a patch release and are listed under **Security** in the changelog |
| RV.3 Find root causes | Each fixed vulnerability gets a regression test (for example the CodeQL ReDoS finding in the transcript parser) |

### Running inside an authorization boundary

- **Choose a model provider the boundary allows.** `LLM_PROVIDER=bedrock` keeps model traffic inside your AWS account and region under IAM (IRSA in Kubernetes). `LLM_PROVIDER=ollama` or `openai` with `OPENAI_BASE_URL` pointing at a self-hosted, OpenAI-compatible server keeps it on your own hardware. `LLM_PROVIDER=offline` makes no model calls at all, which is useful for testing in isolated networks. Do not send controlled or sensitive transcripts to a provider your boundary has not approved.
- **Air-gapped operation.** Set `SEARCH_PROVIDER=none` (or `mock` for testing) so verification uses only the local, dated knowledge base. The knowledge base is a JSON file you control.
- **Keep transcript text out of telemetry.** Leave `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` unset. Export OTLP only to a Collector inside the boundary, and keep the Collector's `attributes/drop-content` processor in every pipeline as a second guard.
- **Restrict egress by destination.** The chart's NetworkPolicy allows DNS and HTTPS. Pin the allowed hosts (the model endpoint, and the search endpoint if enabled) with an egress gateway or an FQDN-aware CNI policy.
- **Put the API behind your identity layer.** The HTTP API has no login of its own. Expose it only through an ingress that enforces your SSO or mTLS, or keep it cluster-internal and use the queue worker.
- **Use your hardened base image and validated cryptography.** The published image is built on `python:3.12-slim`, which is not a FIPS 140-validated module. Where FIPS-validated cryptography or an approved hardened base is required, rebuild from the `Dockerfile` with your organization's base image; the build has no other base-image dependency.
- **Deploy by digest and verify first.** Pin `image.digest` in the environment values (the GitOps flow does this) and check the provenance attestation before promoting.
- **Keep the audit trail.** Every run writes `run_manifest.json` with SHA-256 hashes of its inputs and outputs, `logs/trace.jsonl` and an OpenTelemetry trace id; `podcast-agent audit <run>` re-checks a run from its files. Retain run folders per your records schedule; they support audit and accountability controls (NIST SP 800-53 AU family).
- **Keep a human in the loop.** Verdicts and show notes are drafts for a reviewer. The agent never publishes.

## Known vulnerabilities

None at this time. Fixed vulnerabilities are listed under **Security** in [CHANGELOG.md](CHANGELOG.md).

## Verifying a release

Images are published to `ghcr.io/organized-ai/podcast-agent` with build provenance:

```bash
gh attestation verify oci://ghcr.io/organized-ai/podcast-agent:v0.2.0 --repo Organized-AI/podcast-agent
docker buildx imagetools inspect ghcr.io/organized-ai/podcast-agent:v0.2.0 --format '{{ json .SBOM }}'   # the SBOM
```

Each GitHub Release also carries the image's signed provenance as assets, so it can be checked without GitHub's attestations API, for example from inside an isolated network:

- `podcast-agent-<tag>.sigstore.json` is the Sigstore bundle.
- `podcast-agent-<tag>.intoto.jsonl` is the signed in-toto SLSA statement.

```bash
gh release download v0.2.0 --repo Organized-AI/podcast-agent --pattern 'podcast-agent-v0.2.0.*'
gh attestation verify oci://ghcr.io/organized-ai/podcast-agent:v0.2.0 \
  --bundle podcast-agent-v0.2.0.sigstore.json --repo Organized-AI/podcast-agent
```
