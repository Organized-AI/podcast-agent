<!-- Generated from website/walkthrough.yaml by tools/guide/embed_walkthrough.py: edit the YAML, then run `make walkthrough`. -->

# From a take-home to an agent you can operate

*Jordan Hill · Organized AI · October 2026*

This is the Seekr forward deployed engineer take-home: what the brief asked for, what I built, how I built it, and what I learned along the way.

Each section ends with the lesson I took from it and why it matters. Every number links to evidence in this guide: a captured run, a CI job or a pull request.

Read it in the guide with every link live ([Walkthrough › Write-up](https://podcast-agent-guide.jordan-691.workers.dev/#writeup)), or present it stop by stop ([Walkthrough › Present](https://podcast-agent-guide.jordan-691.workers.dev/#walk-1)).

1. [The brief, and what I delivered](#1-the-brief-and-what-i-delivered)
2. [Run it: transcripts in, report out](#2-run-it-transcripts-in-report-out)
3. [Fact-checks with evidence](#3-fact-checks-with-evidence)
4. [How I know it's right: evals and a judge](#4-how-i-know-its-right-evals-and-a-judge)
5. [How it was built: issues, pull requests, gates](#5-how-it-was-built-issues-pull-requests-gates)
6. [Ship once: one image, one digest](#6-ship-once-one-image-one-digest)
7. [Kubernetes: dev syncs itself on merge](#7-kubernetes-dev-syncs-itself-on-merge)
8. [Operate: one trace, two backends](#8-operate-one-trace-two-backends)
9. [Security for regulated customers](#9-security-for-regulated-customers)
10. [What isn't done, honestly](#10-what-isnt-done-honestly)
11. [What I'd build next](#11-what-id-build-next)

## 1. The brief, and what I delivered

Seekr asked for an agent that turns raw podcast transcripts into fact-checked show notes, plus a deployment strategy. Every requirement maps to code, and everything in this walkthrough links to its evidence.

- **10** requirements, each mapped to the file that meets it
- **5** agent steps the model can plan
- **4** model providers (Anthropic, Bedrock, OpenAI, Ollama), plus an offline mode
- **4** sample transcripts, JSON and timestamped text

**What I did**

- It's for producers who publish show notes and need every factual claim checked: a 200–300 word summary, five takeaways, quotes checked word for word, a fact-check table marked ✅ ⚠️ ❓, and a readable reasoning log.
- I built it to the spec first, as v0.1.0. Then I made it operable, as v0.2.0.
- This guide is the evidence: the Audit trail is a real Claude run, and every card links to a CI job, a pull request or a captured run.

**What I learned: Build to the spec first, then make it operable.** v0.1.0 met the brief: summary, key notes, a fact-check table and a reasoning log, in Docker. A mentor's review showed that a customer needs more than a working agent. They need to run it, trust it and change it safely. So most of v0.2.0 is operations, not features, and that is the part of the job a forward deployed engineer owns.

Evidence: [Learning path › The brief](https://podcast-agent-guide.jordan-691.workers.dev/#m1) · [FIG. 1 · System map](https://podcast-agent-guide.jordan-691.workers.dev/#map) · [Where each requirement lives](https://github.com/Organized-AI/podcast-agent#where-each-requirement-lives)

## 2. Run it: transcripts in, report out

The model writes the plan, code validates it, and the steps run with every decision logged. A run can be re-checked later from its own files.

- **4** episodes in the committed Claude run (Sonnet 5.5 + Haiku 5.5)
- **19** claims fact-checked
- **36 / 36** audit checks pass on that run
- **SHA-256** of every input, report and the structured trace, in the run manifest

**What I did**

- Haiku writes the plan. In the Claude run it skipped normalization on the three clean JSON transcripts, and set a budget of 2–6 tool calls per claim. Sonnet writes the summary and notes and verifies the claims; Haiku extracts them.
- The summary writes the transcript to Sonnet's prompt cache, and the notes read it back at 5% of the input price.
- `podcast-agent audit` re-checks a finished run from its files: hashes, quotes verbatim at their timestamp, every claim grounded in its transcript line, confidence within the evidence cap.

**What I learned: Let the model plan, but never let the run depend on it.** The planner chooses which steps to run, the summary strategy, how many claims to extract and the fact-checker's tool budget. It can also return a bad plan, so a deterministic validator repairs it and a fallback plan exists. Every other output is checked against a schema and business rules, with the error fed back for up to three attempts. A step that still fails is isolated: what depends on it is skipped, and the report comes out with warnings. The model makes the decisions; code enforces the contract.

Evidence: [Audit trail › Episodes](https://podcast-agent-guide.jordan-691.workers.dev/#au-eps) · [Learning path › The plan](https://podcast-agent-guide.jordan-691.workers.dev/#m3) · [Audit trail › Trace](https://podcast-agent-guide.jordan-691.workers.dev/#au-trace)

## 3. Fact-checks with evidence

Each claim gets a verdict, the confidence its evidence allows, and the sources behind it. When the evidence isn't there, it is ❓ and a person decides.

- **6 · 6 · 7** ✅ verified · ⚠️ outdated or inaccurate · ❓ unverifiable, in the committed Claude run
- **18** dated facts in the local knowledge base
- **2–6** tool calls a claim may use, budgeted by the plan
- **in code** where confidence is capped by the evidence

**What I did**

- The verifier is a tool-using loop: the knowledge base first, then web search when the evidence is thin or time-sensitive. The tools are read-only and allow-listed.
- Verdicts cite evidence ids, and the audit checks each confidence against its evidence cap. A forward-looking prediction is ❓ until its deadline passes.
- Search here is a mock index, so runs are reproducible; production points it at a real search API.

**What I learned: Confidence has to come from evidence, not from the model.** A model will state 90% about anything. Capping confidence in code by what the evidence supports, and defaulting to ❓ for a person, is what makes a verdict something an editor can act on. The first real bug was this kind: a prediction whose deadline hadn't passed was marked ⚠️ instead of ❓ (#13). The committed Claude run predates the fix, so its "humans on Mars before 2040" is still ⚠️ there; current code marks it ❓.

Evidence: [Audit trail › Fact-checks](https://podcast-agent-guide.jordan-691.workers.dev/#au-ledger) · [Learning path › One claim, step by step](https://podcast-agent-guide.jordan-691.workers.dev/#m8) · [Learning path › Verdicts and confidence](https://podcast-agent-guide.jordan-691.workers.dev/#m9)

## 4. How I know it's right: evals and a judge

Every pull request is scored against labeled claims. A live suite with Claude is scheduled nightly, and a rubric judge grades the summaries.

- **19 / 19** offline gates, on every pull request
- **14 / 14** live gates with Claude
- **25 / 25** labeled verdicts right with Claude (24 / 25 offline)
- **1** bug found in the judge itself, by chasing its own flag (#51)

**What I did**

- 25 labeled claims cover every verdict and the edge cases. The offline suite gates every pull request and catches code regressions: validation, the verdict rules, the #13 policy.
- The live suite runs Sonnet 5.5 and Haiku 5.5 against a baseline; a drop of more than 0.05 fails it. Confidence is deliberately conservative: Brier 0.033, with the evidence caps keeping it below the hit rate.
- The judge averaged 0.83. Its "invented speaker roles" turned out to be its own input: it saw names without roles. Fixed with a test in PR #62; the next live run confirms it.

**What I learned: Evaluate the evaluator.** All 19 offline gates and all 14 live gates passed, and the rubric judge still flagged a summary for inventing speaker roles (#51). Tracing it back, the roles were in the transcript: the judge had been given a copy with the names but not the roles. The agent was right and the judge was wrong. A judge is code too. It needs the same inputs as the thing it grades, and tests of its own.

Evidence: [Audit trail › Evals](https://podcast-agent-guide.jordan-691.workers.dev/#au-evals) · [Proof: the live run with Claude](https://podcast-agent-guide.jordan-691.workers.dev/#pf-evals-live) · [Issue #51](https://github.com/Organized-AI/podcast-agent/issues/51)

## 5. How it was built: issues, pull requests, gates

A mentor reviewed v0.1.0 and found six gaps. Each one is closed in the repository, not in a plan.

- **37** pull requests merged, this walkthrough's included
- **29** issues tracked; all closed but #51, which waits on the next live run
- **7** required checks on main
- **145** tests, 84% coverage, 83% floor

**What I did**

- v0.1.0 was 14 commits pushed straight to main. Since #22 everything goes through a pull request, and rulesets make that the only way in: squash-only, seven required checks, no new high-severity CodeQL alert.
- The PR Gate covers lint, types, the tests, the offline evals, the Docker run, Helm for every environment and the observability stack end to end.
- Security scanning runs on every pull request: CodeQL, gitleaks, zizmor, Trivy and dependency review. With fuzzing, signed releases and the Best Practices badge in place, the OpenSSF Scorecard went from 6.8 to 7.7.

**What I learned: A check nobody is required to pass isn't a gate.** v0.1.0 ran tests in CI, but nothing required them to pass, and the Helm chart was rendered but never installed. Making the PR Gate required, and installing the chart for real with Argo CD on a kind cluster, turned claims into evidence. The kind test caught a real deploy bug (PR #36), and the fuzzer's first input crashed the JSON parser (#49). Neither would have shown up in a demo.

Evidence: [v0.2.0 › Review of v0.1.0](https://podcast-agent-guide.jordan-691.workers.dev/#rl-review) · [Audit trail › Build and test](https://podcast-agent-guide.jordan-691.workers.dev/#au-proof) · [Merged pull requests](https://github.com/Organized-AI/podcast-agent/pulls?q=is%3Apr+is%3Amerged)

## 6. Ship once: one image, one digest

Each code merge builds one multi-arch image with an SBOM and signed provenance. A release re-tags that digest, so prod runs the bytes dev tested, and anyone can check.

- **1** digest in dev and prod at the v0.2.0 release (sha256:67a18fc8…)
- **SLSA** provenance signed by publish.yml on main
- **7.7 / 10** OpenSSF Scorecard (6.8 at release)
- **passing** OpenSSF Best Practices badge: all 67 criteria met or not applicable

**What I did**

- Nothing is rebuilt for prod. The release workflow re-tags the digest main already built and tested; promotion is a small pull request that changes the tag and the digest.
- Anyone can verify v0.2.0 without a registry login: online with `gh attestation verify`, or offline from the Sigstore bundle attached to the release.
- Every image has an SBOM and runs on amd64 and arm64.

**What I learned: The thing you tested should be the thing you ship.** Rebuilding for prod means prod runs bytes nobody tested. Building once and promoting by digest, with signed provenance, means anyone can confirm that prod runs exactly what passed in dev. For a regulated customer that is the first question, and it maps directly to NIST SSDF PS.2 and PS.3.

Evidence: [v0.2.0 › How it shipped](https://podcast-agent-guide.jordan-691.workers.dev/#rl-ship) · [Proof: anyone can verify v0.2.0](https://podcast-agent-guide.jordan-691.workers.dev/#pf-verify-v0.2.0) · [Release v0.2.0](https://github.com/Organized-AI/podcast-agent/releases/tag/v0.2.0)

## 7. Kubernetes: dev syncs itself on merge

Argo CD renders the Helm chart for each environment. Dev follows main by itself; prod waits for a person. Merging the deploy pull request is the deploy.

- **43 s** from merging #55 to Argo CD's automated sync
- **0** kubectl commands
- **29** rendered resources pass kubeconform (default, dev, prod and Argo CD)
- **dev · prod** one chart, two environments

**What I did**

- The deploy pull request is opened by a GitHub App, so its checks run like anyone's. Merging it is the deploy.
- Earlier: #55 merged at 12:46:35Z, Argo CD started an automated sync at 12:47:18Z, and by 12:47:48Z `/healthz` reported the new commit. No one ran kubectl.
- The lab is a kind cluster on a Mac mini, reached over Tailscale with its own kubeconfig. CI installs the same Argo CD setup on a throwaway kind cluster for every deployment change.

**What I learned: Measure readiness before you measure change.** My first lab watcher took its "before" snapshot while the pod was still pulling its image, and reported a sync that hadn't happened. The fix was to wait for a ready pod and a Synced, Healthy app first, then watch for the image to change. Proving automation takes the same care as building it. The same goes for bot pull requests: one opened with the workflow's own token runs no checks, so deploy PRs now come from a GitHub App.

Evidence: [FIG. 6 · Merge to cluster](https://podcast-agent-guide.jordan-691.workers.dev/#gitops) · [Proof: dev synced on merge](https://podcast-agent-guide.jordan-691.workers.dev/#pf-lab-sync) · [Deploy PR #56](https://github.com/Organized-AI/podcast-agent/pull/56)

## 8. Operate: one trace, two backends

Every run is one OpenTelemetry trace. The Collector sends it to Jaeger and MLflow, and CI proves that an MLflow outage doesn't cost Jaeger a span.

- **128 / 128** spans in both Jaeger and MLflow for a full (offline) run
- **37** queued spans delivered after an MLflow outage
- **6** alert rules, unit-tested
- **0** transcript text in spans, by default

**What I did**

- Span names follow the GenAI semantic conventions, so a backend understands model calls, tools and tokens without custom parsing. MLflow shows the run as an agent: AGENT, CHAT_MODEL and TOOL spans, with token totals.
- I added MLflow after Joseph Fluckiger's AIMUG talk, where he showed it tracing LangGraph agents.
- On every pull request CI stops MLflow, runs an episode and checks that Jaeger got all of it, then restarts MLflow and checks that the queued spans arrive.
- Transcript text stays out of spans unless content capture is turned on, which matters when transcripts are confidential.

**What I learned: If the agent only speaks OTLP, a new backend is a config change.** Adding MLflow took no agent code, just one more exporter in the Collector. The surprises were all at the edges: MLflow needs a SQL store, it rejects unknown Host headers to stop DNS rebinding, and macOS holds port 5000. The outage test proves the property that matters: with MLflow down, Jaeger still gets every span, and MLflow gets its copy when it is back (the Collector retries for five minutes).

Evidence: [FIG. 5 · One trace](https://podcast-agent-guide.jordan-691.workers.dev/#otel) · [Proof: Jaeger, MLflow and the outage](https://podcast-agent-guide.jordan-691.workers.dev/#pf-otel-e2e-mac) · [Joseph Fluckiger's AIMUG talk](https://www.youtube.com/watch?v=YPJd-RXsWI0)

## 9. Security for regulated customers

It's open-source software, not a service. It runs inside the customer's authorization boundary, and SECURITY.md is the evidence their assessor needs.

- **14** NIST SSDF 1.1 practices, each mapped to evidence
- **air-gapped** a local model and the local knowledge base only
- **Bedrock** model traffic stays in AWS, in the customer's region, under their IAM
- **by digest** deploys pin a digest; its provenance can be verified before promotion

**What I did**

- Model traffic stays where the boundary allows: Bedrock in the customer's region under their IAM, or a self-hosted, OpenAI-compatible server or Ollama.
- Air-gapped: a local model, `SEARCH_PROVIDER=none`, and verification from the dated knowledge base the customer controls.
- Deploy by digest and verify provenance before promoting; enforcing that at admission is the next step. Every run keeps hashes of its inputs and outputs, and the agent never publishes: a person reviews.
- What it doesn't have: no FedRAMP authorization or ATO, no login on the API (put it behind SSO or mTLS), and a base image that isn't FIPS-validated (rebuild on theirs).

**What I learned: Security for a regulated customer is evidence, not adjectives.** An assessor asks what was done, not what was intended. Mapping each NIST SSDF practice to a file, a workflow or a release asset, and saying plainly what isn't there (no ATO, no FIPS-validated base image, no built-in login), is more useful to them than a list of best practices.

Evidence: [SECURITY.md › Regulated deployments](https://github.com/Organized-AI/podcast-agent/blob/main/SECURITY.md#government-and-regulated-deployments) · [Learning path › Security and quality](https://podcast-agent-guide.jordan-691.workers.dev/#m15) · [OpenSSF Best Practices](https://www.bestpractices.dev/projects/15516)

## 10. What isn't done, honestly

The gaps that remain need people, time, or a customer's environment.

- **3** bugs found by fact-checking this walkthrough, fixed the same day
- **4** Scorecard checks only people or time can fix
- **designed** provider failover, queue redrive, multi-region
- **kind** the lab cluster; AWS is designed, not yet run

**What I did**

- #51: the judge said a summary invented speaker roles. The cause was the judge's own input; the fix is merged, and the next live run confirms it.
- Scorecard: no second reviewer, branch protection can't require one, the repository is under 90 days old, and every contributor is from one organization.
- Designed but not built: provider failover through a model gateway (Bedrock, then the Anthropic API, then a self-hosted model), SQS redrive, and multi-region recovery.
- The chart's AWS pieces (KEDA on SQS, ExternalSecrets, a service account ready for IRSA) are rendered and schema-checked on every change, but they haven't run on AWS.

**What I learned: Fact-check the story, too.** Checking every number in this walkthrough against the repository found three real bugs: the judge's input (#51), prod's NetworkPolicy blocking telemetry (#61), and a Docker quickstart that failed with an API key (#64). Each got an issue, a fix and a test. Some gaps need people rather than tools: a second reviewer, branch protection that requires one, and time.

Evidence: [v0.2.0 › What isn't done](https://podcast-agent-guide.jordan-691.workers.dev/#rl-skip) · [OpenSSF Scorecard](https://scorecard.dev/viewer/?uri=github.com/Organized-AI/podcast-agent)

## 11. What I'd build next

The next steps come from the cost model and the evals: cost first, then quality, then reach.

- **≈ $0.17** modeled per hour-long episode with 10 claims (the samples measured $0.05–$0.10)
- **≈ $0.09** with tiered verification
- **< $100** a month of model spend at 500 episodes
- **1** new step for audio and video in

**What I did**

- Tiered verification: run the claim loop on Haiku and escalate to Sonnet only for ⚠️ or low confidence.
- Observability inside the cluster: the Collector, Jaeger and MLflow as an Argo CD application, so cluster runs land in MLflow too, and the nightly judge's scores attached to each run's trace.
- A model gateway: per-tenant budgets, rate limits and provider failover.
- Audio and video in: speech-to-text with speaker labels in front of the pipeline, which doesn't otherwise change.

**What I learned: Let the customer's numbers pick the next step.** Verification is most of the token bill, so tiered verification is the first cost lever. At agency scale fixed infrastructure costs more than tokens, so the footprint should follow the client. The roadmap comes from the cost model and the evals, not from a feature list.

Evidence: [Learning path › Cost, scale, failure](https://podcast-agent-guide.jordan-691.workers.dev/#m14) · [DEPLOYMENT.md › Cost](https://github.com/Organized-AI/podcast-agent/blob/main/docs/DEPLOYMENT.md#4-cost)
