#!/bin/bash
# Rendered by pipeline.ec2_stage_adapter.  One instance executes one immutable
# descriptor, uploads a container result, and shuts itself down.  The operator
# observes termination and root-volume deletion before creating stage-result.
set -Eeuo pipefail

REGION="__REGION__"
BUCKET="__BUCKET__"
OUTPUT_PREFIX="__OUTPUT_PREFIX__"
DESCRIPTOR_KEY="__DESCRIPTOR_KEY__"
DESCRIPTOR_SHA256="__DESCRIPTOR_SHA256__"
IMAGE="__IMAGE__"
WATCHDOG_S="__WATCHDOG_S__"

LOG=/var/log/medzen-b4-stage.log
OUT=/opt/medzen-stage/out
CACHE=/opt/medzen-stage/cache
INPUT=/opt/medzen-stage/input
mkdir -p "$OUT" "$CACHE" "$INPUT"
exec > >(tee -a "$LOG") 2>&1

finish() {
  rc=$?
  trap - EXIT
  printf '%s\n' "$rc" >/run/medzen-stage-exit
  aws s3api put-object --bucket "$BUCKET" \
    --key "${OUTPUT_PREFIX}live.log" --body "fileb://$LOG" \
    --if-none-match '*' --server-side-encryption aws:kms \
    --region "$REGION" >/dev/null || true
  aws s3api put-object --bucket "$BUCKET" \
    --key "${OUTPUT_PREFIX}container-exit-code" \
    --body fileb:///run/medzen-stage-exit --if-none-match '*' \
    --server-side-encryption aws:kms \
    --region "$REGION" >/dev/null || true
  if [ -s "$OUT/container-result.json" ]; then
    aws s3api put-object --bucket "$BUCKET" \
      --key "${OUTPUT_PREFIX}container-result.json" \
      --body "fileb://$OUT/container-result.json" --if-none-match '*' \
      --server-side-encryption aws:kms \
      --content-type application/json \
      --region "$REGION" >/dev/null || true
  fi
  sync
  shutdown -h now || poweroff || true
  exit "$rc"
}
trap finish EXIT

(
  sleep "$WATCHDOG_S"
  echo "WATCHDOG: stage exceeded ${WATCHDOG_S}s; stopping the container"
  docker stop --time 30 medzen-b4-stage || true
  shutdown -h now || poweroff || true
) &
WATCHDOG_PID=$!

aws s3 cp "s3://$BUCKET/$DESCRIPTOR_KEY" "$INPUT/descriptor.json" \
  --region "$REGION" --only-show-errors
got_descriptor="$(sha256sum "$INPUT/descriptor.json" | awk '{print $1}')"
test "$got_descriptor" = "$DESCRIPTOR_SHA256" || {
  echo "REFUSING: descriptor readback hash mismatch"
  exit 41
}

account="${IMAGE%%.*}"
aws ecr get-login-password --region "$REGION" |
  docker login --username AWS --password-stdin \
    "${account}.dkr.ecr.${REGION}.amazonaws.com"
docker pull "$IMAGE"
pulled="$(docker inspect --format '{{join .RepoDigests "\n"}}' "$IMAGE")"
printf '%s\n' "$pulled" | grep -F -- "$IMAGE" >/dev/null || {
  echo "REFUSING: Docker did not report the authorised digest"
  exit 42
}

nvidia-smi
docker run --rm --name medzen-b4-stage --gpus all \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=12g \
  -v "$INPUT:/input:ro" \
  -v "$OUT:/out:rw" \
  -v "$CACHE:/cache:rw" \
  -e AWS_REGION="$REGION" \
  -e AWS_DEFAULT_REGION="$REGION" \
  -e MEDZEN_BUCKET="$BUCKET" \
  -e MEDZEN_MODEL_DIR=/cache/models \
  -e MEDZEN_EVAL_CACHE=/cache/eval \
  -e MEDZEN_TRAIN_CACHE=/cache/train/audio \
  -e MEDZEN_STAGE_WORK=/cache/stage \
  -e HF_HOME=/cache/huggingface \
  -e XDG_CACHE_HOME=/cache/xdg \
  -e MEDZEN_IMAGE_DIGEST="__IMAGE_DIGEST__" \
  -e MEDZEN_CODE_GIT_SHA="__GIT_SHA__" \
  -e MEDZEN_CODE_TAR_SHA256="__BUNDLE_TAR_SHA256__" \
  --entrypoint python \
  "$IMAGE" -m pipeline.stage_runner \
    --descriptor /input/descriptor.json \
    --out /out/container-result.json

kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true
