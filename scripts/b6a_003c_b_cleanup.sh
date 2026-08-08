#!/usr/bin/env bash
# Immediate cleanup path for the separately approved 003C-B GPU window.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

if [[ $# -ne 4 ]]; then
  echo "usage: $0 KUBECONFIG WORKLOAD_RENDER AUTHORIZATION PACKET_SHA256" >&2
  exit 2
fi

kubeconfig="$1"
workload_render="$2"
authorization="$3"
packet_sha256="$4"

[[ -f "$kubeconfig" ]] || { echo "REFUSING: kubeconfig is absent" >&2; exit 2; }
[[ -f "$workload_render" ]] || { echo "REFUSING: workload render is absent" >&2; exit 2; }
[[ -f "$authorization" ]] || { echo "REFUSING: authorization is absent" >&2; exit 2; }
[[ "$packet_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "REFUSING: exact packet SHA-256 is required" >&2
  exit 2
}
[[ "${AWS_PROFILE:-}" == "medzen" ]] || {
  echo "REFUSING: AWS_PROFILE=medzen is required" >&2
  exit 2
}

# The Deployment is scaled down first. Delete only the exact B6A namespaced
# workload objects and retain the namespace so zero-state queries remain
# trustworthy and do not confuse Namespace NotFound with zero Pods.
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

# EKS is the canonical desired-size control. The separately armed Auto Scaling
# scheduled action remains in place until every zero-state proof below passes.
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

# Disarm refuses unless EKS, the underlying ASG, GPU nodes, B6A Pods and
# Deployment replicas are all proven zero. Failure intentionally leaves the
# AWS-side deadline action armed.
.venv/bin/python scripts/b6a_003c_b_deadline.py disarm \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --kubeconfig "$kubeconfig"
