#!/bin/bash
# EC2 user-data for a trainer host. Version-controlled: every line here is a
# lesson from B4 preflight attempts 1-5, and none of it should be retyped.
#
#   TRAIN_ARGS="--max-steps 3 --save-steps 3 ..." envsubst < this > /tmp/ud.sh
#   aws ec2 run-instances --user-data file:///tmp/ud.sh ...
#
# Substituted before launch: ${TRAIN_ARGS}. Everything else is fixed.
#
# The four rules this file exists to enforce:
#   1. Ship the log from the first second. Attempts 1-3 died with nothing
#      uploaded, so the cause was invisible until the serial console was read.
#   2. Install the EXIT trap before anything that can fail, or an early exit
#      uploads nothing.
#   3. Prove the encrypted path works before spending GPU minutes: S3 grants
#      are inert without KMS grants on an SSE-KMS bucket.
#   4. Never pipe a command whose exit status matters into `tail`.
set -o pipefail
exec > >(tee /var/log/medzen-trainer.log) 2>&1
set -x

RUN_ID="preflight-$(date +%s)"
S3="s3://medzen-speech/candidates/preflight/$RUN_ID"
export AWS_DEFAULT_REGION=eu-central-1 AWS_REGION=eu-central-1
export MEDZEN_BUCKET=medzen-speech PYTHONUNBUFFERED=1 HF_HUB_DISABLE_XET=1
unset AWS_PROFILE                     # the instance role, never a stale profile

( while true; do
    aws s3 cp /var/log/medzen-trainer.log "$S3/live.log" >/dev/null 2>&1 || true
    sleep 20
  done ) &
SHIPPER=$!

( sleep 2700
  echo "WATCHDOG: hard timeout" >> /var/log/medzen-trainer.log
  aws s3 cp /var/log/medzen-trainer.log "$S3/watchdog.log" || true
  shutdown -h now ) &

finish() {
  local rc=$?
  kill $SHIPPER 2>/dev/null || true
  echo "=== EXIT rc=$rc ==="
  echo "$rc" > /tmp/exit_code
  aws s3 cp /var/log/medzen-trainer.log "$S3/preflight.log" || true
  aws s3 cp /tmp/exit_code "$S3/exit_code" || true
  [ -f /tmp/result.json ] && aws s3 cp /tmp/result.json "$S3/result.json" || true
  [ -f /opt/medzen/artifacts/asr-lora/run.json ] && \
    aws s3 cp /opt/medzen/artifacts/asr-lora/run.json "$S3/run.json" || true
  shutdown -h now
}
trap finish EXIT                      # rule 2: before anything that can fail

aws sts get-caller-identity || { echo "FATAL: no instance credentials"; exit 10; }

# rule 3 -- SSE-KMS round trip before anything expensive
echo "kms-roundtrip-$(date +%s)" > /tmp/kms_probe.txt
aws s3 cp /tmp/kms_probe.txt "$S3/kms_probe.txt" || { echo "FATAL: SSE-KMS write denied"; exit 20; }
aws s3 cp "$S3/kms_probe.txt" /tmp/kms_probe_back.txt || { echo "FATAL: SSE-KMS read denied"; exit 21; }
cmp /tmp/kms_probe.txt /tmp/kms_probe_back.txt || { echo "FATAL: KMS round trip corrupted"; exit 22; }
echo "KMS ROUND TRIP OK"

# The eval write-Deny is a structural guardrail; verify it on the real host and
# HARD FAIL either way it can be wrong. Command substitution, not a pipe to
# grep: under pipefail a pipeline reports aws's non-zero status even when grep
# matches, which made an earlier probe cry wolf about an intact guardrail.
if out=$(aws s3 cp /tmp/kms_probe.txt s3://medzen-speech/eval/_probe_should_fail.txt 2>&1); then
  echo "FATAL: eval write SUCCEEDED - A3 guardrail breached"; exit 25
fi
case "$out" in
  *AccessDenied*) echo "EVAL DENY INTACT" ;;
  *) echo "FATAL: eval write failed for the WRONG reason: $out"; exit 26 ;;
esac

mkdir -p /opt/medzen && cd /opt/medzen
aws s3 cp s3://medzen-speech/candidates/bootstrap/medzen_code.tgz . || { echo "FATAL: code fetch"; exit 11; }
tar xzf medzen_code.tgz || { echo "FATAL: untar"; exit 12; }

source /opt/pytorch/bin/activate 2>/dev/null || echo "note: no /opt/pytorch venv, using system python"
which python; python -V
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | tee /tmp/gpu.txt \
  || { echo "FATAL: nvidia-smi"; exit 14; }

# Environment setup lives in version control, not here. Its exit codes name the
# failing stage (15 install, 16 pin, 17 imports, 18 pip check, 19 stale pkgs).
bash /opt/medzen/pipeline/bootstrap_trainer.sh /opt/medzen
BOOT_RC=$?
[ $BOOT_RC -eq 0 ] || { echo "FATAL: bootstrap failed rc=$BOOT_RC"; exit $BOOT_RC; }

# rule 4: no pipe, so $? is the trainer's own status
python -m pipeline.train_asr ${TRAIN_ARGS}
TRAIN_RC=$?
echo "TRAIN_RC=$TRAIN_RC"

# result.json is assembled from the TRAINING process's own run.json, which holds
# gpu_peak_mb measured in the process where the allocator is observable. A new
# process would report zero.
python - "$TRAIN_RC" <<'PY' > /tmp/result.json
import json, sys, pathlib
rc = int(sys.argv[1])
rj = pathlib.Path("/opt/medzen/artifacts/asr-lora/run.json")
run = json.loads(rj.read_text()) if rj.exists() else {}
print(json.dumps({
    "train_exit_code": rc,
    "nvidia_smi": open("/tmp/gpu.txt").read().strip() if pathlib.Path("/tmp/gpu.txt").exists() else None,
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
}, indent=2))
PY
cat /tmp/result.json
exit $TRAIN_RC
