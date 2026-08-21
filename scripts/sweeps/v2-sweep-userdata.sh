# HISTORICAL / PROVENANCE-ONLY (Codex review #11): this executor ran
# once and is NOT safe for reuse as-is — unversioned S3 reads, mutable
# image tag, overwrite-capable uploads, no sealed_gate integration.
# Future sealed evaluators MUST run scripts/sealed_gate.py acquire first.
#!/bin/bash
set -x
exec > /var/log/t6.log 2>&1
REGION=eu-central-1
ACCT=558069890522
BASE=s3://medzen-speech/research/b5-training/t6-eval-r2
CKPT=s3://medzen-speech/research/b5-training/b5-kinyarwanda-v2full-2026-001/checkpoints
EVALIMG=$ACCT.dkr.ecr.$REGION.amazonaws.com/medzen-asr-eval-runtime@sha256:dafc4b1467c5e659ef931219910c2c718705578392ad86992b11b19aadb91555
TRAINIMG=$ACCT.dkr.ecr.$REGION.amazonaws.com/medzen-trainer-omniasr:ft-2026-08-21b
sync_log() { while true; do aws s3 cp /var/log/t6.log "$BASE/log/v2-sweep.log" --quiet || true; sleep 60; done; }
sync_log &
trap 'echo "BOX_EXIT_TRAP rc=$? $(date -u +%FT%TZ)"; aws s3 cp /var/log/t6.log "$BASE/log/v2-sweep.log" --quiet || true' EXIT
mkdir -p /opt/t6/{models-base,models-active,ck,out,outputs,repo,evalin/audio}
cd /opt/t6
BUNDLES=s3://medzen-speech/research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/bundles
aws s3 cp "$BUNDLES/omniASR_tokenizer_written_v2.model.parts/part-0000" models-active/omniASR_tokenizer_written_v2.model --quiet
test -s models-active/omniASR_tokenizer_written_v2.model || { echo TOKENIZER-EMPTY; shutdown -h now; exit 1; }
aws s3 cp "$BASE/inputs/v2-repo.tar.gz" . --quiet && tar -xzf v2-repo.tar.gz -C repo
aws s3 cp "$BASE/inputs/v2-selection-dev.json" evalin/t6-selection-dev.json --quiet
for st in 0002000 0004000 0006000 0008000 0010000 0012000 0014000 0016000 0018000 0020000 0022000 0024000 0026000 0028000 0030000 0032000 0034000 0036000 0038000 0040000; do
  aws s3 cp "$CKPT/step-$st.pt" ck/step-$st.pt --quiet
done
ls ck/ | wc -l
python3 - << 'PY'
import concurrent.futures, json, pathlib, subprocess, sys
sel = json.load(open("evalin/t6-selection-dev.json"))
pairs = {(r["audio_s3_uri"], r["audio_checksum_sha256"]) for r in sel["rows"]}
def fetch(p):
    uri, sha = p
    dest = pathlib.Path(f"evalin/audio/{sha}.wav")
    if dest.is_file() and dest.stat().st_size > 0: return True
    subprocess.run(["aws","s3","cp",uri,str(dest),"--quiet"])
    return dest.is_file() and dest.stat().st_size > 0
for attempt in range(4):
    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        results = list(ex.map(fetch, sorted(pairs)))
    print(f"dev prestage pass {attempt+1}: {sum(results)}/{len(results)}", flush=True)
    if all(results): sys.exit(0)
sys.exit(1)
PY
[ $? -ne 0 ] && { echo PRESTAGE-INCOMPLETE; shutdown -h now; exit 1; }
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
docker pull "$EVALIMG" >/dev/null 2>&1 || docker pull "$EVALIMG"
docker pull "$TRAINIMG" >/dev/null 2>&1 || docker pull "$TRAINIMG"
# FULL checkpoints: extraction only (no merge, no GPU math) — refuses
# non-finite tensors by design
chmod -R 777 /opt/t6/out
docker run --rm --entrypoint python3 \
  -v /opt/t6/ck:/inputs/checkpoints:ro \
  -v /opt/t6/out:/outputs/merged -v /opt/t6/repo:/repo:ro \
  "$TRAINIMG" /repo/scripts/t6_checkpoint_merge.py || { echo EXTRACT-FAILED; shutdown -h now; exit 1; }
run_eval() {
  chmod -R 777 /opt/t6/outputs
  docker run --rm --gpus all --entrypoint python3 \
    -v /opt/t6/models-active:/models:ro -v /opt/t6/evalin:/inputs:ro \
    -v /opt/t6/outputs:/outputs -v /opt/t6/repo:/repo:ro \
    -e PYTHONPATH=/repo/services/asr-eval-runtime \
    "$EVALIMG" /repo/scripts/t6_eval_runner.py
  RC=$?
  [ $RC -eq 0 ] && aws s3 cp /opt/t6/outputs/t6-results.json "$BASE/results/v2-sweep/$1.json" --quiet
  [ $RC -eq 0 ] && aws s3 cp /opt/t6/outputs/t6-row-receipts.jsonl "$BASE/results/v2-sweep/$1.rows.jsonl" --quiet
  rm -f /opt/t6/outputs/t6-results.json /opt/t6/outputs/t6-row-receipts.jsonl
  return $RC
}
for m in /opt/t6/out/merged-step-*.pt; do
  st=$(basename "$m" .pt | sed 's/merged-step-//')
  cp "$m" models-active/omniASR-CTC-1B-v2.pt
  run_eval "dev-v2-$st" || echo "EVAL-FAILED-$st"
done
echo V2_SWEEP_DONE
aws s3 cp /var/log/t6.log "$BASE/log/v2-sweep.log" --quiet
shutdown -h now