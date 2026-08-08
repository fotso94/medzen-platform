#!/usr/bin/env bash
# Runs as root through AWS-RunShellScript on the exact GPU EC2 instance.
set -euo pipefail

refuse() {
  printf 'MEDZEN_SAMPLER_SELF_TEST_V1 status=REFUSED code=%s\n' "$1"
  exit 1
}

[[ "${MEDZEN_DRA_POD_UID:-}" =~ ^[0-9a-f-]{36}$ ]] || refuse INVALID_POD_UID
[[ "${MEDZEN_DRA_IMAGE_DIGEST:-}" =~ ^sha256:[0-9a-f]{64}$ ]] || refuse INVALID_DIGEST
[[ -x /usr/local/bin/nerdctl ]] || refuse NERDCTL_ABSENT

mapfile -t containers < <(
  /usr/local/bin/nerdctl --namespace k8s.io ps \
    --filter label=io.kubernetes.container.name=gpus \
    --filter "label=io.kubernetes.pod.uid=${MEDZEN_DRA_POD_UID}" \
    --no-trunc --quiet 2>/dev/null
)
[[ ${#containers[@]} -eq 1 ]] || refuse DRA_CONTAINER_AMBIGUOUS
container_id="${containers[0]}"
inspect="$(
  /usr/local/bin/nerdctl --namespace k8s.io inspect "$container_id" 2>/dev/null
)" || refuse DRA_INSPECT_FAILED
grep -Fq "${MEDZEN_DRA_IMAGE_DIGEST}" <<<"$inspect" || refuse DRA_DIGEST_MISMATCH

samples=0
minimum=999999999
peak=0
total_seen=0
for ((iteration = 0; iteration < 120; iteration++)); do
  line="$(
    /usr/local/bin/nerdctl --namespace k8s.io exec "$container_id" \
      /busybox/chroot /driver-root /usr/bin/nvidia-smi \
      --query-gpu=index,memory.used,memory.total \
      --format=csv,noheader,nounits 2>/dev/null
  )" || refuse NVIDIA_SMI_EXEC_FAILED
  parsed="$(
    awk -F, '
      NF == 3 {
        for (i = 1; i <= 3; i++) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i) }
        if ($1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/) {
          print $1 " " $2 " " $3
        }
      }
    ' <<<"$line"
  )"
  [[ $(wc -l <<<"$parsed" | tr -d ' ') -eq 1 ]] || refuse NON_NUMERIC_SAMPLE
  read -r gpu_index used total <<<"$parsed"
  [[ "$gpu_index" -eq 0 && "$used" -le "$total" && "$total" -gt 0 ]] || refuse INVALID_SAMPLE
  if [[ $total_seen -ne 0 && $total -ne $total_seen ]]; then
    refuse TOTAL_MEMORY_CHANGED
  fi
  ((used < minimum)) && minimum=$used
  ((used > peak)) && peak=$used
  total_seen=$total
  ((samples += 1))
  [[ $iteration -eq 119 ]] || sleep 1
done

[[ $samples -eq 120 ]] || refuse INCOMPLETE_SAMPLE_SET
printf 'MEDZEN_SAMPLER_SELF_TEST_V1 status=PASS samples=%d gpu_index=0 min_used_mib=%d peak_used_mib=%d total_mib=%d\n' \
  "$samples" "$minimum" "$peak" "$total_seen"
