#!/bin/bash
# EC2 user-data for a trainer host. Version-controlled: every line here is a
# lesson from B4 preflight attempts 1-5, and none of it should be retyped.
#
#   TRAIN_ARGS="--max-steps 3 ..." WATCHDOG_SECONDS=2700 GIT_SHA=<40 hex> \
#   TAR_SHA256=<64 hex> \
#     envsubst '${TRAIN_ARGS} ${WATCHDOG_SECONDS} ${GIT_SHA} ${TAR_SHA256}' < this > /tmp/ud.sh
#   bash -n /tmp/ud.sh && aws ec2 run-instances --user-data file:///tmp/ud.sh ...
#
# The envsubst ARGUMENT LIST IS MANDATORY. Bare `envsubst` substitutes every
# variable it finds, which would erase $RUN_ID, $S3, $SHIPPER and $TRAIN_RC --
# producing a script that uploads to the wrong prefix and reports the wrong
# exit status, with no syntax error to reveal it. tests/test_trainer_userdata.py
# renders this file and asserts those survive.
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

# Parameterised: a 3-step preflight and a multi-hour full training run need
# very different ceilings, and a watchdog shorter than the job silently
# truncates it. Defaults to 45 min.
#
# WATCHDOG_SECONDS is the RENDER-time placeholder and appears exactly once;
# WATCHDOG is the runtime variable and is never substituted. Reusing one name
# for both would mean an unrendered launch produced `sleep ""` -- which fails
# instantly and shuts the box down mid-training. envsubst also ignores the
# ${VAR:-default} form, so the fallback has to be a separate bash line.
WATCHDOG="${WATCHDOG_SECONDS}"
: "${WATCHDOG:=2700}"
( sleep "$WATCHDOG"
  echo "WATCHDOG: hard timeout after ${WATCHDOG}s" >> /var/log/medzen-trainer.log
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
  [ -f /opt/medzen/src/artifacts/asr-lora/run.json ] && \
    aws s3 cp /opt/medzen/src/artifacts/asr-lora/run.json "$S3/run.json" || true
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

# Per-commit bundle path, and the bundle is verified before it is trusted --
# the same guarantee the image build gets. A shared "latest" key would let this
# box run different code than was reviewed.
# This launcher IS the trusted code, so verification here precedes executing
# anything from the bundle. The root of trust is TAR_SHA256, substituted at
# launch time from the publishing machine: BUNDLE.json comes from S3 and is only
# consulted after the archive as a whole matches a hash S3 could not influence.
BUNDLE_SHA="${GIT_SHA}"
EXPECT_TAR="${TAR_SHA256}"
[ ${#BUNDLE_SHA} -eq 40 ] || { echo "FATAL: GIT_SHA must be 40 chars, got '${BUNDLE_SHA}'"; exit 31; }
[ ${#EXPECT_TAR} -eq 64 ] || { echo "FATAL: TAR_SHA256 must be 64 chars, got '${EXPECT_TAR}'"; exit 31; }
BUNDLE_BASE="s3://medzen-speech/candidates/bootstrap/$BUNDLE_SHA"
rm -rf /opt/medzen || { echo "FATAL: cannot clear /opt/medzen"; exit 12; }
mkdir -p /opt/medzen/src || { echo "FATAL: cannot create /opt/medzen/src"; exit 12; }
cd /opt/medzen || { echo "FATAL: cannot cd /opt/medzen"; exit 12; }
aws s3 cp "$BUNDLE_BASE/medzen_code.tgz" . || { echo "FATAL: code fetch"; exit 11; }
aws s3 cp "$BUNDLE_BASE/BUNDLE.json" . || { echo "FATAL: no BUNDLE.json"; exit 31; }
ACTUAL_TAR=$(sha256sum medzen_code.tgz | cut -d" " -f1) || { echo "FATAL: cannot hash archive"; exit 32; }
[ "$ACTUAL_TAR" = "$EXPECT_TAR" ] || {
  echo "FATAL: archive sha256 $ACTUAL_TAR != expected $EXPECT_TAR"; exit 32; }
echo "ARCHIVE HASH VERIFIED against user-data ($ACTUAL_TAR)"
python3 /dev/stdin "$BUNDLE_SHA" <<'VERIFY_BUNDLE' || { echo "FATAL: bundle verification"; exit 32; }
import hashlib, json, pathlib, sys, tarfile
boot = pathlib.Path("/opt/medzen"); root = boot / "src"
man = json.loads((boot / "BUNDLE.json").read_text())
if man["git_sha"] != sys.argv[1]:
    print(f"MISMATCH: bundle {man['git_sha']} != requested {sys.argv[1]}"); sys.exit(1)
# filter="data" rejects absolute paths, ../ traversal and links leaving the tree
with tarfile.open(boot / "medzen_code.tgz") as t:
    t.extractall(root, filter="data")
on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
declared = set(man["files"])
if on_disk != declared:
    print(f"FILE SET MISMATCH: missing {sorted(declared-on_disk)[:5]} "
          f"unexpected {sorted(on_disk-declared)[:5]}"); sys.exit(1)
bad = [rel for rel, m in man["files"].items()
       if hashlib.sha256((root/rel).read_bytes()).hexdigest() != m["sha256"]
       or (root/rel).stat().st_size != m["bytes"]]
if bad:
    print(f"{len(bad)} file(s) failed verification: {bad[:5]}"); sys.exit(1)
print(f"BUNDLE VERIFIED: {man['git_sha']}, {len(declared)} files")
VERIFY_BUNDLE
cd /opt/medzen/src

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | tee /tmp/gpu.txt \
  || { echo "FATAL: nvidia-smi"; exit 14; }

# Environment setup lives in version control, not here. Its exit codes name the
# failing stage (15 install, 16 pin, 17 imports, 18 pip check, 19 stale pkgs).
bash /opt/medzen/src/pipeline/bootstrap_trainer.sh /opt/medzen/src
BOOT_RC=$?
[ $BOOT_RC -eq 0 ] || { echo "FATAL: bootstrap failed rc=$BOOT_RC"; exit $BOOT_RC; }

# bootstrap builds a clean venv; use it, never the image's interpreter
source /opt/medzen/venv/bin/activate || { echo "FATAL: venv activate"; exit 19; }
which python; python -V

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
rj = pathlib.Path("/opt/medzen/src/artifacts/asr-lora/run.json")
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
