#!/bin/bash
# ARM-1 DEVELOPMENT-ONLY checkpoint sweep (Codex round 13). Hardened
# successor of v2-sweep-userdata.sh: every S3 read is bound to a committed
# VersionId (the served VersionId is verified on each GET; mismatch refuses),
# every committed sha256 is verified, images are pulled by IMMUTABLE digest,
# result uploads are create-only (If-None-Match: *), one checkpoint is staged
# at a time, and a hard shutdown cap bounds cost. No sealed set is read.
set -u
exec > /var/log/sweep.log 2>&1
shutdown -h +210   # hard cost cap: the box dies after 3.5h no matter what
REGION=eu-central-1; ACCT=558069890522; BUCKET=medzen-speech
OUT=research/b5-training/arm1-dev-sweep-2026-001
BINDINGS_KEY=__BINDINGS_KEY__; BINDINGS_VID=__BINDINGS_VID__; BINDINGS_SHA=__BINDINGS_SHA__
KMS=arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57
sync_log() { while true; do aws s3 cp /var/log/sweep.log "s3://$BUCKET/$OUT/log/sweep.log" --quiet || true; sleep 60; done; }
sync_log &
trap 'echo "BOX_EXIT_TRAP rc=$? $(date -u +%FT%TZ)"; aws s3 cp /var/log/sweep.log "s3://$BUCKET/$OUT/log/sweep.log" --quiet || true' EXIT
mkdir -p /opt/sweep/{models-active,ck,out,outputs,repo,evalin/audio,results}
cd /opt/sweep
cat > driver.py <<'PY'
import concurrent.futures, hashlib, json, os, pathlib, subprocess, sys, time
BUCKET=os.environ["BUCKET"]; OUT=os.environ["OUT"]; KMS=os.environ["KMS"]
ACCT=os.environ["ACCT"]; REGION=os.environ["REGION"]
def log(m): print(f"{time.strftime('%FT%TZ', time.gmtime())} {m}", flush=True)
def sha256_file(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for chunk in iter(lambda: f.read(1<<24), b""): h.update(chunk)
    return h.hexdigest()
def fetch(key, dest, version_id, sha256=None):
    r=subprocess.run(["aws","s3api","get-object","--bucket",BUCKET,"--key",key,dest],
                     capture_output=True, text=True)
    if r.returncode: raise SystemExit(f"GET {key} failed: {r.stderr[:300]}")
    meta=json.loads(r.stdout)
    if meta.get("VersionId")!=version_id:
        raise SystemExit(f"REFUSED {key}: served VersionId {meta.get('VersionId')} != bound {version_id}")
    digest=sha256_file(dest)
    if sha256 and digest!=sha256:
        raise SystemExit(f"REFUSED {key}: sha256 {digest[:12]} != bound {sha256[:12]}")
    return {"version_id":meta["VersionId"],"etag":meta["ETag"].strip('"'),"sha256":digest,
            "size":os.path.getsize(dest)}
def put_once(local, key):
    r=subprocess.run(["aws","s3api","put-object","--bucket",BUCKET,"--key",key,"--body",local,
                      "--if-none-match","*","--server-side-encryption","aws:kms","--ssekms-key-id",KMS],
                     capture_output=True, text=True)
    if r.returncode: raise SystemExit(f"PUT-ONCE {key} failed (exists?): {r.stderr[:300]}")
    return json.loads(r.stdout)["VersionId"]
B=json.load(open("bindings.json"))
receipt={"record":"ARM1-DEV-SWEEP-2026-001-RECEIPT","bindings_sha256":os.environ["BINDINGS_SHA"],
         "instance_id":subprocess.run(["bash","-c","TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60'); curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/instance-id"],capture_output=True,text=True).stdout.strip(),
         "started_utc":time.strftime('%FT%TZ', time.gmtime()),"inputs":{},"checkpoints":{},"uploads":{}}
# --- inputs, every one version-bound and sha-verified
sel=B["selection"]; receipt["inputs"]["selection"]=fetch(sel["key"],"evalin/t6-selection-dev.json",sel["version_id"],sel["sha256"])
repo=B["repo_tarball"]; receipt["inputs"]["repo_tarball"]=fetch(repo["key"],"repo.tar.gz",repo["version_id"],repo["sha256"])
subprocess.run(["tar","-xzf","repo.tar.gz","-C","repo"],check=True)
tok=B["tokenizer"]; receipt["inputs"]["tokenizer"]=fetch(tok["key"],"models-active/omniASR_tokenizer_written_v2.model",tok["version_id"],tok["sha256"])
base=B["base_model"]; receipt["inputs"]["base_model"]=fetch(base["key"],"base.pt",base["version_id"],base["sha256"])
log("inputs bound and verified")
# --- audio prestage: checksum-named, verified
rows=json.load(open("evalin/t6-selection-dev.json"))["rows"]
if hashlib.sha256(json.dumps(sorted(rows,key=lambda r:(r["language"],r["audio_checksum_sha256"])),sort_keys=True,separators=(",",":")).encode()).hexdigest()!=sel["rows_sha256"]:
    raise SystemExit("REFUSED: selection rows_sha256 mismatch")
pairs=sorted({(r["audio_s3_uri"],r["audio_checksum_sha256"]) for r in rows})
def get_audio(p):
    uri,sha=p; dest=pathlib.Path(f"evalin/audio/{sha}.wav")
    for _ in range(4):
        subprocess.run(["aws","s3","cp",uri,str(dest),"--quiet"])
        if dest.is_file() and sha256_file(dest)==sha: return True
    return False
with concurrent.futures.ThreadPoolExecutor(16) as ex: ok=list(ex.map(get_audio,pairs))
if not all(ok): raise SystemExit(f"REFUSED: audio prestage incomplete {sum(ok)}/{len(ok)}")
receipt["inputs"]["audio_rows"]=len(pairs); log(f"audio prestaged {len(pairs)} verified")
# --- images by immutable digest
subprocess.run(f"aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {ACCT}.dkr.ecr.{REGION}.amazonaws.com",shell=True,check=True,capture_output=True)
EVALIMG=B["images"]["eval_runtime"]; TRAINIMG=B["images"]["trainer"]
for img in (EVALIMG,TRAINIMG):
    for _ in range(3):
        if subprocess.run(["docker","pull",img],capture_output=True).returncode==0: break
    else: raise SystemExit(f"pull failed {img}")
receipt["images"]={"eval_runtime":EVALIMG,"trainer":TRAINIMG}
def run_eval(label):
    subprocess.run(["chmod","-R","777","/opt/sweep/outputs"])
    for f in ("t6-results.json","t6-row-receipts.jsonl"): pathlib.Path(f"outputs/{f}").unlink(missing_ok=True)
    r=subprocess.run(["docker","run","--rm","--gpus","all","--entrypoint","python3",
        "-v","/opt/sweep/models-active:/models:ro","-v","/opt/sweep/evalin:/inputs:ro",
        "-v","/opt/sweep/outputs:/outputs","-v","/opt/sweep/repo:/repo:ro",
        "-e","PYTHONPATH=/repo/services/asr-eval-runtime",EVALIMG,"/repo/scripts/t6_eval_runner.py"],
        capture_output=True,text=True)
    if r.returncode: raise SystemExit(f"EVAL FAILED {label}: {r.stderr[-1500:]}")
    res=json.load(open("outputs/t6-results.json"))
    up={"results":put_once("outputs/t6-results.json",f"{OUT}/results/{label}.json"),
        "rows":put_once("outputs/t6-row-receipts.jsonl",f"{OUT}/results/{label}.rows.jsonl")}
    receipt["uploads"][label]=up
    return {"per_language_wer":{k:v["wer"] for k,v in res["per_language"].items()},"rows":res["rows"],
            "results_sha256":sha256_file("outputs/t6-results.json"),"model_sha256":sha256_file("models-active/omniASR-CTC-1B-v2.pt")}
# --- base
os.replace("base.pt","models-active/omniASR-CTC-1B-v2.pt")
receipt["checkpoints"]["base"]=run_eval("base"); log(f"base {receipt['checkpoints']['base']['per_language_wer']}")
# --- each checkpoint: fetch (version-bound) -> sha -> extract -> eval -> upload -> discard
for name,ck in B["checkpoints"].items():
    for p in pathlib.Path("ck").glob("*"): p.unlink()
    for p in pathlib.Path("out").glob("*"): p.unlink()
    ident=fetch(ck["key"],f"ck/{name}",ck["version_id"])
    if ck.get("etag") and ident["etag"]!=ck["etag"]: raise SystemExit(f"REFUSED {name}: etag drift")
    if ck.get("expected_sha256") and ident["sha256"]!=ck["expected_sha256"]:
        raise SystemExit(f"REFUSED {name}: sha256 {ident['sha256'][:12]} != trainer pointer {ck['expected_sha256'][:12]}")
    subprocess.run(["chmod","-R","777","/opt/sweep/out"])
    r=subprocess.run(["docker","run","--rm","--entrypoint","python3","-v","/opt/sweep/ck:/inputs/checkpoints:ro",
        "-v","/opt/sweep/out:/outputs/merged","-v","/opt/sweep/repo:/repo:ro",TRAINIMG,"/repo/scripts/t6_checkpoint_merge.py"],
        capture_output=True,text=True)
    if r.returncode: raise SystemExit(f"EXTRACT FAILED {name}: {r.stderr[-1200:]}")
    merged=list(pathlib.Path("out").glob("merged-step-*.pt"))
    if len(merged)!=1: raise SystemExit(f"{name}: expected one extracted model, got {merged}")
    os.replace(merged[0],"models-active/omniASR-CTC-1B-v2.pt")
    label=name.replace(".pt","")
    entry={"source":ident,"extracted_from":merged[0].name}; entry.update(run_eval(label))
    receipt["checkpoints"][label]=entry; log(f"{label} {entry['per_language_wer']}")
# --- strong identity for the remaining large objects (hash, then discard)
receipt["other_objects"]={}
for name,obj in B["hash_only"].items():
    ident=fetch(obj["key"],"tmp.bin",obj["version_id"]); os.unlink("tmp.bin"); receipt["other_objects"][name]=ident
receipt["finished_utc"]=time.strftime('%FT%TZ', time.gmtime())
json.dump(receipt,open("results/sweep-receipt.json","w"),indent=1,sort_keys=True)
receipt_vid=put_once("results/sweep-receipt.json",f"{OUT}/sweep-receipt.json")
log(f"RECEIPT uploaded VersionId={receipt_vid} sha256={sha256_file('results/sweep-receipt.json')}")
log("ARM1_DEV_SWEEP_DONE")
PY
export BUCKET OUT KMS ACCT REGION BINDINGS_SHA
aws s3api get-object --bucket $BUCKET --key "$BINDINGS_KEY" bindings.json > bindings.meta || { echo BINDINGS-FETCH-FAILED; shutdown -h now; exit 1; }
python3 -c "import json,sys; m=json.load(open('bindings.meta')); sys.exit(0 if m['VersionId']=='$BINDINGS_VID' else 1)" || { echo BINDINGS-VERSION-MISMATCH; shutdown -h now; exit 1; }
[ "$(sha256sum bindings.json | cut -d' ' -f1)" = "$BINDINGS_SHA" ] || { echo BINDINGS-SHA-MISMATCH; shutdown -h now; exit 1; }
python3 driver.py || echo "SWEEP_FAILED rc=$?"
aws s3 cp /var/log/sweep.log "s3://$BUCKET/$OUT/log/sweep.log" --quiet || true
shutdown -h now
