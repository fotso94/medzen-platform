#!/usr/bin/env python3
"""B2 integration check — does the corpus in S3 match what the manifest claims?

The A3 validator checks the manifest against itself. This checks the manifest
against REALITY: that every referenced object exists, that its bytes hash to
the recorded checksum, and that the audio really is 16 kHz mono of the recorded
duration. A manifest can be perfectly valid and still describe a corpus that
was never uploaded — which is exactly the defect this catches.

    python scripts/verify_corpus.py --language akan --task asr
    python scripts/verify_corpus.py --language akan --task asr --sample 50 --deep
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import random
import sys

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
TOL_S = 0.05          # duration tolerance vs manifest


def client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def key_of(uri: str) -> str:
    return uri.split(f"{BUCKET}/", 1)[1]


def check_row(cli, rec: dict, deep: bool) -> list[str]:
    """Return a list of problems; empty means the row is sound."""
    bad: list[str] = []
    cur_uri, raw_uri = rec["audio_filepath"], rec.get("raw_filepath")

    # ---- curated object exists -------------------------------------------
    try:
        head = cli.head_object(Bucket=BUCKET, Key=key_of(cur_uri))
    except Exception as e:
        return [f"curated MISSING {cur_uri} ({type(e).__name__})"]
    if head["ContentLength"] == 0:
        bad.append(f"curated EMPTY {cur_uri}")

    # ---- raw object exists (provenance) ----------------------------------
    if raw_uri:
        try:
            cli.head_object(Bucket=BUCKET, Key=key_of(raw_uri))
        except Exception:
            bad.append(f"raw MISSING {raw_uri}")
    else:
        bad.append("no raw_filepath — original artifact not retained")
    if not rec.get("raw_checksum_sha256"):
        bad.append("no raw_checksum_sha256 — original bytes unverifiable")

    if not deep:
        return bad

    # ---- checksum + real audio properties --------------------------------
    body = cli.get_object(Bucket=BUCKET, Key=key_of(cur_uri))["Body"].read()
    got = hashlib.sha256(body).hexdigest()
    if got != rec["audio_checksum_sha256"]:
        bad.append(f"CHECKSUM MISMATCH {cur_uri}: manifest={rec['audio_checksum_sha256'][:12]} "
                   f"actual={got[:12]}")

    # verify the RAW bytes too — provenance you cannot check is not provenance
    if raw_uri and rec.get("raw_checksum_sha256"):
        raw = cli.get_object(Bucket=BUCKET, Key=key_of(raw_uri))["Body"].read()
        rgot = hashlib.sha256(raw).hexdigest()
        if rgot != rec["raw_checksum_sha256"]:
            bad.append(f"RAW CHECKSUM MISMATCH {raw_uri}: "
                       f"manifest={rec['raw_checksum_sha256'][:12]} actual={rgot[:12]}")

    import soundfile as sf
    try:
        info = sf.info(io.BytesIO(body))
    except Exception as e:
        return bad + [f"UNREADABLE audio {cur_uri}: {e}"]

    if info.samplerate != rec["sample_rate"]:
        bad.append(f"SAMPLE RATE {cur_uri}: file={info.samplerate} manifest={rec['sample_rate']}")
    if info.channels != rec["channels"]:
        bad.append(f"CHANNELS {cur_uri}: file={info.channels} manifest={rec['channels']}")
    if abs(info.duration - rec["duration_s"]) > TOL_S:
        bad.append(f"DURATION {cur_uri}: file={info.duration:.3f}s "
                   f"manifest={rec['duration_s']:.3f}s")
    if not (1.0 <= info.duration <= 30.0):
        bad.append(f"DURATION out of A3 range {cur_uri}: {info.duration:.2f}s")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language", required=True)
    ap.add_argument("--task", required=True, choices=["asr", "tts"])
    ap.add_argument("--config", default=None, help="waxal config dir; auto-detected if omitted")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--sample", type=int, default=40,
                    help="rows to deep-check; 0 = all")
    ap.add_argument("--deep", action="store_true",
                    help="download and verify checksum + audio format")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()

    cli = client()
    base = f"curated/{a.language}/{a.task}"
    if a.config:
        prefix = f"{base}/{a.config}/{a.version}"
    else:
        r = cli.list_objects_v2(Bucket=BUCKET, Prefix=base + "/", Delimiter="/")
        cfgs = [p["Prefix"].rstrip("/").split("/")[-1] for p in r.get("CommonPrefixes", [])]
        if len(cfgs) != 1:
            print(f"specify --config; found {cfgs}"); return 2
        prefix = f"{base}/{cfgs[0]}/{a.version}"

    try:
        body = cli.get_object(Bucket=BUCKET, Key=f"{prefix}/manifest.jsonl")["Body"].read()
    except Exception as e:
        print(f"no manifest at s3://{BUCKET}/{prefix}/manifest.jsonl ({type(e).__name__})")
        return 1
    rows = [json.loads(l) for l in body.decode().splitlines() if l.strip()]
    print(f"manifest : s3://{BUCKET}/{prefix}/manifest.jsonl  ({len(rows)} rows)")

    # existence is checked on EVERY row — it is cheap and it is the defect
    # that makes a corpus untrainable
    print(f"\nexistence check: all {len(rows)} rows")
    problems: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for bad in ex.map(lambda r: check_row(cli, r, False), rows):
            problems.extend(bad)
    print(f"  {len(rows) - len({p.split()[-1] for p in problems})}/{len(rows)} objects present")

    if a.deep:
        pool = rows if a.sample == 0 else random.sample(rows, min(a.sample, len(rows)))
        print(f"\ndeep check: {len(pool)} rows (checksum + format + duration)")
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            for bad in ex.map(lambda r: check_row(cli, r, True), pool):
                problems.extend(bad)

    # total bytes actually stored
    tot = n = 0
    tok = {"Bucket": BUCKET, "Prefix": f"{prefix}/audio/"}
    while True:
        r = cli.list_objects_v2(**tok)
        for o in r.get("Contents", []):
            tot += o["Size"]; n += 1
        if not r.get("IsTruncated"):
            break
        tok["ContinuationToken"] = r["NextContinuationToken"]
    print(f"\naudio in curated/: {n} objects, {tot/1e6:.1f} MB")

    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems[:25]:
            print(f"  {p}")
        if len(problems) > 25:
            print(f"  ... {len(problems) - 25} more")
        return 1
    print("\nOK — manifest matches S3: every object exists, "
          f"{'checksums and audio properties verified on the sample' if a.deep else 'run --deep to verify bytes'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
