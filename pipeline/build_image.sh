#!/bin/bash
# Build, scan and push medzen-trainer. Runs on an x86 EC2 builder.
#
# Version-controlled for the same reason the trainer launcher is: this decides
# which bytes end up in the image that trains production models, and it must be
# reviewable rather than retyped into user-data.
#
# The repository already exists in Terraform state with IMMUTABLE tags,
# scan-on-push and KMS encryption. Nothing here creates or configures it.
#
# Required environment:
#   GIT_SHA   full 40-character commit SHA; the image tag and the claim about
#             what is inside the image. Validated, and checked against the
#             bundle's own BUNDLE.json before anything is built.
# Optional:
#   SCAN_MAX_CRITICAL  default 0   SCAN_MAX_HIGH  default 0
#   WATCHDOG           default 3600
set -o pipefail
exec > >(tee /var/log/medzen-build.log) 2>&1
set -x

RUN_ID="build-$(date +%s)"
S3="s3://medzen-speech/candidates/build/$RUN_ID"
ACCOUNT=558069890522
REGISTRY="$ACCOUNT.dkr.ecr.eu-central-1.amazonaws.com"
REPO="$REGISTRY/medzen-trainer"
export AWS_DEFAULT_REGION=eu-central-1 AWS_REGION=eu-central-1
unset AWS_PROFILE

( while true; do
    aws s3 cp /var/log/medzen-build.log "$S3/live.log" >/dev/null 2>&1 || true
    sleep 20
  done ) &
SHIPPER=$!

( sleep "${WATCHDOG:-3600}"
  echo "WATCHDOG: hard timeout" >> /var/log/medzen-build.log
  aws s3 cp /var/log/medzen-build.log "$S3/watchdog.log" || true
  shutdown -h now ) &

finish() {
  local rc=$?
  kill $SHIPPER 2>/dev/null || true
  echo "=== EXIT rc=$rc ==="
  echo "$rc" > /tmp/exit_code
  aws s3 cp /var/log/medzen-build.log "$S3/build.log" || true
  aws s3 cp /tmp/exit_code "$S3/exit_code" || true
  [ -f /tmp/image.json ] && aws s3 cp /tmp/image.json "$S3/image.json" || true
  [ -f /tmp/scan.json ] && aws s3 cp /tmp/scan.json "$S3/scan.json" || true
  shutdown -h now
}
trap finish EXIT

# --- GIT_SHA is mandatory and must be a full commit SHA ---------------------
# A short or malformed value would tag an image with junk, and because the
# repository has IMMUTABLE tags that mistake cannot be corrected in place. The
# full 40 characters also make the tag unambiguous forever.
if [ -z "${GIT_SHA:-}" ]; then
  echo "FATAL: GIT_SHA is required (full 40-char commit SHA)"; exit 30
fi
if [ ${#GIT_SHA} -ne 40 ]; then
  echo "FATAL: GIT_SHA must be exactly 40 chars, got ${#GIT_SHA}: $GIT_SHA"; exit 30
fi
case "$GIT_SHA" in
  *[!0-9a-f]*) echo "FATAL: GIT_SHA is not lowercase hex: $GIT_SHA"; exit 30 ;;
esac
echo "GIT_SHA $GIT_SHA accepted"

aws sts get-caller-identity || { echo "FATAL: no instance credentials"; exit 10; }

# SSE-KMS round trip before any work, for the same reason the trainer does it:
# S3 grants are inert without KMS grants and the denial names KMS, not S3.
echo "probe" > /tmp/probe.txt
aws s3 cp /tmp/probe.txt "$S3/probe.txt" || { echo "FATAL: SSE-KMS write denied"; exit 20; }
echo "KMS ROUND TRIP OK"

# The build plane must not be able to write the corpus or the eval sets.
if out=$(aws s3 cp /tmp/probe.txt s3://medzen-speech/eval/_build_should_fail.txt 2>&1); then
  echo "FATAL: eval write SUCCEEDED - guardrail breached"; exit 25
fi
case "$out" in
  *AccessDenied*) echo "EVAL DENY INTACT" ;;
  *) echo "FATAL: eval write failed for the WRONG reason: $out"; exit 26 ;;
esac

# Amazon Linux 2023 does not ship docker; install it rather than requiring a
# heavier AMI. The DLAMI has docker preinstalled but carries a GPU driver stack
# this builder has no use for.
if ! command -v docker >/dev/null; then
  echo "installing docker..."
  dnf install -y docker >/dev/null 2>&1 || yum install -y docker >/dev/null 2>&1 \
    || { echo "FATAL: cannot install docker"; exit 11; }
fi
systemctl enable --now docker 2>/dev/null || systemctl start docker 2>/dev/null || true
for i in $(seq 1 20); do docker version >/dev/null 2>&1 && break; sleep 3; done
docker version || { echo "FATAL: docker daemon unavailable"; exit 12; }

# --- source tree (MANDATORY: pre-verified by the trusted wrapper) ------------
# BUNDLE_DIR is required, and this script deliberately has NO fetch-and-extract
# path of its own. It is itself part of the tree being verified, so any check it
# performs on that tree proves nothing -- and keeping a second, unfiltered
# extraction path around would be an unsafe alternative route into the build
# that nobody audits. builder_userdata.sh is the only way in: it verifies the
# archive against a hash embedded in user-data, extracts with filter="data",
# and checks the complete file set before executing anything here.
if [ -z "${BUNDLE_DIR:-}" ]; then
  echo "FATAL: BUNDLE_DIR is required."
  echo "  Launch through pipeline/builder_userdata.sh, which verifies the bundle"
  echo "  before executing this script. There is no self-service fetch path."
  exit 13
fi
[ -d "$BUNDLE_DIR/pipeline" ] || { echo "FATAL: BUNDLE_DIR has no pipeline/: $BUNDLE_DIR"; exit 13; }
[ -f "$BUNDLE_DIR/pipeline/Dockerfile.trainer" ] || { echo "FATAL: no Dockerfile in $BUNDLE_DIR"; exit 13; }
SRC="$BUNDLE_DIR"
echo "using bundle pre-verified by the trusted wrapper: $SRC"

TAG="$GIT_SHA"
cd "$SRC" || { echo "FATAL: cannot cd $SRC"; exit 13; }
echo "building $REPO:$TAG (native $(uname -m))"
docker build -f pipeline/Dockerfile.trainer -t "$REPO:$TAG" . \
  || { echo "FATAL: docker build"; exit 15; }

docker image inspect "$REPO:$TAG" --format '{{.Size}}' | \
  awk '{printf "image size: %.2f GB\n", $1/1e9}'

aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin "$REGISTRY" \
  || { echo "FATAL: ecr login"; exit 16; }

docker push "$REPO:$TAG" || { echo "FATAL: docker push"; exit 17; }

# The digest is the artifact identity; read it back from ECR rather than
# trusting the local one, so what is recorded is what the registry holds.
DIGEST=$(aws ecr describe-images --repository-name medzen-trainer \
          --image-ids imageTag="$TAG" \
          --query 'imageDetails[0].imageDigest' --output text) \
  || { echo "FATAL: digest lookup"; exit 18; }
# An empty or malformed digest would be recorded as the artifact identity and
# pinned by deployments. "None" is what the CLI returns for a missing image.
case "$DIGEST" in
  sha256:*) ;;
  *) echo "FATAL: digest not sha256-prefixed: '$DIGEST'"; exit 35 ;;
esac
DIGEST_HEX="${DIGEST#sha256:}"
if [ ${#DIGEST_HEX} -ne 64 ]; then
  echo "FATAL: digest hex must be 64 chars, got ${#DIGEST_HEX}: '$DIGEST'"; exit 35
fi
case "$DIGEST_HEX" in
  *[!0-9a-f]*) echo "FATAL: digest not lowercase hex: '$DIGEST'"; exit 35 ;;
esac
echo "DIGEST $DIGEST"

# --- scan must reach COMPLETE ------------------------------------------------
# scan-on-push runs AFTER the push, so a threshold cannot prevent publication:
# it gates ADOPTION. An image whose scan never completes is not adoptable, so
# a timeout is a build failure, not a warning.
SCAN_DONE=no
for i in $(seq 1 60); do
  STATUS=$(aws ecr describe-image-scan-findings --repository-name medzen-trainer \
            --image-id imageDigest="$DIGEST" \
            --query 'imageScanStatus.status' --output text 2>/dev/null)
  echo "scan status: ${STATUS:-pending} (attempt $i/60)"
  case "$STATUS" in
    COMPLETE) SCAN_DONE=yes; break ;;
    FAILED)   echo "FATAL: image scan FAILED"; exit 19 ;;
  esac
  sleep 15
done
if [ "$SCAN_DONE" != yes ]; then
  echo "FATAL: scan did not reach COMPLETE within 15 minutes."
  echo "  An unscanned image must not be adopted; this build has failed."
  exit 33
fi

aws ecr describe-image-scan-findings --repository-name medzen-trainer \
  --image-id imageDigest="$DIGEST" > /tmp/scan.json 2>&1 \
  || { echo "FATAL: cannot read scan findings"; exit 33; }

# Thresholds. CRITICAL is zero and is not negotiable for an image that will
# process consented health-related speech. HIGH also defaults to zero so the
# first build REPORTS the real base-image count rather than having a number
# guessed for it; raising it is a deliberate decision made with findings in hand.
SCAN_MAX_CRITICAL="${SCAN_MAX_CRITICAL:-0}"
SCAN_MAX_HIGH="${SCAN_MAX_HIGH:-0}"
python3 /dev/stdin "$SCAN_MAX_CRITICAL" "$SCAN_MAX_HIGH" <<'CHECK_SCAN'
import json, sys
max_crit, max_high = int(sys.argv[1]), int(sys.argv[2])
d = json.load(open("/tmp/scan.json"))
fi = d.get("imageScanFindings", {})
counts = fi.get("findingSeverityCounts") or fi.get("enhancedFindingSeverityCounts") or {}
print("SCAN SEVERITY COUNTS:", json.dumps(counts))
crit, high = counts.get("CRITICAL", 0), counts.get("HIGH", 0)
over = []
if crit > max_crit:
    over.append(f"CRITICAL {crit} > {max_crit}")
if high > max_high:
    over.append(f"HIGH {high} > {max_high}")
if over:
    print("SCAN THRESHOLD EXCEEDED: " + "; ".join(over))
    for f in (fi.get("findings") or fi.get("enhancedFindings") or [])[:20]:
        sev = f.get("severity", "?")
        name = f.get("name") or f.get("title") or "?"
        print(f"  {sev:<9} {name}")
    sys.exit(1)
print("SCAN THRESHOLDS OK")
CHECK_SCAN
SCAN_RC=$?

python3 /dev/stdin "$TAG" "$DIGEST" "$REPO" "$SCAN_RC" <<'WRITE_IMAGE_JSON' > /tmp/image.json
import json, subprocess, sys
tag, digest, repo, scan_rc = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
size = subprocess.run(["docker", "image", "inspect", f"{repo}:{tag}", "--format", "{{.Size}}"],
                      capture_output=True, text=True).stdout.strip()
try:
    d = json.load(open("/tmp/scan.json"))
    fi = d.get("imageScanFindings", {})
    scan = {"status": d.get("imageScanStatus", {}).get("status"),
            "severity_counts": fi.get("findingSeverityCounts")
                               or fi.get("enhancedFindingSeverityCounts") or {}}
except Exception as e:
    scan = {"error": f"{type(e).__name__}: {e}"}
print(json.dumps({
    "repository": "medzen-trainer",
    "tag": tag,
    "git_sha": tag,
    "digest": digest,
    "image_bytes": int(size) if size.isdigit() else None,
    "scan": scan,
    "adoptable": scan_rc == 0,
    "pin": f"{repo}@{digest}",
}, indent=2))
WRITE_IMAGE_JSON
cat /tmp/image.json

if [ $SCAN_RC -ne 0 ]; then
  echo "BUILD FAILED: image pushed and scanned, but findings exceed the threshold."
  echo "  Do NOT pin this digest. Review scan.json, then either rebuild on a"
  echo "  patched base or raise SCAN_MAX_HIGH deliberately, with findings on record."
  exit 34
fi
echo "BUILD COMPLETE"
