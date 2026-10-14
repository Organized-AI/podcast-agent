##@ Guide
guide: ## Refill the guide's Audit trail tab from a run folder: make guide RUN=output
	$(PA) audit $(RUN) && $(UV) run python tools/guide/embed_audit.py $(RUN) samples/proof website/public/index.html

guide-deploy: ## Deploy the guide to Cloudflare Workers (needs wrangler login)
	cd website && npx wrangler deploy
.PHONY: guide guide-deploy
