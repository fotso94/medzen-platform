# HISTORICAL / PROVENANCE-ONLY (Codex review #11): this executor ran
# once and is NOT safe for reuse as-is — unversioned S3 reads, mutable
# image tag, overwrite-capable uploads, no sealed_gate integration.
# Future sealed evaluators MUST run scripts/sealed_gate.py acquire first.
#!/bin/bash
set -x
exec > /var/log/ingest.log 2>&1
LOGDST=s3://medzen-speech/research/b5-training/av-ingest/host.log
sync_log() { while true; do aws s3 cp /var/log/ingest.log "$LOGDST" --quiet || true; sleep 120; done; }
sync_log &
# box-death forensics (first box died mid-extract, no marker)
trap 'echo "BOX_EXIT_TRAP rc=$? $(date -u +%FT%TZ)"; aws s3 cp /var/log/ingest.log "$LOGDST" --quiet || true' EXIT
# LESSON: verify instance-role creds from INSIDE before real work
for i in $(seq 1 60); do
  aws sts get-caller-identity >/dev/null 2>&1 && break
  sleep 10
done
aws sts get-caller-identity || { echo NO_CREDS_AFTER_10MIN; shutdown -h now; exit 1; }
dnf install -y python3.11 python3.11-pip zstd
python3.11 -m pip install --quiet "librosa==0.11.0" "soundfile==0.14.0" "openpyxl==3.1.5" boto3 pyyaml jsonschema numpy
mkdir -p /opt/ingest/tmp /opt/ingest/av && cd /opt/ingest
aws s3 cp s3://medzen-speech/research/b5-training/t6-eval-r2/inputs/repo-full-av.tar.gz . --quiet
test -s repo-full-av.tar.gz || { echo REPO_FETCH_FAILED; shutdown -h now; exit 1; }
tar -xzf repo-full-av.tar.gz

# ---- staged download: wait for each archive as the Mac upload lands it ----
# Upload runs at ~1.8 MiB/s from the owner's Mac; global deadline 40 h.
DEADLINE=$(( $(date +%s) + 144000 ))
for n in $(seq 1 42); do
  KEY="raw/_incoming/africanvoices/Batch_${n}.tar.zst"
  until aws s3api head-object --bucket medzen-speech --key "$KEY" >/dev/null 2>&1; do
    [ "$(date +%s)" -gt "$DEADLINE" ] && { echo "STAGING_DEADLINE_40H_AT_BATCH_${n}"; shutdown -h now; exit 1; }
    sleep 120
  done
  echo "STAGING Batch_${n} $(date -u +%FT%TZ) free=$(df -h / | tail -1 | awk '{print $4}')"
  aws s3 cp "s3://medzen-speech/${KEY}" "/opt/ingest/Batch_${n}.tar.zst" --quiet || { echo "DOWNLOAD_FAILED_${n}"; shutdown -h now; exit 1; }
  # archives are FLAT (metadata.csv at root) -> extract into a per-batch dir
  mkdir -p "/opt/ingest/av/Batch_${n}"
  zstd -dc "/opt/ingest/Batch_${n}.tar.zst" | tar -xf - -C "/opt/ingest/av/Batch_${n}" || { echo "EXTRACT_FAILED_${n}"; shutdown -h now; exit 1; }
  test -s "/opt/ingest/av/Batch_${n}/metadata.csv" -o -s "/opt/ingest/av/Batch_${n}/metadata.xlsx" || { echo "NO_METADATA_${n}"; shutdown -h now; exit 1; }
  rm -f "/opt/ingest/Batch_${n}.tar.zst"
done
echo "ALL_42_STAGED $(date -u +%FT%TZ) free=$(df -h / | tail -1 | awk '{print $4}')"

# ---- ingest (single pass over all 42 extracted batches) ----
# LESSONS: TMPDIR on the data volume; MEDZEN_PROFILE= selects the role chain;
# drop_both = the full-CV lesson (never train on contested labels; counts land
# in the log for the Tier-B review); --no-eval-split — pidgin's frozen eval
# stays SOREVA (NV precedent)
TMPDIR=/opt/ingest/tmp MEDZEN_PROFILE= MEDZEN_AV_DIR=/opt/ingest/av \
  MEDZEN_BYTE_CONFLICT_POLICY=drop_both \
  python3.11 -m pipeline.ingest --source africanvoices --language pidgin --version v1 --no-eval-split
RC=$?
echo "INGEST_EXIT_${RC}"
aws s3 cp /var/log/ingest.log "$LOGDST" --quiet
shutdown -h now