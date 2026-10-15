##@ Quality
lint: ## ruff (includes bandit security rules)
	$(UV) run ruff check src tests tools e2e fuzz

types: ## mypy on the package
	$(UV) run mypy

test: ## Hermetic tests with the coverage floor
	$(UV) run pytest -q --cov --cov-report=term

drift: ## Generated files and versions agree with their sources
	$(UV) run python tools/ci/gen_config.py --check
	$(UV) run python tools/ci/check_version.py
	$(UV) run python tools/guide/embed_walkthrough.py --check
	$(UV) lock --check

check: lint types test drift evals ## Everything a pull request must pass locally

precommit: ## Run every pre-commit hook on all files
	$(UV) run --with pre-commit pre-commit run --all-files
.PHONY: lint types test drift check precommit

FUZZ_SECONDS ?= 60
fuzz: ## Run each Atheris fuzzer in fuzz/ for FUZZ_SECONDS (CI runs them with ClusterFuzzLite)
	@for f in fuzz/fuzz_*.py; do echo "== $$f"; \
	  PYTHONPATH=src:fuzz $(UV) run --no-project --python 3.11 --with atheris --with "pydantic>=2" \
	    python $$f -dict=fuzz/transcript.dict -max_total_time=$(FUZZ_SECONDS) -timeout=5 || exit 1; done
.PHONY: fuzz
