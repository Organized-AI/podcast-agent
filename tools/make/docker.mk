##@ Containers
IMAGE   ?= podcast-agent:local
COMPOSE := docker compose -f deploy/compose/docker-compose.yml

docker-build: ## Build the image, stamping the git commit into run manifests
	docker build --build-arg GIT_COMMIT=$$(git rev-parse --short HEAD) -t $(IMAGE) .

docker-run: docker-build ## Run every transcript with the key in .env
	docker run --rm -t --env-file .env -v "$$PWD/output:/app/output" $(IMAGE)

docker-offline: docker-build ## Run every transcript offline in the container
	docker run --rm -t -e LLM_PROVIDER=offline -v "$$PWD/output:/app/output" $(IMAGE)

compose-run: ## One batch run through Compose
	$(COMPOSE) run --rm agent
.PHONY: docker-build docker-run docker-offline compose-run
