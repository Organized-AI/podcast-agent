#!/usr/bin/env bash
# Record "dev syncs itself on merge" on the lab cluster: what dev runs now, then what Argo CD does once main
# changes, with nobody touching the cluster. Start it, merge the "Deploy sha-… to dev" pull request, wait.
#
#   deploy/gitops/lab/watch.sh [output-file]        # or: make lab-watch
#
# It waits for the API to be running first, so "before" is a healthy pod, not a failed pull. It exits
# non-zero if no new revision arrives within WAIT_HOURS, and never reports a change that did not happen.
set -uo pipefail
CLUSTER=${CLUSTER:-podcast-agent-lab}
export KUBECONFIG="${LAB_KUBECONFIG:-$HOME/.kube/$CLUSTER}"
API_PORT=${API_PORT:-30080}
WAIT_HOURS=${WAIT_HOURS:-24}
OUT=${1:-/dev/stdout}
APP=podcast-agent-dev NS=podcast-agent-dev

field() { kubectl -n argocd get application "$APP" -o jsonpath="$1" 2>/dev/null; }
snap() {
  echo "argo cd   : $(field '{.status.sync.status} {.status.health.status}') at $(field '{.status.sync.revision}' | cut -c1-12)"
  echo "image     : $(kubectl -n "$NS" get deploy podcast-agent-api -o jsonpath='{.spec.template.spec.containers[0].image}')"
  echo "pods      : $(kubectl -n "$NS" get pods -l app.kubernetes.io/component=api --no-headers | awk '{print $1" "$2" "$3}' | paste -sd ';' -)"
  echo "/healthz  : $(curl -sS --max-time 5 "http://127.0.0.1:$API_PORT/healthz" 2>&1)"
}
ready() { [ "$(kubectl -n "$NS" get deploy podcast-agent-api -o jsonpath='{.status.availableReplicas}' 2>/dev/null)" -ge 1 ] 2>/dev/null; }

echo "waiting for the API to be running before the first snapshot..." >&2
until ready && [ "$(field '{.status.sync.status} {.status.health.status}')" = "Synced Healthy" ]; do sleep 15; done
before=$(field '{.status.sync.revision}')
{
  echo "# command: deploy/gitops/lab/watch.sh   (merge the dev pull request on GitHub; nothing else touches the cluster)"
  echo "# machine: $(hostname -s) · kind $(kind version 2>/dev/null | awk '{print $2}') · Argo CD $(kubectl -n argocd get deploy argocd-server -o jsonpath='{.spec.template.spec.containers[0].image}' | sed 's/.*://')"
  echo "# commit: $(echo "$before" | cut -c1-7)"
  echo "# captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "== before ($(date -u +%H:%M:%SZ))"
  snap
  echo
  echo "== waiting for a new revision of main (Argo CD checks Git on its own; nobody runs kubectl)"
} > "$OUT"
deadline=$(( $(date +%s) + WAIT_HOURS * 3600 ))
rev=$before
while [ "$rev" = "$before" ] && [ "$(date +%s)" -lt "$deadline" ]; do sleep 5; rev=$(field '{.status.sync.revision}'); done
if [ "$rev" = "$before" ]; then
  { echo "no new revision within ${WAIT_HOURS}h"; echo "exit: 1"; } >> "$OUT"; exit 1
fi
{
  echo "Argo CD moved to $(echo "$rev" | cut -c1-12) at $(date -u +%H:%M:%SZ)"
  kubectl -n "$NS" rollout status deploy/podcast-agent-api --timeout=300s 2>&1
  until [ "$(field '{.status.sync.status} {.status.health.status}')" = "Synced Healthy" ]; do sleep 3; done
  echo
  echo "== after ($(date -u +%H:%M:%SZ))"
  snap
  echo
  echo "== Argo CD's record of the sync"
  field 'initiated by : {.status.operationState.operation.initiatedBy}{"\n"}started      : {.status.operationState.startedAt}{"\n"}finished     : {.status.operationState.finishedAt}{"\n"}result       : {.status.operationState.phase}, {.status.operationState.message}{"\n"}'
  echo "exit: 0"
} >> "$OUT"
