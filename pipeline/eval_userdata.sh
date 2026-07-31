#!/bin/bash
# EC2 user-data for the DIAGNOSTIC evaluation reproduction.
#
#   IMAGE_DIGEST=sha256:<64 hex> GIT_SHA=<40 hex> TAR_SHA256=<64 hex> \
#   ADAPTER_URI=s3://... ADAPTER_SHA256=<64 hex> WATCHDOG_SECONDS=1800 \
#     envsubst 'DOLLAR{IMAGE_DIGEST} DOLLAR{GIT_SHA} DOLLAR{TAR_SHA256} \
#               DOLLAR{ADAPTER_URI} DOLLAR{ADAPTER_SHA256} DOLLAR{WATCHDOG_SECONDS}' \
#       < this > /tmp/ud.sh     # write DOLLAR as $ -- spelled out so envsubst
#                               # does not substitute inside this comment
#   bash -n /tmp/ud.sh && aws ec2 run-instances --user-data file:///tmp/ud.sh ...
#
# The envsubst ARGUMENT LIST IS MANDATORY. Bare envsubst would erase $RUN_ID,
# $S3, $SHIPPER, $DIGEST and $RC and still produce valid bash.
#
# WHY THE IMAGE AND THE CODE ARE DIFFERENT ARTIFACTS
#
# The pinned image was built from commit 202b005 and does not contain
# scripts/evaluate_candidate.py -- it predates it. So the image supplies the
# verified DEPENDENCY SET and nothing else: the code comes from the published
# bundle at the approved commit, verified against a TAR_SHA256 that arrives in
# user-data and never from S3. Running the image's baked entrypoint would run
# the OLD TRAINER. --entrypoint is overridden explicitly for that reason.
#
# The bundle is mounted READ-ONLY. Caches and outputs get their own writable
# mounts so nothing can write back into the verified tree.
set -o pipefail
exec > >(tee /var/log/medzen-eval.log) 2>&1
set -x

REGISTRY=558069890522.dkr.ecr.eu-central-1.amazonaws.com
REPO="$REGISTRY/medzen-trainer"

# Render-time placeholders, each in the BARE ${VAR} form and each used exactly
# once. envsubst does not substitute ${VAR:-default}, ${VAR:offset:len} or any
# other expansion form -- a placeholder written that way survives rendering and
# then evaluates EMPTY on the instance. That is how an earlier launch failed at
# exit 40 with a correctly-built image, and why RUN_ID below is derived from
# the runtime copy CODE_SHA rather than from ${GIT_SHA:0:12}, which would have
# silently produced a run id with no commit in it.
DIGEST="${IMAGE_DIGEST}"
CODE_SHA="${GIT_SHA}"
CODE_TAR="${TAR_SHA256}"
ADAPTER="${ADAPTER_URI}"
ADAPTER_HASH="${ADAPTER_SHA256}"
IMAGE="$REPO@$DIGEST"

RUN_ID="eval-$(date +%s)-$(printf '%.12s' "$CODE_SHA")"
S3="s3://medzen-speech/candidates/evaluations/$RUN_ID"

export AWS_DEFAULT_REGION=eu-central-1 AWS_REGION=eu-central-1
unset AWS_PROFILE

WATCHDOG="${WATCHDOG_SECONDS}"
: "${WATCHDOG:=1800}"

( while true; do
    aws s3 cp /var/log/medzen-eval.log "$S3/live.log" >/dev/null 2>&1 || true
    sleep 20
  done ) &
SHIPPER=$!

( sleep "$WATCHDOG"
  echo "WATCHDOG: hard timeout after ${WATCHDOG}s" >> /var/log/medzen-eval.log
  aws s3 cp /var/log/medzen-eval.log "$S3/watchdog.log" || true
  shutdown -h now ) &

finish() {
  local rc=$?
  kill $SHIPPER 2>/dev/null || true
  echo "=== EXIT rc=$rc ==="
  echo "$rc" > /tmp/exit_code
  aws s3 cp /var/log/medzen-eval.log "$S3/eval.log" || true
  aws s3 cp /tmp/exit_code "$S3/exit_code" || true
  # The evaluation JSON, if it was produced. Nothing else leaves the box.
  if [ -d /opt/medzen-eval-out ]; then
    aws s3 cp /opt/medzen-eval-out "$S3/" --recursive \
      --exclude "*" --include "*.json" || true
  fi
  shutdown -h now
}
trap finish EXIT

die() { echo "FATAL: $*"; exit "${2:-1}"; }

# ---- validate every rendered value BEFORE acting on it ---------------------
case "$DIGEST" in
  sha256:*) ;;
  *) die "digest must start with sha256:; got '$DIGEST'" 40 ;;
esac
DIGEST_HEX="${DIGEST#sha256:}"
[ ${#DIGEST_HEX} -eq 64 ] || die "digest hex must be 64 chars, got ${#DIGEST_HEX}" 40
case "$DIGEST_HEX" in *[!0-9a-f]*) die "digest not lowercase hex" 40 ;; esac
# Length alone is not validation: a 64-character string of the wrong alphabet,
# or an uppercase digest, would pass a length check and then fail deep inside
# a comparison where the message is far less clear.
hexcheck() {                      # hexcheck NAME VALUE LEN EXITCODE
  [ ${#2} -eq "$3" ] || die "$1 must be $3 chars, got ${#2}" "$4"
  case "$2" in
    *[!0-9a-f]*) die "$1 must be lowercase hex, got '$2'" "$4" ;;
  esac
}
hexcheck GIT_SHA        "$CODE_SHA"     40 44
hexcheck TAR_SHA256     "$CODE_TAR"     64 45
hexcheck ADAPTER_SHA256 "$ADAPTER_HASH" 64 46
[ -n "$ADAPTER" ] || die "ADAPTER_URI is empty" 47
case "$ADAPTER" in
  s3://medzen-speech/candidates/*) ;;
  *) die "ADAPTER_URI must be under s3://medzen-speech/candidates/, got '$ADAPTER'" 48 ;;
esac
echo "image $IMAGE"
echo "code  $CODE_SHA tar $CODE_TAR"
echo "run   $RUN_ID -> $S3"

aws sts get-caller-identity || die "no instance credentials" 10

# ---- guardrails, exercised rather than assumed -----------------------------
echo "probe-$(date +%s)" > /tmp/kms_probe.txt
aws s3 cp /tmp/kms_probe.txt "$S3/kms_probe.txt" || die "SSE-KMS write denied" 20
aws s3 cp "$S3/kms_probe.txt" /tmp/kms_probe_back.txt || die "SSE-KMS read denied" 21
cmp /tmp/kms_probe.txt /tmp/kms_probe_back.txt || die "KMS round trip corrupted" 22
echo "KMS ROUND TRIP OK"

if out=$(aws s3 cp /tmp/kms_probe.txt s3://medzen-speech/eval/_probe_should_fail.txt 2>&1); then
  die "eval write SUCCEEDED - A3 guardrail breached" 25
fi
case "$out" in
  *AccessDenied*) echo "EVAL DENY INTACT" ;;
  *) die "eval write failed for the WRONG reason: $out" 26 ;;
esac

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader \
  | tee /tmp/gpu.txt || die "nvidia-smi" 14

# ---- code bundle: verified against a hash S3 did not supply ----------------
B="s3://medzen-speech/candidates/bootstrap/$CODE_SHA"
rm -rf /opt/evalsrc && mkdir -p /opt/evalsrc/src && cd /opt/evalsrc
aws s3 cp "$B/medzen_code.tgz" . || die "bundle download" 50
aws s3 cp "$B/BUNDLE.json" .     || die "bundle manifest download" 50
ACTUAL=$(sha256sum medzen_code.tgz | cut -d' ' -f1)
[ "$ACTUAL" = "$CODE_TAR" ] || die "ARCHIVE HASH MISMATCH: $ACTUAL != $CODE_TAR" 51
echo "ARCHIVE HASH VERIFIED against user-data ($CODE_TAR)"

# Extraction with filter="data": rejects absolute paths, parent traversal,
# symlinks pointing outside the tree and device files. A malicious archive must
# not write outside the extraction directory before anything has been checked.
python3 - "$CODE_SHA" <<'PY' || die "bundle verification" 52
import hashlib, json, sys, tarfile, pathlib
sha = sys.argv[1]
b = json.load(open("/opt/evalsrc/BUNDLE.json"))
if b.get("git_sha") != sha:
    raise SystemExit(f"BUNDLE git_sha {b.get('git_sha')} != {sha}")
with tarfile.open("/opt/evalsrc/medzen_code.tgz") as t:
    t.extractall("/opt/evalsrc/src", filter="data")
root = pathlib.Path("/opt/evalsrc/src")
bad = []
for rel, meta in b["files"].items():
    p = root / rel
    if not p.is_file():
        bad.append(f"missing {rel}"); continue
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != meta["sha256"]:
        bad.append(f"{rel} {got[:16]} != {meta['sha256'][:16]}")
if bad:
    raise SystemExit("BUNDLE MISMATCH: " + "; ".join(bad[:5]))
print(f"BUNDLE VERIFIED: {sha}, {len(b['files'])} files, hashes all match")
PY
[ -f /opt/evalsrc/src/scripts/evaluate_candidate.py ] \
  || die "verified bundle has no evaluator" 53

# ---- image: pulled by digest, and the pull is checked ----------------------
command -v docker >/dev/null || die "no docker" 11
systemctl start docker 2>/dev/null || true
docker version || die "docker daemon unavailable" 12
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin "$REGISTRY" || die "ecr login" 16
docker pull "$IMAGE" || die "docker pull" 41
GOT=$(docker image inspect "$IMAGE" --format '{{index .RepoDigests 0}}' | sed 's/.*@//')
echo "pulled digest $GOT"
[ "$GOT" = "$DIGEST" ] || die "digest mismatch: $GOT != $DIGEST" 42
echo "DIGEST VERIFIED"

# ---- run: verified code READ-ONLY, entrypoint overridden ------------------
# Without --entrypoint this would run the image's baked container_entrypoint.sh,
# which execs the 202b005 TRAINER. That is the opposite of this run's purpose.
mkdir -p /opt/medzen-eval-out /opt/medzen-eval-cache
docker run --rm --gpus all \
  -e AWS_REGION=eu-central-1 -e AWS_DEFAULT_REGION=eu-central-1 \
  -e MEDZEN_BUCKET=medzen-speech \
  -e MEDZEN_IMAGE_DIGEST="$DIGEST" \
  -e MEDZEN_CODE_GIT_SHA="$CODE_SHA" \
  -e MEDZEN_CODE_TAR_SHA256="$CODE_TAR" \
  -e PYTHONPATH=/opt/medzen \
  -e MEDZEN_EVAL_CACHE=/cache \
  -v /opt/evalsrc/src:/opt/medzen:ro \
  -v /opt/medzen-eval-out:/out \
  -v /opt/medzen-eval-cache:/cache \
  -w /opt/medzen \
  --entrypoint python \
  "$IMAGE" scripts/evaluate_candidate.py \
    --language pidgin --task tts --eval-version v1 \
    --lang-token en \
    --adapter "$ADAPTER" \
    --expect-adapter-sha256 "$ADAPTER_HASH" \
    --out /out/evaluation.json
RC=$?
echo "EVAL_RC=$RC"
exit $RC
