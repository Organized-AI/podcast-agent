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
	@echo "== prod's NetworkPolicy lets pods reach the Collector (telemetry is on there)"
	@helm template podcast-agent $(CHART) -f $(CHART)/values-prod.yaml --show-only templates/networkpolicy.yaml \
	  | grep -q 'port: 4318' || { echo "prod turns telemetry on, but its NetworkPolicy allows no egress to the OTLP port"; exit 1; }
	@echo "== Argo CD manifests"
	@if command -v kubeconform >/dev/null; then \
	  kubeconform -strict -summary -schema-location default -schema-location '$(CRD_SCHEMAS)' deploy/gitops/argocd/; \
	else echo "kubeconform not installed: skipped"; fi

gitops-e2e: ## Argo CD on a throwaway kind cluster deploys dev from HEAD (HEAD must be pushed)
	e2e/gitops-kind.sh
LAB := podcast-agent-lab
LAB_KUBECONFIG := $(HOME)/.kube/$(LAB)

lab: ## Standing lab cluster: Argo CD on kind tracking main (TAILSCALE_SERVE=1 publishes it to the tailnet)
	deploy/gitops/lab/bootstrap.sh

lab-status: ## What the lab runs: Argo CD's view of dev, then the API's /healthz
	@KUBECONFIG=$(LAB_KUBECONFIG) kubectl -n argocd get application podcast-agent-dev \
	  -o jsonpath='{.status.sync.status} {.status.health.status} at {.status.sync.revision}{"\n"}'
	@curl -sS http://127.0.0.1:30080/healthz; echo

lab-watch: ## Record dev syncing itself: run, merge the dev pull request, read samples/proof/lab-sync.txt
	deploy/gitops/lab/watch.sh samples/proof/lab-sync.txt

drill-preflight: ## Check that the lab is ready for the cost drill (synced, rule loaded, canary running, nothing firing)
	$(UV) run python tools/drill/cost_drill.py --preflight

drill-cost: ## Cost drill: break the episode budget with a real PR, time the alert and the fix, write the postmortem
	$(UV) run python tools/drill/cost_drill.py

lab-down: ## Delete the lab cluster (other kind clusters are untouched)
	kind delete cluster --name $(LAB) --kubeconfig $(LAB_KUBECONFIG)
.PHONY: helm gitops-e2e lab lab-status lab-watch lab-down drill-preflight drill-cost
