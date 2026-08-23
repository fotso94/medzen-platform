#!/bin/bash
# ARM-1 GPU serving SMOKE (Codex round 13: "a capped synthetic GPU smoke
# test may proceed independently"). DEVELOPMENT-ONLY: verifies that the
# round-12 GPU serving image (by ECR digest) serves the arm-1 MERGED
# EXPORT through the loader's marker path — staged bytes digest-verified,
# marker written by the loader code from the pinned commit, /readyz
# turns ready, one dev-pool utterance transcribes, and the response
# carries the exact artifact tree. No sealed set read. Create-only
# receipt; hard shutdown cap.
set -u
exec > /var/log/smoke.log 2>&1
shutdown -h +100
REGION=eu-central-1; ACCT=558069890522; BUCKET=medzen-speech
OUT=research/b5-training/arm1-gpu-smoke-2026-001
KMS=arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57
GPUIMG=$ACCT.dkr.ecr.$REGION.amazonaws.com/medzen-asr-runtime@sha256:5bd32e6ed796095a338356d54cf4e5e443ebf7a3eac188bbcdb932899f0b0056
REPO_KEY=research/b5-training/arm1-dev-sweep-2026-001/inputs/repo-__HEAD__.tar.gz
TAR_KEY=research/b5-training/b5-universal-arm1-2026-005/output/medzen-b5-b5-universal-arm1-2026-005/output/model.tar.gz
TAR_VID=QfK3zQ_p4Ls43cF1KmIWTzPja7vLW0P4
TAR_SHA=872bdbc666d8eac06901d3ca1a3ea885cd6b8d1d4173b2c901d7e3e3f3776c50
EXPORT_SHA=c6604a689688a5314b23d53c3d45362d2b8123e9c894568b9810de2d40f7490c
TOK_KEY="research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/bundles/omniASR_tokenizer_written_v2.model.parts/part-0000"
TOK_SHA=8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e
WAV_URI=s3://medzen-speech/eval/english/asr/fleurs-v1/audio/1700_0180a339ca77.wav
WAV_SHA=0180a339ca778183d5874713dca55eee524c6098c1638ab95f112657f78e0408
sync_log() { while true; do aws s3 cp /var/log/smoke.log "s3://$BUCKET/$OUT/log/smoke.log" --quiet || true; sleep 30; done; }
sync_log &
trap 'echo "BOX_EXIT_TRAP rc=$? $(date -u +%FT%TZ)"; aws s3 cp /var/log/smoke.log "s3://$BUCKET/$OUT/log/smoke.log" --quiet || true' EXIT
mkdir -p /opt/smoke/models /opt/smoke/repo && cd /opt/smoke
fetch() { # key dest vid sha
  aws s3api get-object --bucket $BUCKET --key "$1" "$2" > /tmp/meta.json || { echo "GET-FAILED $1"; shutdown -h now; exit 1; }
  [ "$(python3 -c "import json;print(json.load(open('/tmp/meta.json'))['VersionId'])")" = "$3" ] || { echo "VID-MISMATCH $1"; shutdown -h now; exit 1; }
  [ "$(sha256sum "$2" | cut -d' ' -f1)" = "$4" ] || { echo "SHA-MISMATCH $1"; shutdown -h now; exit 1; }
}
REPO_VID=$(aws s3api head-object --bucket $BUCKET --key "$REPO_KEY" --query VersionId --output text)
aws s3 cp "s3://$BUCKET/$REPO_KEY" repo.tar.gz --quiet && tar -xzf repo.tar.gz -C repo
[ "$(sha256sum repo.tar.gz | cut -d' ' -f1)" = "__REPO_SHA__" ] || { echo REPO-SHA-MISMATCH; shutdown -h now; exit 1; }
fetch "$TAR_KEY" model.tar.gz "$TAR_VID" "$TAR_SHA"
tar -xzf model.tar.gz export/model.pt export/manifest.json
[ "$(sha256sum export/model.pt | cut -d' ' -f1)" = "$EXPORT_SHA" ] || { echo EXPORT-SHA-MISMATCH; shutdown -h now; exit 1; }
mv export/model.pt models/omniASR-CTC-1B-v2.pt
TOK_VID=$(aws s3api head-object --bucket $BUCKET --key "$TOK_KEY" --query VersionId --output text)
fetch "$TOK_KEY" models/omniASR_tokenizer_written_v2.model "$TOK_VID" "$TOK_SHA"
aws s3 cp "$WAV_URI" sample.wav --quiet
[ "$(sha256sum sample.wav | cut -d' ' -f1)" = "$WAV_SHA" ] || { echo WAV-SHA-MISMATCH; shutdown -h now; exit 1; }
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
docker pull "$GPUIMG" >/dev/null 2>&1 || docker pull "$GPUIMG" || { echo PULL-FAILED; shutdown -h now; exit 1; }
# marker written by the LOADER CODE from the pinned commit, inside the image
chmod -R 777 /opt/smoke/models
docker run --rm --entrypoint python3 -v /opt/smoke/repo:/repo:ro -v /opt/smoke/models:/models "$GPUIMG" - <<'PY'
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, "/repo/services/model-loader")
from medzen_model_loader.loader_v2 import write_ready_marker_v2
from medzen_model_loader.languages_v2 import canonical_language_ids
ck = Path("/models/omniASR-CTC-1B-v2.pt"); tok = Path("/models/omniASR_tokenizer_written_v2.model")
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 24), b""): h.update(c)
    return h.hexdigest()
digest, tok_sha = sha(ck), sha(tok)
tree = hashlib.sha256(json.dumps({"checkpoint_sha256": digest, "tokenizer_sha256": tok_sha},
                                 sort_keys=True, separators=(",", ":")).encode()).hexdigest()
manifest = {"schema_version": 2, "classification": "NONPROD_REAL_PROVIDER_V2",
            "model_family": "omniasr_ctc_1b",
            "artifact": {"format": "fairseq2_pt", "sha256": digest},
            "tokenizer": {"sha256": tok_sha},
            "artifact_tree_sha256": tree,
            "model_version": f"omniasr_ctc_1b:{tree[:12]}",
            "languages": ["english", "ewe", "french", "kinyarwanda", "lingala", "pidgin", "swahili"],
            "language_ids": canonical_language_ids()}
manifest_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
path = write_ready_marker_v2(manifest, manifest_sha256=manifest_sha,
                             checkpoint_path=ck, tokenizer_path=tok,
                             model_dir=Path("/models"))
print(json.dumps({"marker": str(path), "artifact_tree_sha256": tree,
                  "artifact_sha256": digest, "tokenizer_sha256": tok_sha}))
PY
[ $? -ne 0 ] && { echo MARKER-FAILED; shutdown -h now; exit 1; }
TREE=$(python3 -c "
import hashlib,json
def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda: f.read(1<<24), b''): h.update(c)
    return h.hexdigest()
d=sha('models/omniASR-CTC-1B-v2.pt'); t=sha('models/omniASR_tokenizer_written_v2.model')
print(hashlib.sha256(json.dumps({'checkpoint_sha256':d,'tokenizer_sha256':t},sort_keys=True,separators=(',',':')).encode()).hexdigest())")
docker run -d --name smoke --gpus all -p 8081:8081 -v /opt/smoke/models:/models:ro "$GPUIMG"
for i in $(seq 1 60); do
  sleep 5
  READY=$(curl -s -o /tmp/ready.json -w '%{http_code}' http://localhost:8081/readyz)
  [ "$READY" = "200" ] && break
done
cat /tmp/ready.json
[ "$READY" = "200" ] || { echo NEVER-READY; docker logs smoke 2>&1 | tail -30; shutdown -h now; exit 1; }
RID=$(python3 -c "import uuid;print(uuid.uuid4())")
T0=$(date +%s%3N)
CODE=$(curl -s -o /tmp/transcribe.json -w '%{http_code}' -X POST http://localhost:8081/internal/v1/transcriptions \
  -H "Content-Type: audio/wav" -H "X-Request-ID: $RID" -H "X-MedZen-Language: en" \
  --data-binary @sample.wav)
T1=$(date +%s%3N)
echo "HTTP $CODE in $((T1-T0))ms"; cat /tmp/transcribe.json
python3 - "$TREE" <<'PY'
import json, sys
tree = sys.argv[1]
r = json.load(open("/tmp/transcribe.json"))
assert r["artifact_tree_sha256"] == tree, "TREE-MISMATCH"
assert r["classification"] == "NONPROD_REAL_PROVIDER_V2", r["classification"]
assert r["production_approved"] is False
assert r["transcript"]["verbatim"].strip(), "EMPTY-TRANSCRIPT"
ready = json.load(open("/tmp/ready.json"))
receipt = {"record": "ARM1-GPU-SMOKE-2026-001-RECEIPT",
           "image": "medzen-asr-runtime@sha256:5bd32e6ed796095a338356d54cf4e5e443ebf7a3eac188bbcdb932899f0b0056",
           "artifact_tree_sha256": tree,
           "readyz": ready, "response": r,
           "reference": "it is thinner under the maria and thicker under the highlands",
           "classification_served": r["classification"],
           "sealed_sets_read": False}
open("/opt/smoke/receipt.json", "w").write(json.dumps(receipt, indent=1, sort_keys=True))
print("SMOKE_ASSERTIONS_PASS")
PY
[ $? -ne 0 ] && { echo SMOKE-ASSERT-FAILED; shutdown -h now; exit 1; }
aws s3api put-object --bucket $BUCKET --key "$OUT/receipt.json" --body /opt/smoke/receipt.json \
  --if-none-match '*' --server-side-encryption aws:kms --ssekms-key-id $KMS && echo RECEIPT_UP
echo ARM1_GPU_SMOKE_DONE
aws s3 cp /var/log/smoke.log "s3://$BUCKET/$OUT/log/smoke.log" --quiet
shutdown -h now
