#!/usr/bin/env bash
# Is this machine ready to change podcast-agent and run it end to end? Checks the tools and the key, then proves it:
# make check, an offline run with its audit, and the Docker image built and run.
#
#   make doctor            # no model calls, no spend
#   make doctor LIVE=1     # also one short episode with Claude (about $0.09 at list prices) to prove the key works
#
# The key is never printed: the script only checks that ANTHROPIC_API_KEY is set in .env.
set -u
cd "$(dirname "$0")/../.."

PASS=0 FAIL=0 WARN=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS + 1)); }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
note() { printf '  \033[33m!\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

step "Tools"
for tool in git uv make python3; do
  if command -v "$tool" >/dev/null; then ok "$tool: $("$tool" --version 2>&1 | head -1)"; else bad "$tool is not installed"; fi
done
if command -v claude >/dev/null; then
  ok "Claude Code $(claude --version 2>/dev/null | awk '{print $1}') (reads AGENTS.md on its own from 2.1.277)"
else
  note "Claude Code is not on PATH"
fi
if [ -d /Applications/Cursor.app ] || command -v cursor >/dev/null; then ok "Cursor installed"; else note "Cursor not found"; fi
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then ok "gh signed in"; else note "gh not signed in (only needed for PRs)"; fi

DOCKER_UP=""
if command -v docker >/dev/null; then
  host=$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)
  sock=${host#unix://}
  if [ -S "$sock" ] && curl -s --max-time 3 --unix-socket "$sock" http://localhost/_ping | grep -q OK; then
    DOCKER_UP=1
    ok "Docker engine answering ($(docker version --format '{{.Server.Version}}' 2>/dev/null))"
  else
    bad "Docker is installed but its engine doesn't answer: start or restart Docker Desktop"
  fi
else
  bad "docker is not installed"
fi

step "Repository"
git fetch -q origin 2>/dev/null || note "couldn't reach origin to compare"
branch=$(git rev-parse --abbrev-ref HEAD)
behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
ok "on $branch at $(git rev-parse --short HEAD)"
if [ "$behind" = "0" ]; then ok "up to date with origin/main"; else note "$behind commit(s) behind origin/main: git pull"; fi
if [ -z "$(git status --porcelain --untracked-files=no)" ]; then ok "no uncommitted changes"; else note "uncommitted changes in tracked files"; fi

step "Model key"
if [ -f .env ]; then
  if grep -Eq '^ANTHROPIC_API_KEY=.+' .env; then ok ".env has ANTHROPIC_API_KEY (not shown)"; else bad ".env has no ANTHROPIC_API_KEY: runs fall back to offline mode"; fi
else
  bad "no .env: cp .env.example .env and add ANTHROPIC_API_KEY"
fi

step "Python environment and checks"
if uv sync --locked --extra aws -q >"$TMP/sync.log" 2>&1; then ok "uv sync --locked"; else bad "uv sync failed (see below)"; tail -5 "$TMP/sync.log"; fi
start=$(date +%s)
if make check >"$TMP/check.log" 2>&1; then
  ok "make check: $(grep -Eo '[0-9]+ passed' "$TMP/check.log" | tail -1) in $(($(date +%s) - start))s"
else
  bad "make check failed (last lines below)"; tail -15 "$TMP/check.log"
fi

step "End to end, offline (no key, no spend)"
if uv run --locked podcast-agent run data/transcripts --provider offline --quiet --out "$TMP/offline" >"$TMP/run.log" 2>&1 \
   && uv run --locked podcast-agent audit "$TMP/offline" >"$TMP/audit.log" 2>&1; then
  ok "offline run of every transcript; audit: $(grep -Eo '[0-9]+ passed · [0-9]+ warnings · [0-9]+ failed' "$TMP/audit.log" | tail -1)"
else
  bad "offline run or audit failed"; tail -10 "$TMP/run.log" "$TMP/audit.log" 2>/dev/null
fi

if [ -n "$DOCKER_UP" ]; then
  step "End to end in Docker"
  if docker build -q --build-arg GIT_COMMIT="$(git rev-parse --short HEAD)" -t podcast-agent . >"$TMP/build.log" 2>&1; then
    ok "docker build"
    mkdir -p "$TMP/docker" && chmod 777 "$TMP/docker"
    if docker run --rm -e LLM_PROVIDER=offline -v "$TMP/docker:/app/output" podcast-agent >"$TMP/drun.log" 2>&1 \
       && docker run --rm -v "$TMP/docker:/app/output" podcast-agent audit output >"$TMP/daudit.log" 2>&1; then
      ok "docker run (offline) and audit inside the container"
    else
      bad "docker run failed"; tail -10 "$TMP/drun.log" "$TMP/daudit.log" 2>/dev/null
    fi
  else
    bad "docker build failed"; tail -15 "$TMP/build.log"
  fi
fi

if [ -n "${LIVE:-}" ]; then
  step "End to end with Claude (one short episode)"
  if (set -a; . ./.env; set +a; uv run --locked podcast-agent run data/transcripts/ep004_pdf_example.txt --quiet \
        --out "$TMP/claude" >"$TMP/claude.log" 2>&1) && grep -q '"provider": "anthropic"' "$TMP/claude/run_manifest.json" 2>/dev/null; then
    ok "Claude run finished: $(grep -Eo '\$[0-9.]+' "$TMP/claude/logs/run.log" | tail -1) for ep004"
  else
    bad "Claude run failed (key, network or workspace): last lines below"; tail -8 "$TMP/claude.log"
  fi
fi

printf '\n%d ok · %d to look at · %d failed\n' "$PASS" "$WARN" "$FAIL"
[ "$FAIL" -eq 0 ]
