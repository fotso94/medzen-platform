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
LOADERIMG=558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-model-loader@sha256:8fa4c129fc1235485a1e979da941a5f61516a1eb1ab6a2f7371ce4f200182739
GPUIMG=558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-runtime@sha256:67c81efd8458ea1e4424dc39349fdbf2254f4bf929cb7bce98bd284873cf1226
REPO_KEY=research/b5-training/arm1-dev-sweep-2026-001/inputs/repo-f39b34dadc6cfe868e11df5cec3399558fde70d1.tar.gz
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
# GetObject (not versioned) + sha256 verify — the trainer role has
# s3:GetObject on research/*; the sha256 is the integrity guarantee
aws s3 cp "s3://$BUCKET/$TAR_KEY" model.tar.gz --quiet || { echo TAR-GET-FAIL; shutdown -h now; exit 1; }
tar -xzf model.tar.gz export/model.pt
[ "$(sha256sum export/model.pt|cut -d' ' -f1)" = "$EXPORT_SHA" ] || { echo EXPORT-SHA-FAIL; shutdown -h now; exit 1; }
aws s3 cp "s3://$BUCKET/$TOK_KEY" tokenizer.model --quiet || { echo TOK-GET-FAIL; shutdown -h now; exit 1; }
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
CLIPS=[{"language": "english", "iso": "en", "uri": "s3://medzen-speech/eval/english/asr/fleurs-v1/audio/1700_0180a339ca77.wav", "sha": "0180a339ca778183d5874713dca55eee524c6098c1638ab95f112657f78e0408", "ref": "it is thinner under the maria and thicker under the highland"}, {"language": "ewe", "iso": "ewe", "uri": "s3://medzen-speech/eval/ewe/asr/soreva_ewe_tg/soreva-v1/audio/cm_ewe_023_007bf856d225.wav", "sha": "007bf856d2258e85be4c5fa6dfd632c47883ded08be796278d0800882fd9c1c9", "ref": "at\u0254\u0303"}, {"language": "french", "iso": "fr", "uri": "s3://medzen-speech/eval/french/asr/fleurs-v1/audio/1523_002df1b127ed.wav", "sha": "002df1b127ed89d015d7033dec509790d9828fac17f2cbd84a56e3a85dcd87f3", "ref": "le match s'est jou\u00e9 \u00e0 un point 21 \u00e0 20 mettant fin \u00e0 la s\u00e9ri"}, {"language": "kinyarwanda", "iso": "kin", "uri": "s3://medzen-speech/curated/kinyarwanda/asr/cv17_rw/cv17-test-v1/audio/common_voice_rw_20331772_0008221f4693.wav", "sha": "0008221f469348722bd4e482d3524d16d671650e5b9193fc6c6be8172ac7eb52", "ref": "com ni iki wumva kidasanzwe ushima imana ku myaka yawe wujuj"}, {"language": "lingala", "iso": "lin", "uri": "s3://medzen-speech/eval/lingala/asr/fleurs-v1/audio/1731_013a96bc648e.wav", "sha": "013a96bc648eef54008af9d61b0cdad588d777ba5218bd6d8aabf87f9eaa1ea5", "ref": "na bisanga mpe na balake eza na ntina te kozala na yacht"}, {"language": "pidgin", "iso": "pcm", "uri": "s3://medzen-speech/curated/pidgin/asr/av_pcm/v1/audio/pcm_m_PT3M1_ev_read_001.wav", "sha": "005309cd182a1a2059d59c85fc6d8661b2482fc1bbda8ef7030566c2bce8700c", "ref": "abeg de money wey you talk say you transfer give me i no rec"}, {"language": "swahili", "iso": "swa", "uri": "s3://medzen-speech/eval/swahili/asr/fleurs-v1/audio/1530_00cbe4831837.wav", "sha": "00cbe4831837cb153f0cc1ddbc6a582f2ba03b8691653bbc41abfb6c0db2ff9d", "ref": "ulikuwa umeratibiwa kukatwa mnamo jumanne lakini uliokolewa "}]
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
receipt={"record":"ARM1-GPU-RESMOKE-2026-001-RECEIPT","loader_image":"558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-model-loader@sha256:8fa4c129fc1235485a1e979da941a5f61516a1eb1ab6a2f7371ce4f200182739","gpu_image":"558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-runtime@sha256:67c81efd8458ea1e4424dc39349fdbf2254f4bf929cb7bce98bd284873cf1226",
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
