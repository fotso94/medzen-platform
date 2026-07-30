#!/bin/bash
# EC2 user-data for the image builder. TRUSTED CODE: everything it executes
# from S3 is verified here first.
#
#   GIT_SHA=<40 hex> TAR_SHA256=<64 hex> SCAN_MAX_HIGH=0 WATCHDOG=3600 \
#     envsubst '${GIT_SHA} ${TAR_SHA256} ${SCAN_MAX_CRITICAL} ${SCAN_MAX_HIGH} ${WATCHDOG}' \
#       < this > /tmp/ud.sh
#   bash -n /tmp/ud.sh && aws ec2 run-instances --user-data file:///tmp/ud.sh ...
#
# WHY THIS FILE EXISTS SEPARATELY FROM build_image.sh
#
# The first version of this wrapper extracted the bundle and executed
# build_image.sh from it, and that script then verified the bundle it had itself
# come from. That is circular: by the time the check runs, unverified code is
# already executing, and code that verifies itself proves nothing.
#
# So the root of trust is TAR_SHA256, substituted into this file at launch time
# from the machine that published the bundle. It does not come from S3.
# BUNDLE.json does come from S3, and is only used for per-file detail AFTER the
# archive as a whole has matched a hash that S3 could not have influenced.
#
# Extraction is done by python tarfile with filter="data", not `tar xzf`: it
# rejects absolute paths, parent-directory traversal, symlinks pointing outside
# the tree and device files. A malicious archive must not be able to write
# outside the extraction directory before anything has been checked.
#
# Every failure terminates the instance immediately. A builder that limps on
# after a failed download is how a half-built image reaches a registry.
set -o pipefail
exec > >(tee /var/log/medzen-boot.log) 2>&1
set -x

export AWS_DEFAULT_REGION=eu-central-1 AWS_REGION=eu-central-1
unset AWS_PROFILE

GIT_SHA="${GIT_SHA}"
TAR_SHA256="${TAR_SHA256}"
BOOT_ID="boot-$(date +%s)"
BOOT_S3="s3://medzen-speech/candidates/build/$BOOT_ID"

# Ship the boot log ALWAYS, not only on failure. The archive-hash check is the
# security control this whole wrapper exists for, so a successful run that
# leaves its evidence on a terminated instance proves nothing after the fact.
# This is the same lesson as the preflights that died with nothing uploaded.
ship_boot_log() {
  aws s3 cp /var/log/medzen-boot.log "$BOOT_S3/boot.log" 2>/dev/null || true
}
( while true; do ship_boot_log; sleep 20; done ) &
BOOT_SHIPPER=$!

die() {
  echo "FATAL: $*"
  kill $BOOT_SHIPPER 2>/dev/null || true
  ship_boot_log
  aws s3 cp /var/log/medzen-boot.log "$BOOT_S3/boot-failure.log" 2>/dev/null || true
  shutdown -h now
  exit 1
}

[ ${#GIT_SHA} -eq 40 ] || die "GIT_SHA must be 40 chars, got '${GIT_SHA}'"
[ ${#TAR_SHA256} -eq 64 ] || die "TAR_SHA256 must be 64 chars, got '${TAR_SHA256}'"
case "$GIT_SHA" in *[!0-9a-f]*) die "GIT_SHA not lowercase hex" ;; esac
case "$TAR_SHA256" in *[!0-9a-f]*) die "TAR_SHA256 not lowercase hex" ;; esac

B="s3://medzen-speech/candidates/bootstrap/$GIT_SHA"
rm -rf /opt/boot || die "cannot clear /opt/boot"
mkdir -p /opt/boot/src || die "cannot create /opt/boot/src"
cd /opt/boot || die "cannot cd /opt/boot"

aws s3 cp "$B/medzen_code.tgz" . || die "bundle download from $B"
aws s3 cp "$B/BUNDLE.json" . || die "BUNDLE.json download from $B"

# Root of trust: the archive must match the hash embedded in this user-data.
ACTUAL=$(sha256sum medzen_code.tgz | cut -d' ' -f1) || die "cannot hash the archive"
if [ "$ACTUAL" != "$TAR_SHA256" ]; then
  die "archive sha256 mismatch: got $ACTUAL, expected $TAR_SHA256"
fi
echo "ARCHIVE HASH VERIFIED against user-data ($ACTUAL)"

# Safe extraction AND full per-file verification, before anything is executed.
python3 /dev/stdin "$GIT_SHA" <<'VERIFY_AND_EXTRACT' || die "bundle verification"
import hashlib, json, pathlib, sys, tarfile

want = sys.argv[1]
boot = pathlib.Path("/opt/boot")
root = boot / "src"

man = json.loads((boot / "BUNDLE.json").read_text())
if man.get("git_sha") != want:
    print(f"MISMATCH: BUNDLE.json says {man.get('git_sha')}, launched for {want}")
    sys.exit(1)

# filter="data" rejects absolute paths, ../ traversal, links leaving the tree
# and special files. Never extract an unverified archive without it.
with tarfile.open(boot / "medzen_code.tgz") as t:
    t.extractall(root, filter="data")

on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
declared = set(man["files"])
missing, extra = sorted(declared - on_disk), sorted(on_disk - declared)
if missing or extra:
    print(f"FILE SET MISMATCH: {len(missing)} missing, {len(extra)} unexpected")
    for m in missing[:10]:
        print("  missing:", m)
    for e in extra[:10]:
        print("  unexpected:", e)
    sys.exit(1)

bad = []
for rel, meta in sorted(man["files"].items()):
    data = (root / rel).read_bytes()
    if len(data) != meta["bytes"]:
        bad.append(f"{rel}: {len(data)} bytes != {meta['bytes']}")
    elif hashlib.sha256(data).hexdigest() != meta["sha256"]:
        bad.append(f"{rel}: sha256 mismatch")
if bad:
    print(f"{len(bad)} file(s) failed verification:")
    for b in bad[:10]:
        print("  " + b)
    sys.exit(1)

print(f"BUNDLE VERIFIED: git_sha {want}, {len(declared)} files, "
      f"complete set, sizes and sha256 all match")
VERIFY_AND_EXTRACT

[ -f /opt/boot/src/pipeline/build_image.sh ] || die "build_image.sh absent from the verified bundle"

# Record the verification evidence in S3 before handing control to bundle code,
# so it exists even if the build itself never finishes.
echo "BOOTSTRAP EVIDENCE: git_sha=$GIT_SHA archive_sha256=$ACTUAL boot_id=$BOOT_ID"
kill $BOOT_SHIPPER 2>/dev/null || true
ship_boot_log
echo "executing verified build_image.sh"

GIT_SHA="$GIT_SHA" \
WATCHDOG="${WATCHDOG:-3600}" \
SCAN_MAX_CRITICAL="${SCAN_MAX_CRITICAL:-0}" \
SCAN_MAX_HIGH="${SCAN_MAX_HIGH:-0}" \
BUNDLE_DIR=/opt/boot/src \
  bash /opt/boot/src/pipeline/build_image.sh
RC=$?
echo "build_image.sh exited $RC"
# build_image.sh shuts the instance down through its own EXIT trap; this is the
# path taken only if it somehow returns without doing so.
shutdown -h now
exit $RC
