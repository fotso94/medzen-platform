#!/bin/bash
# ARM-1 GPU RE-SMOKE (Codex review #14 finding 6): faithful version of the
# round-13 smoke. Exercises the REAL model-loader init container
# (run_b6v2_init: S3 download -> digest-verify -> stage /models -> marker),
# then serves with the GPU runtime and transcribes ONE dev-pool clip per
# language for ALL SEVEN languages. Images pulled by immutable digest.
# DEV-ONLY; no sealed set. Create-only receipt; hard cost cap.
set -u
exec > /var/log/resmoke.log 2>&1
shutdown -h +110
REGION=eu-central-1; ACCT=558069890522; BUCKET=medzen-speech
OUT=research/b5-training/arm1-gpu-resmoke-2026-001
STAGE=$OUT/staging
KMS=arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57
LOADERIMG=__LOADER_IMG__
GPUIMG=__GPU_IMG__
REPO_KEY=research/b5-training/arm1-dev-sweep-2026-001/inputs/repo-__HEAD__.tar.gz
TAR_KEY=research/b5-training/b5-universal-arm1-2026-005/output/medzen-b5-b5-universal-arm1-2026-005/output/model.tar.gz
TAR_VID=QfK3zQ_p4Ls43cF1KmIWTzPja7vLW0P4
EXPORT_SHA=c6604a689688a5314b23d53c3d45362d2b8123e9c894568b9810de2d40f7490c
TOK_KEY="research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/bundles/omniASR_tokenizer_written_v2.model.parts/part-0000"
TOK_VID=98.BAQFmuF399JKuwRkzeTHm_J0v6nSc; TOK_SHA=8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e
sync_log(){ while true; do aws s3 cp /var/log/resmoke.log "s3://$BUCKET/$OUT/log/resmoke.log" --quiet||true; sleep 30; done; }
sync_log &
trap 'echo "BOX_EXIT rc=$? $(date -u +%FT%TZ)"; aws s3 cp /var/log/resmoke.log "s3://$BUCKET/$OUT/log/resmoke.log" --quiet||true' EXIT
mkdir -p /opt/rs/{models,repo,clips}; cd /opt/rs
aws s3 cp "s3://$BUCKET/$REPO_KEY" repo.tar.gz --quiet && tar -xzf repo.tar.gz -C repo
[ -f /opt/rs/repo/platform/manifests/B6V2-ARM1-SMOKE-MANIFEST-2026-001.json ] || { echo REPO-FAIL; shutdown -h now; exit 1; }
# stage the artifact set into the smoke prefix (create-only)
aws s3api get-object --bucket $BUCKET --key "$TAR_KEY" model.tar.gz --version-id "$TAR_VID" >/dev/null || { echo TAR-GET-FAIL; shutdown -h now; exit 1; }
tar -xzf model.tar.gz export/model.pt
[ "$(sha256sum export/model.pt|cut -d' ' -f1)" = "$EXPORT_SHA" ] || { echo EXPORT-SHA-FAIL; shutdown -h now; exit 1; }
aws s3api get-object --bucket $BUCKET --key "$TOK_KEY" tokenizer.model --version-id "$TOK_VID" >/dev/null || { echo TOK-GET-FAIL; shutdown -h now; exit 1; }
[ "$(sha256sum tokenizer.model|cut -d' ' -f1)" = "$TOK_SHA" ] || { echo TOK-SHA-FAIL; shutdown -h now; exit 1; }
MAN=/opt/rs/repo/platform/manifests/B6V2-ARM1-SMOKE-MANIFEST-2026-001.json
MAN_SHA=$(sha256sum "$MAN"|cut -d' ' -f1)
aws s3api put-object --bucket $BUCKET --key "$STAGE/manifest.json" --body "$MAN" --if-none-match '*' --server-side-encryption aws:kms --ssekms-key-id $KMS >/dev/null || echo "manifest exists (ok on retry)"
aws s3api put-object --bucket $BUCKET --key "$STAGE/model.pt" --body export/model.pt --if-none-match '*' --server-side-encryption aws:kms --ssekms-key-id $KMS >/dev/null || echo "model exists"
aws s3api put-object --bucket $BUCKET --key "$STAGE/omniASR_tokenizer_written_v2.model" --body tokenizer.model --if-none-match '*' --server-side-encryption aws:kms --ssekms-key-id $KMS >/dev/null || echo "tokenizer exists"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
for img in "$LOADERIMG" "$GPUIMG"; do docker pull "$img" >/dev/null 2>&1 || docker pull "$img" || { echo "PULL-FAIL $img"; shutdown -h now; exit 1; }; done
# REAL loader init: run_b6v2_init downloads+verifies+stages /models+marker
chmod -R 777 /opt/rs/models
docker run --rm -e MEDZEN_LOADER_MODE=b6v2 \
  -e MEDZEN_B6V2_MANIFEST_URI="s3://$BUCKET/$STAGE/manifest.json" \
  -e MEDZEN_B6V2_MANIFEST_SHA256="$MAN_SHA" -e AWS_REGION=$REGION -e MODEL_DIR=/models \
  -v /opt/rs/models:/models "$LOADERIMG" > init.json 2>init.err || { echo LOADER-INIT-FAIL; cat init.err|tail -20; shutdown -h now; exit 1; }
cat init.json
[ -s /opt/rs/models/.medzen-ready-v2.json ] || { echo MARKER-MISSING; shutdown -h now; exit 1; }
# serve
docker run -d --name rs --gpus all -p 8081:8081 -v /opt/rs/models:/models:ro "$GPUIMG"
for i in $(seq 1 60); do sleep 5; RC=$(curl -s -o /tmp/r.json -w '%{http_code}' http://localhost:8081/readyz); [ "$RC" = "200" ] && break; done
cat /tmp/r.json
[ "$RC" = "200" ] || { echo NEVER-READY; docker logs rs 2>&1|tail -30; shutdown -h now; exit 1; }
# transcribe one clip per language (7)
python3 - <<'PY'
import json, subprocess, urllib.request, uuid, hashlib, sys
CLIPS=__CLIPS__
results={}
tree=json.load(open("/opt/rs/models/.medzen-ready-v2.json"))["artifact_tree_sha256"]
for c in CLIPS:
    subprocess.run(["aws","s3","cp",c["uri"],"/opt/rs/clips/a.wav","--quiet"],check=True)
    audio=open("/opt/rs/clips/a.wav","rb").read()
    if hashlib.sha256(audio).hexdigest()!=c["sha"]: print("SHA-FAIL",c["language"]); sys.exit(1)
    req=urllib.request.Request("http://localhost:8081/internal/v1/transcriptions",data=audio,
        headers={"Content-Type":"audio/wav","X-Request-ID":str(uuid.uuid4()),"X-MedZen-Language":c["iso"]},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=120) as r: body=json.loads(r.read()); code=r.status
    except Exception as e: print("HTTP-FAIL",c["language"],e); sys.exit(1)
    assert code==200 and body.get("artifact_tree_sha256")==tree, (c["language"],code)
    txt=body["transcript"]["verbatim"]
    results[c["language"]]={"iso":c["iso"],"http":code,"latency_ms":body["latency_ms"],
        "language_probability":body.get("language_probability"),
        "hypothesis_sha256":hashlib.sha256(txt.encode()).hexdigest(),"nonempty":bool(txt.strip()),
        "classification":body["classification"],"production_approved":body["production_approved"]}
    print(c["language"],c["iso"],"HTTP",code,round(body["latency_ms"],1),"ms nonempty",bool(txt.strip()))
receipt={"record":"ARM1-GPU-RESMOKE-2026-001-RECEIPT","loader_image":"__LOADER_IMG__","gpu_image":"__GPU_IMG__",
 "artifact_tree_sha256":tree,"init":json.load(open("/opt/rs/init.json")),
 "readyz":json.load(open("/tmp/r.json")),"per_language":results,
 "real_loader_init":True,"languages_tested":len(results),"sealed_sets_read":False}
open("/opt/rs/receipt.json","w").write(json.dumps(receipt,indent=1,sort_keys=True))
if len(results)!=7 or not all(v["nonempty"] for v in results.values()): print("RESMOKE-INCOMPLETE"); sys.exit(1)
print("ALL_7_LANGUAGES_PASS")
PY
[ $? -ne 0 ] && { echo RESMOKE-ASSERT-FAIL; shutdown -h now; exit 1; }
aws s3api put-object --bucket $BUCKET --key "$OUT/receipt.json" --body /opt/rs/receipt.json --if-none-match '*' --server-side-encryption aws:kms --ssekms-key-id $KMS && echo RECEIPT_UP
echo ARM1_GPU_RESMOKE_DONE
aws s3 cp /var/log/resmoke.log "s3://$BUCKET/$OUT/log/resmoke.log" --quiet
shutdown -h now
