#!/usr/bin/env bash
# Immediate cleanup path for a separately approved 003C-E execution.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

if [[ $# -ne 5 ]]; then
  echo "usage: $0 KUBECONFIG WORKLOAD_RENDER AUTHORIZATION PACKET_SHA256 RECEIPTS_DIR" >&2
  exit 2
fi
kubeconfig="$1"
workload_render="$2"
authorization="$3"
packet_sha256="$4"
receipts_dir="$5"

[[ -f "$kubeconfig" && -f "$workload_render" && -f "$authorization" ]] || {
  echo "REFUSING: a required 003C-E cleanup file is absent" >&2
  exit 2
}
[[ "$packet_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "REFUSING: exact 003C-E packet SHA-256 is required" >&2
  exit 2
}
[[ "${AWS_PROFILE:-}" == "medzen" ]] || {
  echo "REFUSING: AWS_PROFILE=medzen is required" >&2
  exit 2
}

kubectl --kubeconfig "$kubeconfig" scale deployment/asr-runtime-b6a \
  --namespace medzen --replicas=0 || true
kubectl --kubeconfig "$kubeconfig" delete \
  deployment/asr-runtime-b6a \
  service/asr-runtime-b6a \
  resourceclaimtemplate/asr-runtime-b6a-gpu \
  networkpolicy/asr-runtime-b6a-ingress-deny \
  configmap/asr-runtime-b6a-config \
  serviceaccount/asr-runtime-b6a \
  --namespace medzen --ignore-not-found --wait=true --timeout=10m || true

aws eks update-nodegroup-config \
  --cluster-name medzen-speech \
  --nodegroup-name gpu \
  --scaling-config minSize=0,maxSize=1,desiredSize=0 \
  --region eu-central-1 \
  --profile medzen >/dev/null
aws eks wait nodegroup-active \
  --cluster-name medzen-speech \
  --nodegroup-name gpu \
  --region eu-central-1 \
  --profile medzen

.venv/bin/python scripts/b6a_003c_e_deadline.py disarm \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --receipts-dir "$receipts_dir" \
  --kubeconfig "$kubeconfig"
