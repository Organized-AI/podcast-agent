# Podcast Agent — show notes and fact-checking for podcast transcripts

**Interactive guide:** [podcast-agent-guide.jordan-691.workers.dev](https://podcast-agent-guide.jordan-691.workers.dev) · **Proof it runs:** [Audit trail tab](https://podcast-agent-guide.jordan-691.workers.dev/#audit) (a real Claude run, re-checkable in the browser) · **Source:** [github.com/organized-ai/podcast-agent](https://github.com/organized-ai/podcast-agent)

An agentic AI system that takes raw podcast transcripts (`.json` or timestamped `.txt`) and produces publish-ready show notes for an ad agency:

1. **Summary.** 200–300 words covering core themes, key discussions and the opinions shared.
2. **Key notes.** 🔹 top 5 takeaways, 💬 notable quotes with timestamps (each verified word-for-word against the transcript), 🧭 tag-style topics.
3. **Fact-check.** Check-worthy claims are extracted and verified by a tool-using agent against a local knowledge base and a (mock or real) web search. Each claim is marked ✅ verified, ⚠️ possibly outdated/inaccurate or ❓ unverifiable, with a calibrated confidence score and cited evidence.
4. **Reasoning log.** A readable trace of every plan, tool call (with its stated purpose), observation, self-correction and verdict, plus Claude's own thinking summaries, plus a structured JSONL version.

The deployment strategy (diagram + narrative) is in **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**. An interactive architecture guide you can click through is live at **[podcast-agent-guide.jordan-691.workers.dev](https://podcast-agent-guide.jordan-691.workers.dev)** (source: [docs/guide/public/index.html](docs/guide/public/index.html); deploy your own copy as a Cloudflare Worker with `cd docs/guide && npx wrangler deploy`). Its **Audit trail** tab is filled from a real run by `make guide` (`scripts/embed_audit.py`).

## Where each requirement lives

| The assignment asks for | Where it is |
|---|---|
| A summary of each episode, about 200–300 words | `src/podcast_agent/agent/steps.py` → `summarize`; written to `output/report.md` |
| Top 5 takeaways, quotes with timestamps, topic tags | `agent/steps.py` → `extract_notes` (quotes are checked word-for-word against the transcript) |
| Fact-checking via search, a local knowledge base or a reasoning step, marked ✅ / ⚠️ / ❓ | `agent/verifier.py` (tool loop), `data/knowledge_base.json`, `data/mock_web_index.json`, `tools/` |
| A multi-step agent that plans and executes on its own | `agent/planner.py` (model-written plan, validated and repaired), `agent/pipeline.py` (executor) |
| Containerized, runs locally via Docker | `Dockerfile`, `docker-compose.yml`; see [Quickstart](#quickstart-docker) |
| A model running locally or on a cloud provider (Bedrock, OpenAI, Anthropic) | `src/podcast_agent/llm/`: `LLM_PROVIDER=anthropic`, `bedrock`, `openai`, `ollama` or `offline` |
| Readable logs of the agent's reasoning steps and plans | `output/logs/run.log` (readable) and `output/logs/trace.jsonl` (structured); browse a full run in the guide's **Audit trail** tab |
| An output JSON or Markdown file with summary, notes and fact-check table | `output/report.md` and `output/report.json` (plus one pair per episode) |
| A deployment strategy with diagram and narrative | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/architecture.svg](docs/architecture.svg) |
| A detailed README | This file, plus the [interactive guide](https://podcast-agent-guide.jordan-691.workers.dev) (`docs/guide/public/index.html`) |

Sample outputs are committed for two complete runs, each with its audit (`audit.md`): [`samples/claude-run/`](samples/claude-run) (Claude Sonnet 5.5 and Haiku 5.5, run in Docker on an M1 MacBook: 36 of 36 audit checks pass) and [`samples/offline-mode/`](samples/offline-mode) (no API key, deterministic heuristics). Captured test, lint, Helm and Docker output from the same commit, including a tamper test the audit catches, is in [`samples/proof/`](samples/proof). The guide's **Audit trail** tab renders all of it and re-hashes the run's files in the browser.

---

## Quickstart (Docker)

```bash
git clone <this repo> && cd podcast-agent
cp .env.example .env            # add ANTHROPIC_API_KEY=...
docker build --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) -t podcast-agent .
docker run --rm -t --env-file .env -v "$PWD/output:/app/output" podcast-agent
docker run --rm -t -v "$PWD/output:/app/output" podcast-agent audit output   # re-verify the run
```

That processes every transcript in `data/transcripts/` and writes to `./output/`:

| File | Contents |
|---|---|
| `output/report.md` | **The deliverable**: every episode's summary, key notes and fact-check table (Claim · Verification · Confidence) in one file |
| `output/report.json` | The same for every episode, machine-readable, including a `fact_check_table` array per episode |
| `output/<episode_id>.md` | One episode's show notes, fact-check table, evidence for each claim and agent run details |
| `output/<episode_id>.json` | The same report, machine-readable (schema: `src/podcast_agent/models.py → EpisodeReport`) |
| `output/index.md` | One row per episode: counts, verdict mix, LLM calls, tokens |
| `output/logs/run.log` | Human-readable reasoning trace |
| `output/logs/trace.jsonl` | Structured trace (one event per line: `run_id`, `episode_id`, `step`, `kind`, `message`, `data`) |
| `output/run_manifest.json` | Code commit, prompt version, settings, input and output SHA-256 hashes, per-step timings and token usage |
| `output/audit.json` · `audit.md` | Written by `podcast-agent audit`: every guarantee re-checked from the files (quotes verbatim, confidence caps, trace completeness, hashes) |

Process your own files:

```bash
docker run --rm -t --env-file .env -v "$PWD/output:/app/output" -v "$PWD/my_transcripts:/app/in:ro" \
  podcast-agent run /app/in --out output
```

On Linux, add `--user "$(id -u):$(id -g)"` if the output volume isn't writable. `docker compose run --rm agent` does the same as the first `docker run`.

### No API key? Two options

```bash
# 1) Offline mode: deterministic heuristics stand in for the model, so the whole pipeline runs
#    (planning, validation, tool loop, reports, logs). Plumbing check only, not model quality.
docker run --rm -t -e LLM_PROVIDER=offline -v "$PWD/output:/app/output" podcast-agent

# 2) Fully local model via Ollama
docker compose --profile local-llm up -d ollama
docker compose exec ollama ollama pull llama3.1:8b
LLM_PROVIDER=ollama docker compose run --rm agent
```

### Claude on Amazon Bedrock

```bash
# AWS credentials come from the usual chain: env vars, a mounted ~/.aws profile, or an IAM role.
docker run --rm -t -v "$PWD/output:/app/output" -v "$HOME/.aws:/home/agent/.aws:ro" \
  -e LLM_PROVIDER=bedrock -e AWS_PROFILE=default -e AWS_REGION=us-east-1 \
  -e BEDROCK_MODEL=<Claude model or inference-profile ID from the Bedrock console> \
  podcast-agent
```

## Run without Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
export ANTHROPIC_API_KEY=...            # or LLM_PROVIDER=offline
.venv/bin/podcast-agent run data/transcripts --out output
.venv/bin/podcast-agent run data/transcripts/ep002_ai_healthcare.json --today 2026-10-13 --max-claims 5
.venv/bin/podcast-agent audit output             # re-verify a run: hashes, quotes, caps, trace coverage
.venv/bin/podcast-agent kb "FDA autonomous AI"    # query the knowledge base directly
.venv/bin/podcast-agent serve --port 8080         # HTTP API: POST /v1/process, GET /healthz
.venv/bin/pytest -q                               # 40 tests, no network or API key needed
```

---

## How the agent works

```
 transcript (.json / .txt)
        │  parse · resolve speakers ("Dr. Priya" → Dr. Priya Patel, guest) · keep raw text
        ▼
 ┌─ PLAN ──────────────────────────────────────────────────────────────────────────┐
 │ profile the episode (length, fillers, entities, years, prediction cues) → model  │
 │ writes a plan from a capability catalog: which steps, order, strategy, claim     │
 │ budget, tools, tool budget → validator enforces required deliverables and        │
 │ dependency order, repairs the plan (or falls back to a deterministic plan)       │
 └─────────────────────────────────────────────────────────────────────────────────┘
        ▼
 normalize_transcript ─► summarize ─► extract_notes ─► extract_claims ─► verify_claims
   (fillers, stutters)    (single-pass   (takeaways,      (standalone,      (tool loop per
                           or map-reduce) quotes, topics)  typed claims)     claim, below)

 every model step:  prompt ─► JSON ─► schema check ─► business-rule check ─┬─► accept
                                  ▲                                         │
                                  └──── specific problems fed back ◄────────┘  (≤ 3 attempts)

 verify_claims, per claim (ReAct):
   purpose ─► action: search_knowledge_base | web_search ─► observation (dated evidence E1..En)
      ▲                                                            │
      └────────────────────── repeat (≤ tool budget) ◄─────────────┘
   finish(verdict, confidence, rationale, evidence_ids) ─► validate ids ─► calibrate confidence
```

### Design choices

| Choice | Why |
|---|---|
| **A model plans; a validator guards the plan** | The model picks steps, summarization strategy, claim budget and tools from the episode's profile. A deterministic validator guarantees no deliverable is ever skipped and repairs bad plans. |
| **Structured outputs + self-correction, not vendor tool-calling** | Every step returns JSON validated by Pydantic plus step rules: word count, exactly 5 takeaways, quotes verbatim, timestamps that exist. Failures go back to the model as specific feedback. The same loop works for Claude, OpenAI-compatible servers, Ollama and the offline provider. |
| **Quotes are grounded, not trusted** | Each quote is fuzzy-matched (≥ 0.90) to a single transcript line. A paraphrase is rejected and retried. A wrong timestamp or speaker is corrected from the matched line and logged. |
| **Claims are decontextualized and typed** | "They've written handbooks" becomes "GitLab, Automattic and Doist have written handbooks…". Each claim is typed (factual, statistical, historical, prediction, anecdotal) and flagged if time-sensitive. |
| **Knowledge base first, web second, dates everywhere** | KB entries carry `as_of` dates and web results carry publish dates. The agent is told today's date, which is what separates ✅ from ⚠️ outdated. |
| **Confidence is calibrated against evidence** | Confidence means how strongly the evidence supports the verdict. It is capped by what was actually gathered: ❓ ≤ 0.50, no tool evidence ≤ 0.60, one source type ≤ 0.90, knowledge base + web agreeing ≤ 0.95. Both raw and calibrated values are reported. |
| **Bounded autonomy** | Tool budget per claim, turn cap, at most 2 corrections; a loop that never finishes defaults to ❓ 0.20 for human review. |
| **Failures are isolated** | A failed step is recorded and its dependents skipped, but independent steps still run. A search outage is retried, then reported to the agent as an observation, and it continues with the knowledge base. |
| **Cost-aware model use** | Sonnet 5.5 for summary, notes and verification; Haiku 5.5 for planning and claim extraction. The transcript is sent as a prompt-cache prefix, and a content-addressed response cache makes reruns free and reproducible. |

### Fact-checking the PDF's own example

`data/transcripts/ep004_pdf_example.txt` is a synthetic transcript built from the assignment's example lines, with fillers, stutters and a `[hh:mm:ss]` format. The assignment's table lists the NASA claim as verified and the Bitcoin claim as unverifiable. Checked against current evidence, the agent reaches different verdicts:

| Claim | Agent verdict | Why |
|---|---|---|
| NASA's Mars sample-return mission launches in early 2026 | ⚠️ Possibly outdated/inaccurate | Mars Sample Return was restructured in 2025 (Earth return 2035–2039, decision deferred to 2026). The "launch in 2026" figure comes from the 2020 plan. |
| Bitcoin will reach $200K by 2025 | ⚠️ Possibly outdated/inaccurate | A prediction whose deadline has passed is checkable: the 2025 peak was about $126K (Oct 6, 2025). |
| Perseverance landed in Jezero Crater in February 2021 | ✅ Verified true | KB + web agree (Feb 18, 2021). |
| Humans will walk on Mars before 2040 | ❓ Unverifiable | A future prediction. |

### A reasoning-log excerpt (`output/logs/run.log`)

```
15:27:10 plan              ◆ 1. normalize_transcript — 11 filler/repeated tokens detected; clean before quoting.
15:27:10 plan              ◆ 2. summarize {'strategy': 'single_pass'} — 122 words fits within the single-pass budget.
15:27:10 plan              ◆ 4. extract_claims {'max_claims': 8} — Claim signals (entities=8, years=4, predictions=3) …
15:27:10 summarize      ↻ attempt 1/3 rejected — the summary is 136 words; it must be 200–300 words — expand it …
15:27:10 verify C1      ▶ “NASA recently announced something about a new Mars mission: It’s launching in early 2026 — …”
15:27:10 verify C1         💭 Start with the curated knowledge base.
15:27:10 verify C1         🔧 search_knowledge_base("NASA announced new Mars mission launching early 2026 bring back samples")
15:27:10 verify C1         👁  3 result(s); top [E1] KB-030: NASA's Mars Sample Return has been delayed and restructured … (2025-07)
15:27:10 verify C1         💭 Knowledge-base evidence is possibly stale for a time-sensitive claim; check the web for current information.
15:27:10 verify C1         🔧 web_search("NASA announced new Mars mission launching early 2026 bring back samples")
15:27:10 verify C1         👁  3 result(s); top [E4] NASA and ESA outline Mars Sample Return campaign with launches in 2026 (2020-11)
15:27:10 verify C1         ⚖️  ⚠️ Possibly outdated/inaccurate · confidence 0.70
```

The full logs from a complete run are in [`samples/offline-mode/logs/`](samples/offline-mode/logs). With a real model, the 💭 lines carry the model's own reasoning and the `⇄` lines show model, tokens, latency and prompt-cache reads.

---

## Sample outputs

- [`samples/offline-mode/`](samples/offline-mode): a complete run in **offline mode** (no API key), committed so the output format, logs and fact-check flow can be inspected without running anything. These summaries are extractive and the takeaways are lexical: it's a plumbing demonstration, not model quality.
- `samples/claude/`: produce it with your key: `podcast-agent run data/transcripts --out samples/claude --today 2026-10-13`.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example)); the main ones:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `auto` | `anthropic` · `bedrock` · `openai` (any OpenAI-compatible server) · `ollama` · `offline`. `auto` picks Anthropic if a key is set, else offline; Bedrock is used only when named. |
| `BEDROCK_MODEL` / `BEDROCK_FAST_MODEL` / `AWS_REGION` | none / main model / `us-east-1` | Claude model or inference-profile IDs on Bedrock; credentials from the AWS chain |
| `PROMPT_CACHE` | `true` | Mark the transcript as a cacheable prompt prefix (Anthropic and Bedrock) |
| `ANTHROPIC_MODEL` / `ANTHROPIC_FAST_MODEL` | `claude-sonnet-5-5` / `claude-haiku-5-5` | Main model (summary, notes, verification) / fast model (planning, claim extraction) |
| `SEARCH_PROVIDER` | `mock` | `mock` (fixture in `data/mock_web_index.json`) · `tavily` (needs `TAVILY_API_KEY`) · `none` |
| `KB_PATH` | `data/knowledge_base.json` | Curated facts with `as_of` dates; add entries freely |
| `MAX_CLAIMS` / `MAX_STEPS_PER_CLAIM` | `8` / `4` | Upper bounds; the planner chooses within them |
| `SUMMARY_LENGTH_MODE` | `spec` | `spec` = 200–300 words (±10% accepted for the spec's "~"); `adaptive` lowers the floor for very short transcripts |
| `AGENT_TODAY` | today | Pin the fact-checker's "today" for reproducible verdicts |
| `MOCK_SEARCH_FAILURE_RATE` | `0` | Inject search failures to watch retries and graceful degradation |
| `LLM_CACHE` | `true` | Content-addressed response cache in `.cache/llm` (rerun = free + identical) |

## Project layout

```
src/podcast_agent/
  cli.py · runtime.py · server.py · worker.py   entry points: run · serve · worker (queue consumer)
  config.py · models.py · prompts.py            settings · typed contracts · every prompt (versioned)
  transcript.py · text.py                       JSON/TXT parsing, filler cleanup · BM25, fuzzy span matching
  agent/planner.py                              profile → model plan → validate/repair → fallback
  agent/pipeline.py                             executor: step registry, failure isolation, report assembly
  agent/steps.py                                normalize · summarize (+map-reduce) · notes · claims
  agent/verifier.py                             per-claim ReAct tool loop, evidence validation, calibration
  llm/                                          Anthropic · OpenAI-compatible/Ollama · offline · cache · structured outputs
  tools/                                        knowledge base (BM25) · web search (mock / Tavily) + retries
  heuristics.py                                 offline stand-ins for each model task
  report.py · trace.py                          Markdown/JSON reports · reasoning trace (console + JSONL)
data/transcripts/                               ep001–ep003 (provided) + ep004 (synthetic, from the PDF example)
data/knowledge_base.json · mock_web_index.json  local KB (18 dated facts) · mock search corpus
docs/DEPLOYMENT.md                              deployment strategy: diagram + narrative
deploy/helm/podcast-agent/                      Helm chart: API + KEDA-scaled workers
tests/                                          40 tests, including scripted-model tests of every correction path
```

## Making changes quickly

| To… | Do this |
|---|---|
| Add a fact the checker can use | Append an entry to `data/knowledge_base.json` (`id`, `title`, `content`, `source`, `url`, `as_of`, `tags`) |
| Change a prompt | Edit `prompts.py` and bump `PROMPT_VERSION` (busts the response cache and is recorded in the manifest) |
| Swap models or vendors | `ANTHROPIC_MODEL=…`, or `LLM_PROVIDER=ollama OLLAMA_MODEL=…`, or `--model` |
| Add a new output (e.g. chapters) | Write a function in `agent/steps.py` decorated with `@register("…")`, add it to `capability_catalog()` in `planner.py`, render it in `report.py` |
| Make fact-checking stricter | Change the caps in `verifier.calibrate()`; require 2 source types for ✅; raise `MAX_STEPS_PER_CLAIM` |
| Demo resilience | `MOCK_SEARCH_FAILURE_RATE=0.6 podcast-agent run …` and watch retries and fallback in the log |

## Limitations and next steps

- **Mock search.** The default web search is a fixture, so its verdicts are only as current as that file and the knowledge base. `SEARCH_PROVIDER=tavily` switches to live search. In production, add domain allow-lists and source-quality scoring.
- **Lexical retrieval.** The knowledge base uses BM25. Hybrid BM25 + embeddings (pgvector) would improve recall on paraphrased claims.
- **Sequential execution.** Steps and claims run one after another to keep the log readable. Summary, notes and claims are independent and can run in parallel, and claims can fan out with bounded concurrency.
- **Very short transcripts.** The provided episodes are 190–325 words, so a 200–300-word summary is as long as the source. `SUMMARY_LENGTH_MODE=adaptive` avoids padding.
- **Evaluation.** The next step is a labeled golden set (claims with gold verdicts, reference takeaways) run in CI on every prompt or model change; see docs/DEPLOYMENT.md §7.
