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
# Immutable tags mean a tag can never be moved, so the git SHA is a safe tag.
# The digest is still what deployments pin: a tag is a human label, a digest is
# the artifact.
set -o pipefail
exec > >(tee /var/log/medzen-build.log) 2>&1
set -x

RUN_ID="build-$(date +%s)"
S3="s3://medzen-speech/candidates/build/$RUN_ID"
REPO="558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-trainer"
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

aws sts get-caller-identity || { echo "FATAL: no instance credentials"; exit 10; }

# SSE-KMS round trip before doing any work, for the same reason the trainer does
# it: S3 grants are inert without KMS grants and the failure names KMS, not S3.
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

mkdir -p /opt/medzen && cd /opt/medzen
aws s3 cp s3://medzen-speech/candidates/bootstrap/medzen_code.tgz . \
  || { echo "FATAL: code fetch"; exit 13; }
tar xzf medzen_code.tgz || { echo "FATAL: untar"; exit 14; }

TAG="${GIT_SHA:-untagged}"
echo "building $REPO:$TAG (native $(uname -m))"
docker build -f pipeline/Dockerfile.trainer -t "$REPO:$TAG" . \
  || { echo "FATAL: docker build"; exit 15; }

docker image inspect "$REPO:$TAG" --format '{{.Size}}' | \
  awk '{printf "image size: %.2f GB\n", $1/1e9}'

aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin "${REPO%%/*}" \
  || { echo "FATAL: ecr login"; exit 16; }

docker push "$REPO:$TAG" || { echo "FATAL: docker push"; exit 17; }

# The digest is the artifact identity; read it back from ECR rather than trusting
# the local one, so what is recorded is what the registry actually holds.
DIGEST=$(aws ecr describe-images --repository-name medzen-trainer \
          --image-ids imageTag="$TAG" \
          --query 'imageDetails[0].imageDigest' --output text) \
  || { echo "FATAL: digest lookup"; exit 18; }
echo "DIGEST $DIGEST"

# scan-on-push is enabled on the repository; wait for it to finish and record
# the findings. A build that cannot be scanned must not be reported as clean.
for i in $(seq 1 40); do
  STATUS=$(aws ecr describe-image-scan-findings --repository-name medzen-trainer \
            --image-id imageDigest="$DIGEST" \
            --query 'imageScanStatus.status' --output text 2>/dev/null)
  echo "scan status: $STATUS"
  case "$STATUS" in
    COMPLETE) break ;;
    FAILED)   echo "FATAL: image scan FAILED"; exit 19 ;;
  esac
  sleep 15
done
aws ecr describe-image-scan-findings --repository-name medzen-trainer \
  --image-id imageDigest="$DIGEST" > /tmp/scan.json 2>&1 || true
python3 - <<'PY' >> /var/log/medzen-build.log 2>&1 || true
import json
d = json.load(open("/tmp/scan.json"))
c = (d.get("imageScanFindings", {}).get("findingSeverityCounts")
     or d.get("imageScanFindings", {}).get("enhancedFindingSeverityCounts") or {})
print("SCAN SEVERITY COUNTS:", json.dumps(c))
PY

python3 - "$TAG" "$DIGEST" <<'PY' > /tmp/image.json
import json, subprocess, sys
tag, digest = sys.argv[1], sys.argv[2]
size = subprocess.run(["docker","image","inspect",
                       f"558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-trainer:{tag}",
                       "--format","{{.Size}}"], capture_output=True, text=True).stdout.strip()
scan = {}
try:
    d = json.load(open("/tmp/scan.json"))
    fi = d.get("imageScanFindings", {})
    scan = {"status": d.get("imageScanStatus", {}).get("status"),
            "severity_counts": fi.get("findingSeverityCounts")
                               or fi.get("enhancedFindingSeverityCounts") or {}}
except Exception as e:
    scan = {"error": f"{type(e).__name__}: {e}"}
print(json.dumps({"repository": "medzen-trainer", "tag": tag, "digest": digest,
                  "image_bytes": int(size) if size.isdigit() else None,
                  "scan": scan,
                  "pin": f"558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-trainer@{digest}"},
                 indent=2))
PY
cat /tmp/image.json
echo "BUILD COMPLETE"
