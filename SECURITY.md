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

## Verifying a release

Images are published to `ghcr.io/organized-ai/podcast-agent` with build provenance:

```bash
gh attestation verify oci://ghcr.io/organized-ai/podcast-agent:v0.2.0 --repo Organized-AI/podcast-agent
```
