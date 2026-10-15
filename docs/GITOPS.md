# GitOps

Git is the only way anything reaches a cluster. Argo CD watches `main` and renders the Helm chart once per environment; CI never talks to a cluster.

```
 PR merged to main ─► publish workflow: build the image once, push ghcr.io/organized-ai/podcast-agent:sha-<sha>
                       └─► bot PR: deploy/gitops/envs/dev/values.yaml → that digest ─► merge ─► Argo CD syncs dev
 tag vX.Y.Z on main ─► release workflow: re-tag the same digest vX.Y.Z (no rebuild), GitHub Release
                       └─► bot PR: deploy/gitops/envs/prod/values.yaml → that digest ─► merge ─► someone presses Sync on prod
```

## Layout

| Path | What it is |
|---|---|
| [deploy/gitops/argocd/project.yaml](../deploy/gitops/argocd/project.yaml) | AppProject: this repository only; the `podcast-agent-dev` and `podcast-agent-prod` namespaces only; only the namespaced kinds the chart renders; no cluster-scoped resources |
| [deploy/gitops/argocd/applicationset.yaml](../deploy/gitops/argocd/applicationset.yaml) | One Application per environment from a list generator; dev gets automated sync with prune and self-heal through `templatePatch`, prod syncs by hand |
| `deploy/helm/podcast-agent/values-<env>.yaml` | The environment's shape: provider, replicas, KEDA, External Secrets, telemetry, alerts |
| [deploy/gitops/envs/&lt;env&gt;/values.yaml](../deploy/gitops/envs) | Only the image tag and digest, the one thing automation writes |

Values are layered `values.yaml` → `values-<env>.yaml` → `envs/<env>/values.yaml`, and `values.schema.json` rejects unknown keys at render time.

## Bootstrap

On a cluster with Argo CD installed (namespace `argocd`):

```bash
kubectl apply -f deploy/gitops/argocd/project.yaml
kubectl apply -f deploy/gitops/argocd/applicationset.yaml
kubectl -n argocd get applications            # podcast-agent-dev (auto), podcast-agent-prod (manual)
```

prod expects External Secrets with a `ClusterSecretStore` named `aws-secrets-manager`, KEDA, and the Prometheus Operator (see [DEPLOYMENT.md](DEPLOYMENT.md)). dev expects nothing: it runs the offline provider with no API key and no worker.

## Proving it on kind

```bash
make gitops-e2e          # or: e2e/gitops-kind.sh <commit>   (the commit must be pushed)
```

[e2e/gitops-kind.sh](../e2e/gitops-kind.sh) creates a throwaway kind cluster and installs a checksum-verified Argo CD v3.5.4. It applies the project and an ApplicationSet rendered for dev at the given commit, using a locally built image, because an unmerged commit has no published image. It then waits for `podcast-agent-dev` to be Synced and Healthy and calls `/healthz` inside the API pod. The `gitops-e2e` workflow runs the same script on every pull request that touches `deploy/`.

## Checks on every pull request

- `make helm`: `helm lint --strict` for every environment against the values schema; the rendered manifests and the Argo CD manifests validated with kubeconform, including the CRDs (Argo CD, KEDA, External Secrets, Prometheus Operator)
- `tests/test_gitops.py`: every value file the ApplicationSet references exists inside the repository, every environment has an allowed destination, only dev syncs itself, and the environment files hold nothing but the image
- Trivy scans the chart for misconfiguration
