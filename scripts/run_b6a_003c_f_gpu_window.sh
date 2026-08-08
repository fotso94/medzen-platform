#!/usr/bin/env bash
# Execute only a separately approved, independently reviewed 003C-F packet.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"

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

.venv/bin/python scripts/b6a_003c_f_bindings.py \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --workload "$workload_render" \
  --audio "$synthetic_wav" \
  --receipts-dir "$receipts_dir"

cleanup() {
  original_status=$?
  trap - EXIT INT TERM
  set +e
  scripts/b6a_003c_f_cleanup.sh \
    "$kubeconfig" "$workload_render" "$authorization" "$packet_sha256" "$receipts_dir"
  cleanup_status=$?
  if [[ $cleanup_status -ne 0 ]]; then
    echo "REFUSING: cleanup proof failed; 003C-F deadline remains armed" >&2
    exit "$cleanup_status"
  fi
  exit "$original_status"
}
trap cleanup EXIT INT TERM

.venv/bin/python scripts/b6a_003c_f_deadline.py arm \
  --authorization "$authorization" \
  --packet-sha256 "$packet_sha256" \
  --receipts-dir "$receipts_dir" \
  --window-seconds 4610
.venv/bin/python scripts/b6a_003c_f_deadline.py verify \
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

.venv/bin/python scripts/run_b6a_003c_f_sampler_self_test.py \
  --kubeconfig "$kubeconfig" \
  --receipts-dir "$receipts_dir"

# The 003C-E proof code remains immutable and is reused by exact hash. It
# already persists transcription before starting its independent memory sampler.
.venv/bin/python scripts/run_b6a_003c_e_proof.py \
  --kubeconfig "$kubeconfig" \
  --workload "$workload_render" \
  --audio "$synthetic_wav" \
  --receipts-dir "$receipts_dir"
