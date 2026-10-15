#!/usr/bin/env bash
# Argo CD on kind, end to end: install Argo CD, apply the AppProject and the ApplicationSet (dev only, at a
# given commit, with a locally built image), wait for podcast-agent-dev to be Synced and Healthy, then
# call the API's /healthz from inside its pod.
#
#   e2e/gitops-kind.sh [commit]        # default: HEAD, which must be pushed (Argo CD fetches it from GitHub)
#   CLUSTER=podcast-agent-e2e KEEP=1 e2e/gitops-kind.sh
set -euo pipefail
cd "$(dirname "$0")/.."

REVISION=${1:-$(git rev-parse HEAD)}
CLUSTER=${CLUSTER:-podcast-agent-e2e}
IMAGE=podcast-agent:e2e
ARGOCD_VERSION=v3.5.4
ARGOCD_SHA256=1feb02cc7bacf3a379da58b0ca8c6f07d18288cd04d531dabd08816375e5e010
APP=podcast-agent-dev
NS=podcast-agent-dev

# render_appset.py needs PyYAML: use uv when it is installed, the system python otherwise.
if command -v uv >/dev/null; then PY=(uv run --no-project --quiet --with pyyaml python); else PY=(python3); fi
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
step() { printf '\n== %s\n' "$*"; }

step "kind cluster $CLUSTER"
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s
kubectl config use-context "kind-$CLUSTER" >/dev/null

step "image $IMAGE"
docker build -q --build-arg GIT_COMMIT="${REVISION:0:7}" -t "$IMAGE" . >/dev/null
kind load docker-image "$IMAGE" --name "$CLUSTER"

step "Argo CD $ARGOCD_VERSION"
manifest=$(mktemp)
curl -sSfL -o "$manifest" "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
[ "$(sha256 "$manifest")" = "$ARGOCD_SHA256" ] || { echo "Argo CD manifest checksum mismatch"; exit 1; }
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -n argocd --server-side --force-conflicts -f "$manifest" >/dev/null
# Every component, Redis included: the repo-server caches manifests in Redis, and an Application created while
# Redis is still starting can fail its first comparison with DeadlineExceeded (seen on #69 and #88).
kubectl -n argocd wait deploy --all --for=condition=Available --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s

step "AppProject + ApplicationSet at ${REVISION:0:12}"
kubectl apply -f deploy/gitops/argocd/project.yaml
"${PY[@]}" e2e/render_appset.py --revision "$REVISION" --env dev --image "$IMAGE" | kubectl apply -f -

step "waiting for $APP to be Synced and Healthy"
status=""
for i in $(seq 1 120); do
  status=$(kubectl -n argocd get application "$APP" -o jsonpath='{.status.sync.status} {.status.health.status}' 2>/dev/null || true)
  echo "  $status"
  [ "$status" = "Synced Healthy" ] && break
  # A comparison that failed (say, a manifest generation that timed out) waits for the next reconcile; ask for a
  # fresh one every 30 seconds instead.
  if [ $((i % 6)) = 0 ] && kubectl -n argocd get application "$APP" \
       -o jsonpath='{.status.conditions[*].type}' 2>/dev/null | grep -q ComparisonError; then
    echo "  comparison failed; asking Argo CD for a hard refresh"
    kubectl -n argocd annotate application "$APP" argocd.argoproj.io/refresh=hard --overwrite >/dev/null
  fi
  sleep 5
done
if [ "$status" != "Synced Healthy" ]; then
  kubectl -n argocd get application "$APP" -o yaml | sed -n '/^status:/,$p' | head -60
  kubectl -n "$NS" get pods,events 2>/dev/null | tail -30
  kubectl -n argocd get pods -o wide
  kubectl -n argocd logs deploy/argocd-repo-server --tail=40 || true
  exit 1
fi

step "resources Argo CD manages"
kubectl -n argocd get application "$APP" -o jsonpath='{range .status.resources[*]}{.kind}/{.name} {.status} {.health.status}{"\n"}{end}'

step "/healthz from the API pod"
kubectl -n "$NS" exec deploy/podcast-agent-api -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/healthz').read().decode())"

if [ -z "${KEEP:-}" ]; then step "deleting $CLUSTER"; kind delete cluster --name "$CLUSTER"; fi
echo "PASS: Argo CD deployed $APP from ${REVISION:0:12} and the API is healthy"
