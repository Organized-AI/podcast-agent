#!/usr/bin/env bash
# A standing lab cluster that runs dev the way production would: Argo CD on kind, tracking main.
#
# After a merge, publish.yml builds the image and opens "Deploy sha-… to dev". Merging that pull request is
# the only step left: Argo CD sees the new digest in deploy/gitops/envs/dev/values.yaml and rolls the pods.
# Nobody runs kubectl or helm against the cluster.
#
#   deploy/gitops/lab/bootstrap.sh                     # or: make lab
#   TAILSCALE_SERVE=1 deploy/gitops/lab/bootstrap.sh   # also publish the lab's ports to the tailnet only
#   OBSERVABILITY=0 deploy/gitops/lab/bootstrap.sh     # skip the observability stack
#
# The observability stack (deploy/gitops/argocd/observability.yaml) adds the Collector, Jaeger, Prometheus,
# Alertmanager and a private MLflow (Postgres, basic auth), with their ports on the node: Jaeger 30686, MLflow 30500,
# Prometheus 30909, Alertmanager 30903. Their passwords are generated here into the mlflow-secrets Secret and never
# leave the cluster. For the incident bridge to open GitHub issues, pass a token that can write them:
#   INCIDENT_GITHUB_TOKEN=... deploy/gitops/lab/bootstrap.sh    or    INCIDENT_TOKEN_FROM_GH=1 (uses `gh auth token`)
# Without one the bridge runs dry and only logs the issues it would open.
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
OBSERVABILITY=${OBSERVABILITY:-1}
OBS_PORTS="30686 30500 30909 30903"   # Jaeger, MLflow, Prometheus, Alertmanager (NodePorts in deploy/gitops/observability)
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
$(for p in $OBS_PORTS; do echo "      - { containerPort: $p, hostPort: $p, listenAddress: \"$LISTEN\", protocol: TCP }"; done)
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
# The observability kustomization reads the alert rules from deploy/observability, outside its own directory.
kubectl -n argocd patch configmap argocd-cm --type merge -p "{\"data\":{
  \"timeout.reconciliation\":\"$RECONCILE\",\"timeout.reconciliation.jitter\":\"0s\",
  \"users.anonymous.enabled\":\"true\",
  \"kustomize.buildOptions\":\"--load-restrictor LoadRestrictionsNone\"}}" >/dev/null
kubectl -n argocd patch configmap argocd-rbac-cm --type merge -p '{"data":{"policy.default":"role:readonly"}}' >/dev/null
kubectl -n argocd patch configmap argocd-cmd-params-cm --type merge -p '{"data":{"server.insecure":"true"}}' >/dev/null
kubectl -n argocd patch service argocd-server -p "{\"spec\":{\"type\":\"NodePort\",
  \"ports\":[{\"port\":80,\"nodePort\":$ARGOCD_PORT}]}}" >/dev/null
# The lab uses neither SSO nor notifications; turning them off keeps it light next to other workloads.
kubectl -n argocd scale deploy/argocd-dex-server deploy/argocd-notifications-controller --replicas=0 >/dev/null
kubectl -n argocd rollout restart deploy/argocd-server statefulset/argocd-application-controller >/dev/null
kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s
kubectl -n argocd rollout status deploy/argocd-applicationset-controller --timeout=300s
kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s

step "AppProject + dev Application tracking $REVISION (image from deploy/gitops/envs/dev/values.yaml)"
kubectl apply -f deploy/gitops/argocd/project.yaml
"${PY[@]}" e2e/render_appset.py --revision "$REVISION" --env dev \
  --set api.service.type=NodePort --set "api.service.nodePort=$API_PORT" | kubectl apply -f -

if [ "$OBSERVABILITY" = 1 ]; then
  step "observability: secrets (generated once, kept in the cluster) and the Argo CD Applications"
  kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  if ! kubectl -n observability get secret mlflow-secrets >/dev/null 2>&1; then
    rand() { openssl rand -hex 24; }
    pg=$(rand); admin=$(rand); collector=$(rand)
    ini=$(mktemp); chmod 600 "$ini"
    cat > "$ini" <<INI
[mlflow]
default_permission = READ
database_uri = postgresql://mlflow:$pg@postgres:5432/mlflow_auth
admin_username = admin
admin_password = $admin
authorization_function = mlflow.server.auth:authenticate_request_basic_auth
INI
    kubectl -n observability create secret generic mlflow-secrets \
      --from-literal=POSTGRES_PASSWORD="$pg" \
      --from-literal=MLFLOW_BACKEND_STORE_URI="postgresql://mlflow:$pg@postgres:5432/mlflow" \
      --from-literal=MLFLOW_FLASK_SERVER_SECRET_KEY="$(rand)" \
      --from-literal=ADMIN_PASSWORD="$admin" \
      --from-literal=COLLECTOR_PASSWORD="$collector" \
      --from-literal=COLLECTOR_BASIC_AUTH="$(printf 'collector:%s' "$collector" | base64 | tr -d '\n')" \
      --from-file=basic_auth.ini="$ini" >/dev/null
    rm -f "$ini"
    echo "  created mlflow-secrets (MLflow admin password: kubectl -n observability get secret mlflow-secrets -o jsonpath='{.data.ADMIN_PASSWORD}' | base64 -d)"
  else
    echo "  mlflow-secrets exists; kept"
  fi
  sed "s|targetRevision: main|targetRevision: $REVISION|" deploy/gitops/argocd/observability.yaml | kubectl apply -f - >/dev/null
  echo "  Applications observability and mlflow tracking $REVISION"
fi

token=${INCIDENT_GITHUB_TOKEN:-}
if [ -z "$token" ] && [ -n "${INCIDENT_TOKEN_FROM_GH:-}" ]; then token=$(gh auth token); fi
if [ -n "$token" ]; then
  step "incident bridge: GitHub token Secret in $NS"
  kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  printf '%s' "$token" | kubectl -n "$NS" create secret generic incident-bridge-github --from-file=token=/dev/stdin \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n "$NS" rollout restart deploy/podcast-agent-incident-bridge >/dev/null 2>&1 || true
  echo "  incident-bridge-github set; the bridge opens real issues"
  unset token
fi

if [ -n "${TAILSCALE_SERVE:-}" ]; then
  step "publishing to the tailnet only"
  TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
  for port in "$API_PORT" "$ARGOCD_PORT" $([ "$OBSERVABILITY" = 1 ] && echo $OBS_PORTS); do
    "$TS" serve --bg --http="$port" "http://127.0.0.1:$port" >/dev/null
  done
  "$TS" serve status
fi

step "waiting for the Applications to be Synced and Healthy"
wait_app() {
  local app=$1 status=""
  for _ in $(seq 1 120); do
    status=$(kubectl -n argocd get application "$app" -o jsonpath='{.status.sync.status} {.status.health.status}' 2>/dev/null || true)
    [ "$status" = "Synced Healthy" ] && break
    sleep 5
  done
  echo "  $app: ${status:-not created yet}"
  [ "$status" = "Synced Healthy" ]
}
if [ "$OBSERVABILITY" = 1 ]; then wait_app observability || true; wait_app mlflow || true; fi
wait_app "$APP" && status="Synced Healthy" || status="not healthy"
if [ "$status" != "Synced Healthy" ]; then
  kubectl -n "$NS" get pods 2>/dev/null || true
  echo "Not healthy yet. If pods show ImagePullBackOff, the GHCR package is still private." >&2
  exit 1
fi
echo "  revision $(kubectl -n argocd get application "$APP" -o jsonpath='{.status.sync.revision}' | cut -c1-12)"
echo "  /healthz $(curl -sS "http://127.0.0.1:$API_PORT/healthz")"
echo "  Argo CD  http://127.0.0.1:$ARGOCD_PORT  (read-only without login; admin password:"
echo "           kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
if [ "$OBSERVABILITY" = 1 ]; then
  echo "  Jaeger   http://127.0.0.1:30686   MLflow http://127.0.0.1:30500 (user admin)"
  echo "  Prometheus http://127.0.0.1:30909   Alertmanager http://127.0.0.1:30903"
fi
