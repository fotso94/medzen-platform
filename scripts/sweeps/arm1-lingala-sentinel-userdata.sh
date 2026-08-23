#!/bin/bash
# ARM-1 LINGALA regression sentinel (Codex review #14 finding 1): evaluate
# base + step-0014000 on the frozen 386-row lingala dev selection and upload
# both per-row receipt sets. DEV-ONLY; no sealed set. Version-bound reads,
# digest-pinned images, create-only uploads, hard cost cap.
set -u
exec > /var/log/sentinel.log 2>&1
shutdown -h +90
REGION=eu-central-1; ACCT=558069890522; BUCKET=medzen-speech
OUT=research/b5-training/arm1-lingala-sentinel-2026-001
KMS=arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57
EVALIMG=$ACCT.dkr.ecr.$REGION.amazonaws.com/medzen-asr-eval-runtime@sha256:dafc4b1467c5e659ef931219910c2c718705578392ad86992b11b19aadb91555
TRAINIMG=$ACCT.dkr.ecr.$REGION.amazonaws.com/medzen-trainer-omniasr@sha256:059f4bfb27d2dc5aeca8a5f609398f3590379bdf0a8e40c6b59680cf02507a7b
REPO_KEY=research/b5-training/arm1-dev-sweep-2026-001/inputs/repo-__HEAD__.tar.gz
CK_KEY=research/b5-training/b5-universal-arm1-2026-005/checkpoints/step-0014000.pt
CK_VID=__CK_VID__; CK_SHA=__CK_SHA__
BASE_KEY="research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/bundles/omniASR-CTC-1B-v2.pt.parts/part-0000"
BASE_VID=wrn40IBzqLLtFrt7vzq0XYZyqm0FuW_9; BASE_SHA=354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c
TOK_KEY="research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/bundles/omniASR_tokenizer_written_v2.model.parts/part-0000"
TOK_VID=98.BAQFmuF399JKuwRkzeTHm_J0v6nSc; TOK_SHA=8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e
SEL_KEY=$OUT/inputs/selection.json; SEL_VID=__SEL_VID__; SEL_SHA=__SEL_SHA__
sync_log(){ while true; do aws s3 cp /var/log/sentinel.log "s3://$BUCKET/$OUT/log/sentinel.log" --quiet||true; sleep 45; done; }
sync_log &
trap 'echo "BOX_EXIT_TRAP rc=$? $(date -u +%FT%TZ)"; aws s3 cp /var/log/sentinel.log "s3://$BUCKET/$OUT/log/sentinel.log" --quiet||true' EXIT
mkdir -p /opt/s/{models,ck,out,outputs,repo,evalin/audio,results}; cd /opt/s
fetch(){ aws s3api get-object --bucket $BUCKET --key "$1" "$2" > /tmp/m.json || { echo "GET-FAIL $1"; shutdown -h now; exit 1; }
  [ "$(python3 -c "import json;print(json.load(open('/tmp/m.json'))['VersionId'])")" = "$3" ] || { echo "VID-MISMATCH $1"; shutdown -h now; exit 1; }
  [ -n "$4" ] && [ "$(sha256sum "$2"|cut -d' ' -f1)" != "$4" ] && { echo "SHA-MISMATCH $1"; shutdown -h now; exit 1; }; return 0; }
fetch "$SEL_KEY" evalin/t6-selection-lingala.json "$SEL_VID" "$SEL_SHA"
# rewrap selection -> {"rows":[...]} for the eval runner
python3 -c "import json; d=json.load(open('evalin/t6-selection-lingala.json')); json.dump({'rows':d['rows']}, open('evalin/t6-selection-lingala.json','w'))"
aws s3 cp "s3://$BUCKET/$REPO_KEY" repo.tar.gz --quiet && tar -xzf repo.tar.gz -C repo
[ -f /opt/s/repo/scripts/t6_checkpoint_merge.py ] || { echo REPO-FETCH-FAIL; shutdown -h now; exit 1; }
fetch "$TOK_KEY" models/omniASR_tokenizer_written_v2.model "$TOK_VID" "$TOK_SHA"
fetch "$BASE_KEY" base.pt "$BASE_VID" "$BASE_SHA"
fetch "$CK_KEY" ck/step-0014000.pt "$CK_VID" "$CK_SHA"
# prestage lingala audio (checksum-verified)
python3 - <<'PY'
import concurrent.futures, hashlib, json, pathlib, subprocess, sys
rows=json.load(open("evalin/t6-selection-lingala.json"))["rows"]
pairs=sorted({(r["audio_s3_uri"], r["audio_checksum_sha256"]) for r in rows})
def g(p):
    uri,sha=p; d=pathlib.Path(f"evalin/audio/{sha}.wav")
    for _ in range(4):
        subprocess.run(["aws","s3","cp",uri,str(d),"--quiet"])
        if d.is_file() and hashlib.sha256(d.read_bytes()).hexdigest()==sha: return True
    return False
with concurrent.futures.ThreadPoolExecutor(16) as ex: ok=list(ex.map(g,pairs))
print(f"audio {sum(ok)}/{len(ok)}"); sys.exit(0 if all(ok) else 1)
PY
[ $? -ne 0 ] && { echo PRESTAGE-INCOMPLETE; shutdown -h now; exit 1; }
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
docker pull "$EVALIMG" >/dev/null 2>&1 || docker pull "$EVALIMG"
docker pull "$TRAINIMG" >/dev/null 2>&1 || docker pull "$TRAINIMG"
# extract step-0014000
chmod -R 777 /opt/s/out
docker run --rm --entrypoint python3 -v /opt/s/ck:/inputs/checkpoints:ro -v /opt/s/out:/outputs/merged -v /opt/s/repo:/repo:ro "$TRAINIMG" /repo/scripts/t6_checkpoint_merge.py || { echo EXTRACT-FAIL; shutdown -h now; exit 1; }
run_eval(){ # $1=label
  chmod -R 777 /opt/s/outputs; rm -f outputs/t6-results.json outputs/t6-row-receipts.jsonl
  docker run --rm --gpus all --entrypoint python3 -v /opt/s/models:/models:ro -v /opt/s/evalin:/inputs:ro -v /opt/s/outputs:/outputs -v /opt/s/repo:/repo:ro -e PYTHONPATH=/repo/services/asr-eval-runtime "$EVALIMG" /repo/scripts/t6_eval_runner.py || { echo "EVAL-FAIL $1"; shutdown -h now; exit 1; }
  aws s3api put-object --bucket $BUCKET --key "$OUT/results/$1.results.json" --body outputs/t6-results.json --if-none-match '*' --server-side-encryption aws:kms --ssekms-key-id $KMS >/dev/null
  aws s3api put-object --bucket $BUCKET --key "$OUT/results/$1.rows.jsonl" --body outputs/t6-row-receipts.jsonl --if-none-match '*' --server-side-encryption aws:kms --ssekms-key-id $KMS >/dev/null
  python3 -c "import json;d=json.load(open('outputs/t6-results.json'));print('$1 lingala WER:', d['per_language']['lingala']['wer'])"; }
cp base.pt models/omniASR-CTC-1B-v2.pt; run_eval base
cp /opt/s/out/merged-step-0014000.pt models/omniASR-CTC-1B-v2.pt; run_eval step-0014000
echo ARM1_LINGALA_SENTINEL_DONE
aws s3 cp /var/log/sentinel.log "s3://$BUCKET/$OUT/log/sentinel.log" --quiet
shutdown -h now
