#!/bin/bash
# EC2 user-data to run the trainer FROM THE ECR IMAGE, pinned by digest.
#
#   IMAGE_DIGEST=sha256:<64 hex> TRAIN_ARGS="--max-steps 3 ..." WATCHDOG_SECONDS=2700 \
#     envsubst 'DOLLAR{IMAGE_DIGEST} DOLLAR{TRAIN_ARGS} DOLLAR{WATCHDOG_SECONDS}' \
#       < this > /tmp/ud.sh      # write DOLLAR as $ -- spelled out so envsubst
#                                # does not substitute inside this comment
#   bash -n /tmp/ud.sh && aws ec2 run-instances --user-data file:///tmp/ud.sh ...
#
# The envsubst ARGUMENT LIST IS MANDATORY; bare envsubst would erase $RUN_ID,
# $S3, $SHIPPER and $TRAIN_RC and still produce valid bash.
#
# Pinned by DIGEST, never by tag. The repository has immutable tags, so a tag is
# already stable, but a digest is the artifact itself and is what survives a
# repository being re-tagged or a mirror being introduced.
#
# LAUNCH REQUIREMENT: the instance must be started with
#   --metadata-options HttpPutResponseHopLimit=2
# A container on the default bridge network is one hop further from IMDS than
# the host, and EC2's default limit of 1 silently denies it credentials. The
# symptom is an AccessDenied inside the container with correct IAM, which is
# indistinguishable from a policy problem until you know to look.
set -o pipefail
exec > >(tee /var/log/medzen-container.log) 2>&1
set -x

RUN_ID="preflight-$(date +%s)"
S3="s3://medzen-speech/candidates/preflight/$RUN_ID"
REGISTRY=558069890522.dkr.ecr.eu-central-1.amazonaws.com
REPO="$REGISTRY/medzen-trainer"
# IMAGE_DIGEST is the RENDER-time placeholder and must appear exactly once.
# DIGEST is the runtime variable. envsubst does not substitute the ${VAR:-}
# form, so a validation written against ${IMAGE_DIGEST:-} survives rendering
# and then evaluates EMPTY on the instance -- which is exactly how the first
# attempt failed at exit 40 with a correctly-built IMAGE.
DIGEST="${IMAGE_DIGEST}"
IMAGE="$REPO@$DIGEST"

# Same split, same reason. An empty ARGS would run the trainer with its
# defaults -- 600 steps, batch 2, grad-accum 8 -- which is FULL TRAINING, not a
# preflight. That must never happen by omission.
ARGS="${TRAIN_ARGS}"
export AWS_DEFAULT_REGION=eu-central-1 AWS_REGION=eu-central-1
unset AWS_PROFILE

WATCHDOG="${WATCHDOG_SECONDS}"
: "${WATCHDOG:=2700}"

( while true; do
    aws s3 cp /var/log/medzen-container.log "$S3/live.log" >/dev/null 2>&1 || true
    sleep 20
  done ) &
SHIPPER=$!

( sleep "$WATCHDOG"
  echo "WATCHDOG: hard timeout after ${WATCHDOG}s" >> /var/log/medzen-container.log
  aws s3 cp /var/log/medzen-container.log "$S3/watchdog.log" || true
  shutdown -h now ) &

finish() {
  local rc=$?
  kill $SHIPPER 2>/dev/null || true
  echo "=== EXIT rc=$rc ==="
  echo "$rc" > /tmp/exit_code
  aws s3 cp /var/log/medzen-container.log "$S3/preflight.log" || true
  aws s3 cp /tmp/exit_code "$S3/exit_code" || true
  [ -f /tmp/result.json ] && aws s3 cp /tmp/result.json "$S3/result.json" || true
  [ -f /opt/medzen-out/asr-lora/run.json ] && \
    aws s3 cp /opt/medzen-out/asr-lora/run.json "$S3/run.json" || true
  shutdown -h now
}
trap finish EXIT

# Exactly sha256: plus 64 lowercase hex. A malformed digest would either fail
# the pull in a confusing way or, worse, be recorded as the artifact identity of
# whatever did get pulled.
case "$DIGEST" in
  sha256:*) ;;
  *) echo "FATAL: digest must start with sha256:; got '$DIGEST'"; exit 40 ;;
esac
DIGEST_HEX="${DIGEST#sha256:}"
[ ${#DIGEST_HEX} -eq 64 ] || {
  echo "FATAL: digest hex must be 64 chars, got ${#DIGEST_HEX}"; exit 40; }
case "$DIGEST_HEX" in
  *[!0-9a-f]*) echo "FATAL: digest not lowercase hex: '$DIGEST'"; exit 40 ;;
esac
echo "image $IMAGE"

[ -n "$ARGS" ] || {
  echo "FATAL: TRAIN_ARGS rendered empty. The trainer would fall back to its"
  echo "  defaults (--max-steps 600), which is full training, not a preflight."
  exit 43; }
echo "train args: $ARGS"

aws sts get-caller-identity || { echo "FATAL: no instance credentials"; exit 10; }

echo "kms-roundtrip-$(date +%s)" > /tmp/kms_probe.txt
aws s3 cp /tmp/kms_probe.txt "$S3/kms_probe.txt" || { echo "FATAL: SSE-KMS write denied"; exit 20; }
aws s3 cp "$S3/kms_probe.txt" /tmp/kms_probe_back.txt || { echo "FATAL: SSE-KMS read denied"; exit 21; }
cmp /tmp/kms_probe.txt /tmp/kms_probe_back.txt || { echo "FATAL: KMS round trip corrupted"; exit 22; }
echo "KMS ROUND TRIP OK"

if out=$(aws s3 cp /tmp/kms_probe.txt s3://medzen-speech/eval/_probe_should_fail.txt 2>&1); then
  echo "FATAL: eval write SUCCEEDED - A3 guardrail breached"; exit 25
fi
case "$out" in
  *AccessDenied*) echo "EVAL DENY INTACT" ;;
  *) echo "FATAL: eval write failed for the WRONG reason: $out"; exit 26 ;;
esac

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | tee /tmp/gpu.txt \
  || { echo "FATAL: nvidia-smi"; exit 14; }

command -v docker >/dev/null || { echo "FATAL: no docker"; exit 11; }
systemctl start docker 2>/dev/null || true
docker version || { echo "FATAL: docker daemon unavailable"; exit 12; }

aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin "$REGISTRY" \
  || { echo "FATAL: ecr login"; exit 16; }
docker pull "$IMAGE" || { echo "FATAL: docker pull"; exit 41; }

# Confirm the pulled image really is the requested digest. A pull by digest
# should guarantee this, but the whole point of pinning is not taking that on
# trust when the artifact trains production models.
GOT=$(docker image inspect "$IMAGE" --format '{{index .RepoDigests 0}}' | sed 's/.*@//')
echo "pulled digest $GOT"
[ "$GOT" = "$DIGEST" ] || { echo "FATAL: digest mismatch: $GOT != $DIGEST"; exit 42; }
echo "DIGEST VERIFIED"

# Host-side directories so artifacts and the base-model cache survive the
# container: /opt/medzen-cache keeps the 3 GB checkpoint across restarts on
# this instance, which is the point of caching it at all.
mkdir -p /opt/medzen-out /opt/medzen-cache

# --gpus all needs nvidia-container-toolkit, which the DLAMI ships.
docker run --rm --gpus all \
  -e AWS_REGION=eu-central-1 -e AWS_DEFAULT_REGION=eu-central-1 \
  -e MEDZEN_BUCKET=medzen-speech \
  -e MEDZEN_MODEL_DIR=/opt/medzen-base \
  -v /opt/medzen-out:/opt/medzen/artifacts \
  -v /opt/medzen-cache:/opt/medzen-base \
  "$IMAGE" $ARGS
TRAIN_RC=$?
echo "TRAIN_RC=$TRAIN_RC"

python3 /dev/stdin "$TRAIN_RC" "$DIGEST" <<'WRITE_RESULT' > /tmp/result.json
import json, pathlib, sys
rc, digest = int(sys.argv[1]), sys.argv[2]
rj = pathlib.Path("/opt/medzen-out/asr-lora/run.json")
run = json.loads(rj.read_text()) if rj.exists() else {}
gpu = pathlib.Path("/tmp/gpu.txt")
print(json.dumps({
    "train_exit_code": rc,
    "image_digest": digest,
    "ran_in_container": True,
    "nvidia_smi": gpu.read_text().strip() if gpu.exists() else None,
    "torch_version": run.get("torch_version"),
    "cuda_version": run.get("cuda_version"),
    "device_used": run.get("device_used"),
    "gpu_name": run.get("gpu_name"),
    "gpu_peak_mb": run.get("gpu_peak_mb"),
    "train_loss": run.get("train_loss"),
    "loss_is_finite": run.get("loss_is_finite"),
    "steps": run.get("steps"),
    "mlflow_run_id": run.get("mlflow_run_id"),
    "dataset_fingerprint": run.get("dataset_fingerprint"),
    "base_revision": (run.get("params") or {}).get("base_revision"),
    "base_source": run.get("base_source"),
    "base_cache_uri": run.get("base_cache_uri"),
    "base_manifest_sha256": run.get("base_manifest_sha256"),
}, indent=2))
WRITE_RESULT
cat /tmp/result.json
exit $TRAIN_RC
