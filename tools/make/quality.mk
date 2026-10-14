##@ Quality
lint: ## ruff (includes bandit security rules)
	$(UV) run ruff check src tests tools

types: ## mypy on the package
	$(UV) run mypy

test: ## Hermetic tests with the coverage floor
	$(UV) run pytest -q --cov --cov-report=term

drift: ## Generated files and versions agree with their sources
	$(UV) run python tools/ci/gen_config.py --check
	$(UV) run python tools/ci/check_version.py
	$(UV) lock --check

check: lint types test drift ## Everything a pull request must pass locally

precommit: ## Run every pre-commit hook on all files
	$(UV) run --with pre-commit pre-commit run --all-files
.PHONY: lint types test drift check precommit
