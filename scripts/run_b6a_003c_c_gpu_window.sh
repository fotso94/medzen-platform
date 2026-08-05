#!/usr/bin/env bash
# Execute only a separately approved stable-readiness 003C-C retry.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

if [[ $# -ne 7 ]]; then
  echo "usage: $0 KUBECONFIG WORKLOAD_RENDER SYNTHETIC_WAV AUTHORIZATION PACKET_SHA256 DEADLINE_RECEIPT PROOF_RECEIPT" >&2
  exit 2
fi
kubeconfig="$1"
workload_render="$2"
synthetic_wav="$3"
authorization="$4"
packet_sha256="$5"
deadline_receipt="$6"
proof_receipt="$7"

[[ "${AWS_PROFILE:-}" == "medzen" ]] || {
  echo "REFUSING: AWS_PROFILE=medzen is required" >&2
  exit 2
}

cleanup() {
  original_status=$?
  trap - EXIT INT TERM
  set +e
  scripts/b6a_003c_c_cleanup.sh \
    "$kubeconfig" "$workload_render" "$authorization" "$packet_sha256"
  cleanup_status=$?
  if [[ $cleanup_status -ne 0 ]]; then
    echo "REFUSING: cleanup proof failed; 003C-C deadline remains armed" >&2
    exit "$cleanup_status"
  fi
  exit "$original_status"
}
trap cleanup EXIT INT TERM

.venv/bin/python scripts/b6a_003c_c_deadline.py arm \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --window-seconds 7140 \
  --receipt "$deadline_receipt"
.venv/bin/python scripts/b6a_003c_c_deadline.py verify \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256"

aws eks update-nodegroup-config \
  --cluster-name medzen-speech \
  --nodegroup-name gpu \
  --scaling-config minSize=0,maxSize=1,desiredSize=1 \
  --region eu-central-1 \
  --profile medzen >/dev/null
aws eks wait nodegroup-active \
  --cluster-name medzen-speech \
  --nodegroup-name gpu \
  --region eu-central-1 \
  --profile medzen

# Rollout is necessary but not sufficient. The proof script then requires the
# same ready Pod and ResourceSlice fingerprint on three consecutive reads.
kubectl --kubeconfig "$kubeconfig" rollout status \
  daemonset/dra-driver-nvidia-gpu-kubelet-plugin \
  --namespace nvidia-dra-driver --timeout=15m

.venv/bin/python scripts/run_b6a_003c_c_proof.py \
  --kubeconfig "$kubeconfig" \
  --workload "$workload_render" \
  --audio "$synthetic_wav" \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --receipt "$proof_receipt"
