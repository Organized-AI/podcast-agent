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
| [deploy/gitops/argocd/project.yaml](../deploy/gitops/argocd/project.yaml) | AppProject: this repository only; the `podcast-agent-dev` and `podcast-agent-prod` namespaces only; only the namespaced kinds the chart renders; the only cluster-scoped kind is the environment's Namespace, which `CreateNamespace=true` needs |
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

## A standing lab cluster

The kind test proves the wiring and then deletes the cluster. The lab keeps one running, so that "dev syncs itself on merge" can be watched rather than taken on trust:

```bash
make lab                       # or TAILSCALE_SERVE=1 make lab to reach it from other machines on the tailnet
make lab-status                # Synced Healthy at <commit>, then /healthz with the running image's commit
make lab-watch                 # record it: start, merge the dev pull request, read samples/proof/lab-sync.txt
```

[deploy/gitops/lab/bootstrap.sh](../deploy/gitops/lab/bootstrap.sh):

- creates the kind cluster `podcast-agent-lab` with its own kubeconfig, `~/.kube/podcast-agent-lab`, so other clusters and the current kubectl context are untouched;
- installs the same checksum-verified Argo CD as the kind test;
- applies the AppProject and the ApplicationSet's dev Application, tracking `main`.

The image is not overridden: dev runs the digest named in `deploy/gitops/envs/dev/values.yaml`, pulled from GHCR, exactly as a real cluster would. Two lab-only additions:

- The API is exposed as a NodePort on `:30080`, and the Argo CD dashboard on `:30090`.
- Both listen on 127.0.0.1. With `TAILSCALE_SERVE=1` they are also published to the tailnet only, never to the local network.

Argo CD checks Git every 30 seconds instead of every 3 minutes. The dashboard is read-only without a login, so anyone on the tailnet can watch it but not sync or delete.

What a merge looks like:

1. A pull request merges to `main`. `publish` builds and attests `sha-<commit>`, then opens **Deploy sha-<commit> to dev**.
2. You merge that pull request.
3. Within the reconcile interval, Argo CD sees the new digest in Git, renders the chart and rolls the API pod. The **Refresh** button skips the wait.
4. `/healthz` reports the new `commit`.

At no point does anyone run `kubectl` or `helm` against the cluster.

## The automation

| Workflow | Trigger | What it does |
|---|---|---|
| [publish.yml](../.github/workflows/publish.yml) | push to `main` (not docs or environment bumps) | Builds `linux/amd64` and `linux/arm64` once, pushes `sha-<commit>` and `main` with an SBOM and `mode=max` provenance, attests the digest, then opens **Deploy sha-… to dev** (`gitops/dev`) with [tools/ci/set_image.py](../tools/ci/set_image.py) |
| [release.yml](../.github/workflows/release.yml) | tag `vX.Y.Z` on `main` | `check_version.py --tag`; re-tags `sha-<commit>` as `vX.Y.Z` and `X.Y.Z` with `imagetools create` (same digest); GitHub Release from the `CHANGELOG.md` section; opens **Deploy vX.Y.Z to prod** (`gitops/prod`) |

Both bump jobs open their pull requests as the `organized-ai-gitops` GitHub App, using a short-lived token from `actions/create-github-app-token`. The App's pull requests trigger the normal `pull_request` checks, so they need no manual approval and code scanning analyzes them like any other pull request. The App can write contents and pull requests on this repository only. Its ID is the repository variable `DEPLOY_APP_ID`, and its key is the secret `DEPLOY_APP_PRIVATE_KEY`.

Without the App (a fork, or `DEPLOY_APP_ID` unset), the jobs fall back to `GITHUB_TOKEN`. Pull requests opened that way trigger no workflows, so the jobs dispatch `ci.yml` and `security.yml` on the bump branch themselves, and the repository must allow GitHub Actions to create pull requests (Settings → Actions → General).

Verify any image before trusting it:

```bash
gh attestation verify oci://ghcr.io/organized-ai/podcast-agent:v0.2.0 --repo Organized-AI/podcast-agent
```

## Checks on every pull request

- `make helm`: `helm lint --strict` for every environment against the values schema; the rendered manifests and the Argo CD manifests validated with kubeconform, including the CRDs (Argo CD, KEDA, External Secrets, Prometheus Operator)
- `tests/test_gitops.py`: every value file the ApplicationSet references exists inside the repository, every environment has an allowed destination, only dev syncs itself, and the environment files hold nothing but the image
- Trivy scans the chart for misconfiguration
