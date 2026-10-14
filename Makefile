.PHONY: install test lint run offline audit docker-build docker-run docker-offline serve worker-demo helm-lint

install:            ## local virtualenv + dev tools
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev,aws]"

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests

run:                ## real model (needs ANTHROPIC_API_KEY or another provider in .env)
	set -a; [ -f .env ] && . ./.env; set +a; .venv/bin/podcast-agent run data/transcripts --out output

offline:            ## no key needed: deterministic heuristics, exercises the full pipeline
	.venv/bin/podcast-agent run data/transcripts --out output --provider offline

audit:              ## re-verify the last run from its files
	.venv/bin/podcast-agent audit output

docker-build:
	docker build --build-arg GIT_COMMIT=$$(git rev-parse --short HEAD) -t podcast-agent:local .

docker-run: docker-build
	docker run --rm -t --env-file .env -v "$$PWD/output:/app/output" podcast-agent:local

docker-offline: docker-build
	docker run --rm -t -e LLM_PROVIDER=offline -v "$$PWD/output:/app/output" podcast-agent:local

serve:
	.venv/bin/podcast-agent serve --port 8080

worker-demo:        ## drop transcripts into a local queue and drain it
	mkdir -p queue/inbox && cp data/transcripts/* queue/inbox/ && .venv/bin/podcast-agent worker --queue dir://queue --output output/jobs --once

helm-lint:
	helm lint deploy/helm/podcast-agent && helm template demo deploy/helm/podcast-agent > /dev/null
