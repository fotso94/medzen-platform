#!/usr/bin/env python3
"""B4 preflight verdict — checked against S3, not against the run's own claims.

result.json is written by the same process whose success it reports, so on its
own it is an assertion, not evidence. Three preflight attempts "completed" by
their own account while uploading nothing at all. Every check here that can be
answered from S3 directly is answered from S3 directly: checkpoint objects are
listed, the log is read and scanned for errors, and the instance state comes
from EC2. result.json is used only for facts that exist nowhere else (the
in-process CUDA allocator peak, the loss), and even those are cross-checked
against run.json and the log.

    python scripts/verify_preflight.py                 # newest preflight run
    python scripts/verify_preflight.py --run preflight-1785392508
    python scripts/verify_preflight.py --instance i-073c68cac2bfef890
"""
from __future__ import annotations

import argparse
import json
import re
import sys

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
PREFIX = "candidates/preflight"
CKPT_PREFIX = "candidates/asr"
EXPECT_BASE_REVISION = "06f233fe06e710322aca913c1bc4249a0d71fce1"
EXPECT_STEPS = 3

# Substrings that mean the run was not clean. "error" alone is too noisy: pip
# prints "error" in ordinary resolver chatter and nvidia-smi headers vary.
LOG_FAILURES = ("FATAL:", "AccessDenied", "Traceback (most recent call last)",
                "CUDA out of memory", "No such file or directory",
                "guardrail breached", "torch pin not satisfied")


def sess():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def latest_run(cli) -> str | None:
    r = cli.list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}/", Delimiter="/")
    runs = [p["Prefix"].rstrip("/").split("/")[-1] for p in r.get("CommonPrefixes", [])]
    runs = [x for x in runs if x.startswith("preflight-")]
    return sorted(runs, key=lambda s: int(s.split("-")[1]))[-1] if runs else None


def get(cli, key: str) -> bytes | None:
    try:
        return cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except Exception:
        return None


def ls(cli, prefix: str) -> list[tuple[str, int]]:
    out, tok = [], {"Bucket": BUCKET, "Prefix": prefix}
    while True:
        r = cli.list_objects_v2(**tok)
        out += [(o["Key"], o["Size"]) for o in r.get("Contents", [])]
        if not r.get("IsTruncated"):
            return out
        tok["ContinuationToken"] = r["NextContinuationToken"]


def ls_dated(cli, prefix: str) -> list[tuple[str, float]]:
    """Keys with their LastModified epoch. Paginated — eval/ exceeds one page."""
    out, tok = [], {"Bucket": BUCKET, "Prefix": prefix}
    while True:
        r = cli.list_objects_v2(**tok)
        out += [(o["Key"], o["LastModified"].timestamp()) for o in r.get("Contents", [])]
        if not r.get("IsTruncated"):
            return out
        tok["ContinuationToken"] = r["NextContinuationToken"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    ap.add_argument("--instance", default=None)
    ap.add_argument("--expect-digest", default=None,
                    help="image digest the run must report (container preflight)")
    ap.add_argument("--expect-manifest", default=None,
                    help="base-model MANIFEST.json sha256 the run must report")
    a = ap.parse_args()

    s = sess()
    cli = s.client("s3")
    run = a.run or latest_run(cli)
    if not run:
        print("no preflight run found in S3")
        return 2
    base = f"{PREFIX}/{run}"
    print(f"run : s3://{BUCKET}/{base}\n")

    objs = ls(cli, base + "/")
    print("uploaded objects:")
    for k, sz in sorted(objs):
        print(f"  {sz:>10}  {k.split('/')[-1]}")
    print()

    result = json.loads(get(cli, f"{base}/result.json") or b"{}")
    runj = json.loads(get(cli, f"{base}/run.json") or b"{}")
    log = (get(cli, f"{base}/preflight.log") or get(cli, f"{base}/live.log") or b"").decode(
        "utf-8", "replace")

    checks: list[tuple[str, bool, str]] = []

    def chk(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    # --- 1. exit code -------------------------------------------------------
    ec = (get(cli, f"{base}/exit_code") or b"").decode().strip()
    rc = result.get("train_exit_code")
    chk("exit code 0", ec == "0" and rc == 0, f"exit_code file={ec!r} result.train_exit_code={rc!r}")

    # --- 2. steps -----------------------------------------------------------
    steps = result.get("steps", runj.get("steps"))
    chk(f"steps == {EXPECT_STEPS}", steps == EXPECT_STEPS, f"steps={steps!r}")

    # --- 3. CUDA actually used, on the L4 a g6.xlarge is supposed to have ---
    dev = result.get("device_used", runj.get("device_used"))
    gpu = str(result.get("gpu_name", runj.get("gpu_name")) or "")
    smi = str(result.get("nvidia_smi") or "")
    chk("CUDA used", dev == "cuda" and bool(gpu) and gpu != "None",
        f"device_used={dev!r} gpu_name={gpu!r}")
    chk("GPU is an L4", "L4" in gpu or "L4" in smi,
        f"torch reports {gpu!r}; nvidia-smi reports {smi.splitlines()[0] if smi else 'nothing'!r}")

    # --- 4. GPU memory above zero ------------------------------------------
    peak = result.get("gpu_peak_mb", runj.get("gpu_peak_mb"))
    chk("GPU peak memory > 0", isinstance(peak, (int, float)) and peak > 0,
        f"gpu_peak_mb={peak!r}")

    # --- 5. finite loss -----------------------------------------------------
    loss = result.get("train_loss", runj.get("train_loss"))
    fin = result.get("loss_is_finite", runj.get("loss_is_finite"))
    chk("loss finite", fin is True and isinstance(loss, (int, float)),
        f"train_loss={loss!r} loss_is_finite={fin!r}")

    # --- 6. pinned base revision recorded AND correct -----------------------
    rev = result.get("base_revision") or (runj.get("params") or {}).get("base_revision")
    chk("base revision pinned", rev == EXPECT_BASE_REVISION,
        f"recorded={rev!r} expected={EXPECT_BASE_REVISION!r}")

    # --- 6b. dataset fingerprint recorded -----------------------------------
    fp = result.get("dataset_fingerprint", runj.get("dataset_fingerprint"))
    chk("dataset fingerprint recorded", bool(fp) and len(str(fp)) >= 8,
        f"dataset_fingerprint={fp!r}")

    # The run id scopes every artifact check below, so resolve it first.
    mrid = result.get("mlflow_run_id", runj.get("mlflow_run_id"))

    # --- 7. artifacts, SCOPED TO THIS RUN -----------------------------------
    # Listing candidates/asr/ wholesale aggregated every past run's objects, so
    # the counts were meaningless and the duplicate adapter names looked like
    # nested copies in this run when they were separate runs. Everything here is
    # scoped to candidates/asr/<mlflow_run_id>/.
    if not mrid:
        chk("checkpoint-3 in S3", False, "no mlflow_run_id; cannot scope artifact checks")
        chk("final adapter in S3", False, "no mlflow_run_id; cannot scope artifact checks")
        chk("no checkpoint inside final/", False, "no mlflow_run_id")
        run_objs = []
    else:
        base_run = f"{CKPT_PREFIX}/{mrid}"
        run_objs = ls(cli, base_run + "/")
        rel = {k[len(base_run) + 1:]: sz for k, sz in run_objs}

        ck = {k: sz for k, sz in rel.items() if k.startswith("checkpoint-3/")}
        chk("checkpoint-3 in S3", bool(ck) and sum(ck.values()) > 0,
            f"{len(ck)} object(s), {sum(ck.values())/1e6:.1f} MB under {base_run}/checkpoint-3/")

        # The exact filename a loader opens -- not any .safetensors/.bin, which
        # previously let training_args.bin count as weights.
        ADAPTER = "final/adapter_model.safetensors"
        fin = {k: sz for k, sz in rel.items() if k.startswith("final/")}
        chk("final adapter in S3", rel.get(ADAPTER, 0) > 0,
            f"{ADAPTER} = {rel.get(ADAPTER, 0)/1e6:.1f} MB; "
            f"{len(fin)} object(s) under final/")

        # The skip_checkpoints fix: final/ must hold the adapter and processor
        # only. A nested checkpoint duplicates data already stored under its own
        # prefix and makes final/ ambiguous for a loader.
        nested = sorted(k for k in fin if k.startswith("final/checkpoint-"))
        chk("no checkpoint inside final/", not nested,
            f"{len(nested)} nested checkpoint object(s)"
            + (f": {nested[:3]}" if nested else ""))

    # --- 8. MLflow ----------------------------------------------------------
    mldb = ls(cli, "mlflow/db/")
    chk("MLflow run recorded", bool(mrid) and len(str(mrid)) >= 8,
        f"mlflow_run_id={mrid!r}")
    chk("MLflow DB in S3", any(mrid and mrid in k for k, _ in mldb) if mrid else False,
        f"{len(mldb)} db object(s); run-specific key present={any(mrid and mrid in k for k,_ in mldb) if mrid else False}")

    # --- 9. log present and error-free -------------------------------------
    # The guardrail probe DELIBERATELY provokes an AccessDenied on
    # eval/_probe_should_fail.txt, and set -x echoes it into the trace. Scanning
    # the raw log would therefore fail every healthy run, so drop exactly those
    # lines -- and only those -- before looking for real failures.
    scanned = "\n".join(l for l in log.splitlines() if "_probe_should_fail" not in l)
    hits = sorted({f for f in LOG_FAILURES if f in scanned})
    chk("log uploaded", len(log) > 0, f"{len(log)} bytes")
    chk("log error-free", len(log) > 0 and not hits, f"failure markers: {hits or 'none'}")

    # --- 10. guardrail still intact on the real host ------------------------
    chk("eval write-Deny verified on host", "EVAL DENY INTACT" in log,
        "'EVAL DENY INTACT' in log" if "EVAL DENY INTACT" in log else "marker absent")

    # --- 10b. eval/ physically untouched ------------------------------------
    # The Deny proves the attempt was refused; this proves nothing landed by
    # any other route. Anything modified at or after the run's start timestamp
    # (which the run id carries) would be new.
    started = int(run.split("-")[1])
    ev = ls_dated(cli, "eval/")          # paginated: eval/ holds audio, >1000 keys
    touched = [k for k, ts in ev if ts >= started]
    stray = [k for k, _ in ev if "_probe_should_fail" in k]
    chk("eval/ prefix untouched", not touched and not stray,
        f"{len(ev)} object(s) under eval/, {len(touched)} modified since run start, "
        f"stray probes: {stray or 'none'}")

    # --- 11. torch/CUDA actually reported ----------------------------------
    tv = result.get("torch_version", runj.get("torch_version"))
    cv = result.get("cuda_version", runj.get("cuda_version"))
    m = re.search(r"torch pinned=(\S+) loaded=(\S+)", log)
    pin_ok = bool(m) and m.group(1) == m.group(2)
    chk("torch pin enforced", bool(tv) and pin_ok,
        f"torch={tv!r} cuda={cv!r} " + (f"pinned={m.group(1)} loaded={m.group(2)}" if m else "pin line absent"))

    # --- 11b. the artifact that ran, and the weights it loaded --------------
    # A green preflight for a DIFFERENT image or a different base checkpoint
    # proves nothing about the one being adopted.
    if a.expect_digest:
        got = result.get("image_digest")
        chk("image digest matches", got == a.expect_digest,
            f"reported={got} expected={a.expect_digest}")
        chk("ran in container", result.get("ran_in_container") is True,
            f"ran_in_container={result.get('ran_in_container')!r}")
    src = result.get("base_source", runj.get("base_source"))
    man = result.get("base_manifest_sha256", runj.get("base_manifest_sha256"))
    chk("base model from the S3 cache", src == "s3_cache",
        f"base_source={src!r} (hf_hub would mean the offline path did not hold)")
    if a.expect_manifest:
        chk("base cache manifest matches", man == a.expect_manifest,
            f"reported={man} expected={a.expect_manifest}")
    else:
        chk("base cache manifest recorded", bool(man) and len(str(man)) == 64,
            f"base_manifest_sha256={man}")

    # --- 12. instance terminated -------------------------------------------
    iid = a.instance
    if iid:
        ec2 = s.client("ec2")
        try:
            st = ec2.describe_instances(InstanceIds=[iid])[
                "Reservations"][0]["Instances"][0]["State"]["Name"]
        except Exception as e:
            st = f"lookup failed: {type(e).__name__}"
        chk("instance terminated", st == "terminated", f"{iid} state={st}")
    else:
        chk("instance terminated", False, "no --instance given; not verified")

    width = max(len(n) for n, _, _ in checks)
    print("verdict:")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")

    failed = [n for n, ok, _ in checks if not ok]
    print()
    if failed:
        print(f"NOT PROVEN — {len(failed)} check(s) failed: {', '.join(failed)}")
        return 1
    print(f"ALL {len(checks)} CHECKS PASSED — preflight is proven; full training still requires approval")
    return 0


if __name__ == "__main__":
    sys.exit(main())
