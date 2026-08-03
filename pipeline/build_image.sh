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

RUN_ID="${BUILD_RUN_ID:-}"
case "$RUN_ID" in
  ""|*[!0-9A-Za-z._-]*)
    echo "FATAL: BUILD_RUN_ID is required and must be path-safe"; exit 30 ;;
esac
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
docker build --build-arg GIT_SHA="$GIT_SHA" \
  -f pipeline/Dockerfile.trainer -t "$REPO:$TAG" . \
  || { echo "FATAL: docker build"; exit 15; }

# The baked value must be the commit we verified the bundle against.
BAKED=$(docker run --rm --entrypoint printenv "$REPO:$TAG" MEDZEN_GIT_SHA 2>/dev/null)
[ "$BAKED" = "$GIT_SHA" ] || {
  echo "FATAL: image reports MEDZEN_GIT_SHA=$BAKED, expected $GIT_SHA"; exit 37; }
echo "IMAGE PROVENANCE: MEDZEN_GIT_SHA=$BAKED baked in"

# Execute the complete committed suite inside the image before it can be
# pushed.  The source bundle is mounted read-only, matching the GPU stage, and
# /tmp is the only writable test scratch space.  This also makes the build
# self-verifying when a developer's local Docker daemon is unavailable.
echo "running pinned-image suite against read-only verified source"
docker run --rm --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=4g \
  -v "$SRC:/work:ro" -w /work \
  --entrypoint pytest "$REPO:$TAG" tests/ -q -rsw \
  || { echo "FATAL: pinned-image test suite"; exit 38; }
echo "PINNED-IMAGE TEST SUITE PASSED"

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

# Thresholds stay at ZERO. Findings are excluded only by NAMED, JUSTIFIED,
# EXPIRING exception in platform/cve_allowlist.json -- never by raising a
# threshold, which would also hide the next finding nobody has seen yet.
#
# An exception stops applying the moment a fix exists: every justification rests
# on "there is nothing to upgrade to", so if fixed_in_version appears the waiver
# is void and the build fails until the image is rebuilt.
SCAN_MAX_CRITICAL="${SCAN_MAX_CRITICAL:-0}"
SCAN_MAX_HIGH="${SCAN_MAX_HIGH:-0}"
ALLOWLIST="$SRC/platform/cve_allowlist.json"
[ -f "$ALLOWLIST" ] || { echo "FATAL: no CVE allowlist at $ALLOWLIST"; exit 36; }
python3 /dev/stdin "$SCAN_MAX_CRITICAL" "$SCAN_MAX_HIGH" "$ALLOWLIST" <<'CHECK_SCAN'
import datetime, json, os, sys

max_crit, max_high, allow_path = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
today = datetime.date.today()

# Every severity is gated at zero unless explicitly raised. Gating only CRITICAL
# and HIGH let a brand-new unlisted MEDIUM through silently, which defeats the
# point of an allowlist: the allowlist is the record of what we have looked at.
# INFORMATIONAL and UNDEFINED are reported but not gated -- they are not findings
# that call for action, and gating them would train people to raise limits.
limits = {
    "CRITICAL": max_crit,
    "HIGH": max_high,
    "MEDIUM": int(os.environ.get("SCAN_MAX_MEDIUM", "0")),
    "LOW": int(os.environ.get("SCAN_MAX_LOW", "0")),
}
UNGATED = {"INFORMATIONAL", "UNDEFINED"}

allow = json.load(open(allow_path))
waivers = {e["cve"]: e for e in allow["entries"]}

d = json.load(open("/tmp/scan.json"))
fi = d.get("imageScanFindings", {})
findings = fi.get("findings") or fi.get("enhancedFindings") or []
print("SCAN SEVERITY COUNTS:",
      json.dumps(fi.get("findingSeverityCounts")
                 or fi.get("enhancedFindingSeverityCounts") or {}))
print(f"allowlist: {len(waivers)} entries, review_by {allow.get('review_by')}")
print(f"gate limits: {json.dumps(limits)} (ungated: {', '.join(sorted(UNGATED))})")

hard_fail = []

# The allowlist as a whole expires. Past review_by, every waiver in it is stale
# by definition and the file must be re-reviewed rather than kept running.
review_by = allow.get("review_by")
if not review_by:
    hard_fail.append("allowlist has no review_by date")
elif datetime.date.fromisoformat(review_by) < today:
    hard_fail.append(f"allowlist review_by {review_by} has passed; re-review required")

waived, unwaived, invalid = [], [], []
for f in findings:
    cve, sev = f.get("name"), f.get("severity")
    a = {x["key"]: x["value"] for x in f.get("attributes", [])}
    pkg, pkgver, fix = a.get("package_name"), a.get("package_version"), a.get("fixed_in_version")
    w = waivers.get(cve)
    if not w:
        unwaived.append((sev, cve, pkg, pkgver, fix))
        continue
    # A fix now exists: every justification rests on there being nothing to
    # upgrade to, so the waiver is void.
    if fix:
        invalid.append((sev, cve, pkg, f"FIX NOW AVAILABLE ({fix}) -- waiver void"))
    elif datetime.date.fromisoformat(w["expires"]) < today:
        invalid.append((sev, cve, pkg, f"waiver EXPIRED {w['expires']}"))
    elif w.get("package") != pkg:
        invalid.append((sev, cve, pkg, f"waiver names package {w.get('package')!r}"))
    elif w.get("package_version") != pkgver:
        # A waiver is written against a specific package build. A different
        # version is a different artifact and has not been reviewed.
        invalid.append((sev, cve, pkg,
                        f"waiver is for {pkg} {w.get('package_version')!r}, "
                        f"scan reports {pkgver!r}"))
    elif w.get("severity") != sev:
        # A CVE re-rated upward must not stay waived under a justification
        # written when it was less severe.
        invalid.append((sev, cve, pkg,
                        f"severity changed: waiver says {w.get('severity')}, "
                        f"scan says {sev}"))
    else:
        waived.append((sev, cve, pkg))

print(f"\nwaived by allowlist ({len(waived)}):")
for sev, cve, pkg in sorted(waived):
    w = waivers[cve]
    print(f"  {sev:<9} {cve:<18} {pkg} {w['package_version']}  "
          f"(reachable={str(w.get('reachable')).lower()}, expires {w['expires']})")

# Emitted so the image record can report RAW and UNWAIVED counts separately.
# A waived CVE is still in the image; a record showing zero because everything
# has a waiver would hide an exception the day it expires.
with open("/tmp/waived.json", "w") as fh:
    json.dump([{"cve": cve, "severity": sev, "package": pkg,
                "package_version": waivers[cve]["package_version"],
                "expires": waivers[cve]["expires"],
                "reachable": waivers[cve].get("reachable")}
               for sev, cve, pkg in sorted(waived)], fh)

# Entries that match nothing in this scan are stale and must be pruned. Leaving
# them is how an allowlist quietly becomes a list of things nobody checks.
matched = {c for _, c, _ in waived} | {c for _, c, _, _ in invalid}
stale = sorted(set(waivers) - matched)
if stale:
    hard_fail.append(f"{len(stale)} stale allowlist entr{'y' if len(stale)==1 else 'ies'} "
                     f"matching nothing in this scan: {', '.join(stale)} -- prune them")

if invalid:
    print(f"\nINVALID WAIVERS ({len(invalid)}) -- these do NOT count as waived:")
    for sev, cve, pkg, why in sorted(invalid):
        print(f"  {sev:<9} {cve:<18} {pkg}  {why}")

if unwaived:
    print(f"\nNOT WAIVED ({len(unwaived)}):")
    for sev, cve, pkg, pkgver, fix in sorted(unwaived):
        print(f"  {sev:<9} {cve:<18} {pkg} {pkgver}  fix={fix or 'none'}")

counts = {}
for sev, cve, pkg, pkgver, fix in unwaived:
    counts[sev] = counts.get(sev, 0) + 1

over = []
for sev in sorted(set(counts) | {i[0] for i in invalid}):
    if sev in UNGATED:
        continue
    limit = limits.get(sev, 0)
    n_unwaived = counts.get(sev, 0)
    n_invalid = sum(1 for i in invalid if i[0] == sev)
    if n_unwaived + n_invalid > limit:
        parts = []
        if n_unwaived:
            parts.append(f"{n_unwaived} unwaived")
        if n_invalid:
            parts.append(f"{n_invalid} with an invalid waiver")
        over.append(f"{sev}: {' + '.join(parts)} > limit {limit}")

if over or hard_fail:
    print("\nSCAN GATE FAILED")
    for o in over:
        print(f"  {o}")
    for h in hard_fail:
        print(f"  {h}")
    sys.exit(1)
print(f"\nSCAN THRESHOLDS OK: {len(waived)} finding(s) waived by named exception, "
      f"0 unwaived or invalid above threshold")
CHECK_SCAN
SCAN_RC=$?

python3 /dev/stdin "$TAG" "$DIGEST" "$REPO" "$SCAN_RC" <<'WRITE_IMAGE_JSON' > /tmp/image.json
import json, subprocess, sys
tag, digest, repo, scan_rc = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
size = subprocess.run(["docker", "image", "inspect", f"{repo}:{tag}", "--format", "{{.Size}}"],
                      capture_output=True, text=True).stdout.strip()
def attr(f, k):
    return next((a["value"] for a in f.get("attributes", []) if a["key"] == k), None)

try:
    d = json.load(open("/tmp/scan.json"))
    fi = d.get("imageScanFindings", {})
    raw = fi.get("findingSeverityCounts") or \
        fi.get("enhancedFindingSeverityCounts") or {}
    findings = [{"cve": f["name"], "severity": f["severity"],
                 "package": attr(f, "package_name"),
                 "package_version": attr(f, "package_version")}
                for f in fi.get("findings", [])]
    try:
        waived = json.load(open("/tmp/waived.json"))
    except Exception:
        waived = []
    wset = {(w["cve"], w.get("package"), w.get("package_version"))
            for w in waived}
    unwaived = [f for f in findings
                if (f["cve"], f["package"], f["package_version"]) not in wset]
    unwaived_counts = {}
    for f in unwaived:
        unwaived_counts[f["severity"]] = unwaived_counts.get(f["severity"], 0) + 1
    scan = {
        "status": d.get("imageScanStatus", {}).get("status"),
        # RAW counts, exactly as the scanner reported them. These are NEVER
        # reduced to zero because a finding has a waiver -- a waived CVE is
        # still present in the image, and a record that hides it would let an
        # expiring exception pass unnoticed.
        "raw_severity_counts": raw,
        "raw_total_findings": len(findings),
        "waived_findings": len(waived),
        "waived_detail": waived,
        # What the gate actually requires to be empty.
        "unwaived_severity_counts": unwaived_counts,
        "unwaived_total": len(unwaived),
        "unwaived_detail": unwaived,
        "gate_requires": "zero unwaived findings at any gated severity",
        "note": ("waivers are exact CVE + package + package_version matches "
                 "with an unexpired review_by; raw counts above are the "
                 "scanner's, not the gate's"),
        # kept for backward compatibility with earlier records
        "severity_counts": raw,
    }
except Exception as e:
    scan = {"error": f"{type(e).__name__}: {e}"}

# ---- dependency provenance ------------------------------------------------
def sh(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None

import hashlib, pathlib as _pl
req = _pl.Path("/opt/boot/src/requirements.txt")
freeze = sh("docker", "run", "--rm", "--entrypoint", "python",
            f"{repo}:{tag}", "-m", "pip", "freeze")
vers = sh("docker", "run", "--rm", "--entrypoint", "python", f"{repo}:{tag}",
          "-c", ("import json,torch,transformers,peft;"
                 "print(json.dumps({'torch':torch.__version__,"
                 "'cuda_build':torch.version.cuda,"
                 "'transformers':transformers.__version__,"
                 "'peft':peft.__version__}))"))
base_digest = sh("bash", "-c",
                 "grep -oE 'python:3[^ ]*@sha256:[0-9a-f]{64}' "
                 "/opt/boot/src/pipeline/Dockerfile.trainer | head -1")
deps = {
    "requirements_sha256": (hashlib.sha256(req.read_bytes()).hexdigest()
                            if req.exists() else None),
    "base_image": base_digest,
    "runtime_versions": json.loads(vers) if vers else None,
    "installed_packages": (freeze.splitlines() if freeze else None),
    "installed_package_count": (len(freeze.splitlines()) if freeze else None),
    "inventory_source": "pip freeze inside the built image",
}
print(json.dumps({
    "repository": "medzen-trainer",
    "tag": tag,
    "git_sha": tag,
    "digest": digest,
    "image_bytes": int(size) if size.isdigit() else None,
    "scan": scan,
    "dependencies": deps,
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
