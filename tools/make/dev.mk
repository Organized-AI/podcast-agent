##@ Develop
UV  ?= uv
RUN ?= samples/claude-run
PA  := $(UV) run podcast-agent

install: ## Create .venv from uv.lock (dev group + AWS extra)
	$(UV) sync --locked --extra aws

lock: ## Refresh uv.lock after editing dependencies
	$(UV) lock

run: ## Process data/transcripts with the configured model (reads .env)
	set -a; [ -f .env ] && . ./.env; set +a; $(PA) run data/transcripts --out output

offline: ## Full pipeline on deterministic heuristics, no key needed
	$(PA) run data/transcripts --out output --provider offline

audit: ## Re-verify the last run from its files
	$(PA) audit output

serve: ## HTTP API on :8080
	$(PA) serve --host 127.0.0.1 --port 8080

worker-demo: ## Drop transcripts into a local queue and drain it
	mkdir -p queue/inbox && cp data/transcripts/* queue/inbox/ && $(PA) worker --queue dir://queue --output output/jobs --once

config: ## Regenerate config/settings.yaml from the code and .env.example
	$(UV) run python tools/ci/gen_config.py
.PHONY: install lock run offline audit serve worker-demo config
