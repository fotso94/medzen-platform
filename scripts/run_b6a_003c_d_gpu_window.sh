#!/usr/bin/env bash
# Execute only a separately approved, independently IAM-reviewed 003C-D packet.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

if [[ $# -ne 6 ]]; then
  echo "usage: $0 KUBECONFIG WORKLOAD_RENDER SYNTHETIC_WAV AUTHORIZATION PACKET_SHA256 RECEIPTS_DIR" >&2
  exit 2
fi
kubeconfig="$1"
workload_render="$2"
synthetic_wav="$3"
authorization="$4"
packet_sha256="$5"
receipts_dir="$6"

[[ "${AWS_PROFILE:-}" == "medzen" ]] || {
  echo "REFUSING: AWS_PROFILE=medzen is required" >&2
  exit 2
}

# Read-only/local stage. Nothing has to be cleaned if this refuses.
.venv/bin/python scripts/b6a_003c_d_bindings.py \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --workload "$workload_render" \
  --audio "$synthetic_wav" \
  --receipts-dir "$receipts_dir"

cleanup() {
  original_status=$?
  trap - EXIT INT TERM
  set +e
  scripts/b6a_003c_d_cleanup.sh \
    "$kubeconfig" "$workload_render" "$authorization" "$packet_sha256" "$receipts_dir"
  cleanup_status=$?
  if [[ $cleanup_status -ne 0 ]]; then
    echo "REFUSING: cleanup proof failed; 003C-D deadline remains armed" >&2
    exit "$cleanup_status"
  fi
  exit "$original_status"
}
trap cleanup EXIT INT TERM

.venv/bin/python scripts/b6a_003c_d_deadline.py arm \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --receipts-dir "$receipts_dir" \
  --window-seconds 6520
.venv/bin/python scripts/b6a_003c_d_deadline.py verify \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --receipts-dir "$receipts_dir"

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

kubectl --kubeconfig "$kubeconfig" rollout status \
  daemonset/dra-driver-nvidia-gpu-kubelet-plugin \
  --namespace nvidia-dra-driver --timeout=15m

# This is the first explicit diagnostic run on the GPU node. It exercises the
# exact DRA container + driver-root context for about two minutes through SSM.
# A missing numeric PASS receipt aborts before the model workload is applied.
.venv/bin/python scripts/run_b6a_003c_d_sampler_self_test.py \
  --kubeconfig "$kubeconfig" \
  --receipts-dir "$receipts_dir"

.venv/bin/python scripts/run_b6a_003c_d_proof.py \
  --kubeconfig "$kubeconfig" \
  --workload "$workload_render" \
  --audio "$synthetic_wav" \
  --receipts-dir "$receipts_dir"
