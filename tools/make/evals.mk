##@ Evals
EVAL_OUT ?= evals/reports

evals: ## Offline eval suite: fact-check cases + committed runs, gated by thresholds
	$(PA) eval --suite evals/suites/offline.yaml --out $(EVAL_OUT)/offline

evals-score: ## Score a run folder against the episode expectations: make evals-score RUN=output
	$(PA) eval --score $(RUN) --out $(EVAL_OUT)/score
.PHONY: evals evals-score
