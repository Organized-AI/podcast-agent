##@ Kubernetes
CHART := deploy/helm/podcast-agent
ENVS  := default dev prod
CRD_SCHEMAS := https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json

helm: ## Lint the chart against values.schema.json and validate the rendered manifests for every environment
	@for env in $(ENVS); do \
	  values=$$([ "$$env" = default ] || echo "-f $(CHART)/values-$$env.yaml"); \
	  echo "== $$env"; \
	  helm lint --strict $(CHART) $$values --quiet || exit 1; \
	  if command -v kubeconform >/dev/null; then \
	    helm template podcast-agent $(CHART) $$values -n podcast-agent-$$env \
	      | kubeconform -strict -summary -schema-location default -schema-location '$(CRD_SCHEMAS)' - || exit 1; \
	  else echo "kubeconform not installed: rendered only"; helm template podcast-agent $(CHART) $$values > /dev/null || exit 1; fi; \
	done
.PHONY: helm
