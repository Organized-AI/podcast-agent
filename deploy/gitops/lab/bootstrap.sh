#!/usr/bin/env bash
# A standing lab cluster that runs dev the way production would: Argo CD on kind, tracking main.
#
# After a merge, publish.yml builds the image and opens "Deploy sha-… to dev". Merging that pull request is
# the only step left: Argo CD sees the new digest in deploy/gitops/envs/dev/values.yaml and rolls the pods.
# Nobody runs kubectl or helm against the cluster.
#
#   deploy/gitops/lab/bootstrap.sh                     # or: make lab
#   TAILSCALE_SERVE=1 deploy/gitops/lab/bootstrap.sh   # also publish the API and Argo CD to the tailnet only
#
# Safe to re-run: it creates what is missing and re-applies the settings. It never touches other kind
# clusters or the machine's current kubectl context: the lab has its own kubeconfig (~/.kube/<cluster>), and
# its ports listen on 127.0.0.1 unless LISTEN says otherwise.
set -euo pipefail
cd "$(dirname "$0")/../../.."

CLUSTER=${CLUSTER:-podcast-agent-lab}
export KUBECONFIG="${LAB_KUBECONFIG:-$HOME/.kube/$CLUSTER}"
LISTEN=${LISTEN:-127.0.0.1}
API_PORT=${API_PORT:-30080}
ARGOCD_PORT=${ARGOCD_PORT:-30090}
REVISION=${REVISION:-main}
RECONCILE=${RECONCILE:-30s}     # how often Argo CD checks Git; the default is 180s
APP=podcast-agent-dev
NS=podcast-agent-dev
# One pinned, checksum-verified Argo CD release for the e2e test and the lab.
eval "$(sed -n '/^ARGOCD_VERSION=/p; /^ARGOCD_SHA256=/p' e2e/gitops-kind.sh)"   # bash 3.2 cannot source <(...)
[ -n "${ARGOCD_VERSION:-}" ] && [ -n "${ARGOCD_SHA256:-}" ] || { echo "no pinned Argo CD in e2e/gitops-kind.sh"; exit 1; }

if command -v uv >/dev/null; then PY=(uv run --no-project --quiet --with pyyaml python); else PY=(python3); fi
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
step() { printf '\n== %s\n' "$*"; }

step "kind cluster $CLUSTER (kubeconfig $KUBECONFIG)"
mkdir -p "$(dirname "$KUBECONFIG")"
if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --wait 180s --config - <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - { containerPort: $API_PORT, hostPort: $API_PORT, listenAddress: "$LISTEN", protocol: TCP }
      - { containerPort: $ARGOCD_PORT, hostPort: $ARGOCD_PORT, listenAddress: "$LISTEN", protocol: TCP }
YAML
else
  kind export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null
fi

step "Argo CD $ARGOCD_VERSION"
manifest=$(mktemp)
curl -sSfL -o "$manifest" "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
[ "$(sha256 "$manifest")" = "$ARGOCD_SHA256" ] || { echo "Argo CD manifest checksum mismatch"; exit 1; }
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -n argocd --server-side --force-conflicts -f "$manifest" >/dev/null

step "Argo CD settings: check Git every $RECONCILE, read-only dashboard without login, HTTP on :$ARGOCD_PORT"
# The dashboard is reachable only through localhost or the tailnet; anonymous users can look, not sync.
kubectl -n argocd patch configmap argocd-cm --type merge -p "{\"data\":{
  \"timeout.reconciliation\":\"$RECONCILE\",\"timeout.reconciliation.jitter\":\"0s\",
  \"users.anonymous.enabled\":\"true\"}}" >/dev/null
kubectl -n argocd patch configmap argocd-rbac-cm --type merge -p '{"data":{"policy.default":"role:readonly"}}' >/dev/null
kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{"data":{"server.insecure":"true"}}' >/dev/null
kubectl -n argocd patch service argocd-server -p "{\"spec\":{\"type\":\"NodePort\",
  \"ports\":[{\"port\":80,\"nodePort\":$ARGOCD_PORT}]}}" >/dev/null
kubectl -n argocd rollout restart deploy/argocd-server statefulset/argocd-application-controller >/dev/null
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s
kubectl -n argocd rollout status deploy/argocd-applicationset-controller --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s

step "AppProject + dev Application tracking $REVISION (image from deploy/gitops/envs/dev/values.yaml)"
kubectl apply -f deploy/gitops/argocd/project.yaml
"${PY[@]}" e2e/render_appset.py --revision "$REVISION" --env dev \
  --set api.service.type=NodePort --set "api.service.nodePort=$API_PORT" | kubectl apply -f -

if [ -n "${TAILSCALE_SERVE:-}" ]; then
  step "publishing to the tailnet only"
  TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
  "$TS" serve --bg --http="$API_PORT" "http://127.0.0.1:$API_PORT" >/dev/null
  "$TS" serve --bg --http="$ARGOCD_PORT" "http://127.0.0.1:$ARGOCD_PORT" >/dev/null
  "$TS" serve status
fi

step "waiting for $APP to be Synced and Healthy"
status=""
for _ in $(seq 1 60); do
  status=$(kubectl -n argocd get application "$APP" -o jsonpath='{.status.sync.status} {.status.health.status}' 2>/dev/null || true)
  [ "$status" = "Synced Healthy" ] && break
  sleep 5
done
echo "  $APP: ${status:-not created yet}"
if [ "$status" != "Synced Healthy" ]; then
  kubectl -n "$NS" get pods 2>/dev/null || true
  echo "Not healthy yet. If pods show ImagePullBackOff, the GHCR package is still private." >&2
  exit 1
fi
echo "  revision $(kubectl -n argocd get application "$APP" -o jsonpath='{.status.sync.revision}' | cut -c1-12)"
echo "  /healthz $(curl -sS "http://127.0.0.1:$API_PORT/healthz")"
echo "  Argo CD  http://127.0.0.1:$ARGOCD_PORT  (read-only without login; admin password:"
echo "           kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
