#!/usr/bin/env bash
# Attach an image's signed SLSA build provenance to its GitHub Release.
#
#   tools/ci/release_provenance.sh v0.2.0 sha256:<digest>      (needs GH_TOKEN; GITHUB_REPOSITORY or REPO)
#
# Reads the attestation publish.yml created for the digest (GitHub's attestations API) and uploads:
#   podcast-agent-<tag>.sigstore.json   the Sigstore bundle: gh attestation verify ... --bundle <file>
#   podcast-agent-<tag>.intoto.jsonl    the signed DSSE envelope carrying the in-toto SLSA statement
set -euo pipefail
TAG=$1 DIGEST=$2
REPO=${REPO:-${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY or REPO}}
[[ $DIGEST =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "not an image digest: $DIGEST" >&2; exit 1; }
SLSA='select(.bundle.dsseEnvelope.payload | @base64d | fromjson | .predicateType == "https://slsa.dev/provenance/v1")'
out=$(mktemp -d)
gh api "repos/$REPO/attestations/$DIGEST" --jq "[.attestations[] | $SLSA][0].bundle" > "$out/podcast-agent-$TAG.sigstore.json"
gh api "repos/$REPO/attestations/$DIGEST" --jq ".attestations[] | $SLSA | .bundle.dsseEnvelope | tojson" \
  > "$out/podcast-agent-$TAG.intoto.jsonl"
[ -s "$out/podcast-agent-$TAG.intoto.jsonl" ] && grep -q '"payloadType"' "$out/podcast-agent-$TAG.sigstore.json" \
  || { echo "no SLSA provenance attestation for $DIGEST in $REPO" >&2; exit 1; }
gh release upload "$TAG" "$out/podcast-agent-$TAG.sigstore.json" "$out/podcast-agent-$TAG.intoto.jsonl" --repo "$REPO" --clobber
echo "attached provenance for $DIGEST to $TAG"
