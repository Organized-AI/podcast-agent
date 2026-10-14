##@ Kubernetes
CHART := deploy/helm/podcast-agent

helm: ## Lint the chart and render it with default values
	helm lint $(CHART)
	helm template demo $(CHART) > /dev/null
.PHONY: helm
