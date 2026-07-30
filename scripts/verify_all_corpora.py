#!/usr/bin/env python3
"""Verify EVERY curated corpus and write a durable record.

verify_corpus.py checks one language/task at a time and prints to a terminal.
That was run during B2, but nothing was persisted, so there was no evidence that
the corpus training will read is intact -- only a memory that it had been
checked once. This driver runs every corpus and stores the result in S3 and in
the repo, so the claim is auditable later.

Why it matters here specifically: a training preflight proves almost nothing
about the corpus. Audio loading is lazy, so a 3-step run touches ~48 files out
of 4,619. The first epoch of a real run is the first time every object is read,
and discovering a missing or corrupt object there wastes GPU hours.

Two tiers, reported separately rather than blurred:

  existence  every row: the curated object exists, is non-empty, and its size
             matches what its duration/sample-rate/channels imply; the raw
             artifact exists and a raw checksum is recorded. Metadata only, so
             cheap enough to run over everything.
  deep       downloads and hashes BOTH the curated audio and the retained raw
             artifact against their recorded sha256, and checks sample rate,
             channels and duration. --deep-all covers every row; --deep-sample N
             covers a random N per corpus.

    python scripts/verify_all_corpora.py                    # existence, all rows
    python scripts/verify_all_corpora.py --deep-sample 40   # + 40/corpus deep
    python scripts/verify_all_corpora.py --deep-all         # + every byte
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import random
import sys
import time

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
TOL_S = 0.05


def client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def key_of(uri: str) -> str:
    return uri.split(f"{BUCKET}/", 1)[1]


def manifests(cli) -> list[str]:
    keys, tok = [], {"Bucket": BUCKET, "Prefix": "curated/"}
    while True:
        r = cli.list_objects_v2(**tok)
        keys += [o["Key"] for o in r.get("Contents", [])
                 if o["Key"].endswith("manifest.jsonl")]
        if not r.get("IsTruncated"):
            return sorted(keys)
        tok["ContinuationToken"] = r["NextContinuationToken"]


def expected_bytes(rec: dict) -> int:
    """Bytes a 16-bit PCM wav of this duration must have.

    The manifests carry no audio_bytes field, so an earlier version of this
    check compared against rec.get("audio_bytes") and therefore never ran at
    all -- a claim that looked like a size check and was not one. The size IS
    derivable: duration x sample_rate x channels x 2, plus a header.
    """
    return int(rec["duration_s"] * rec["sample_rate"] * rec["channels"] * 2) + 44


# duration_s is stored to 2dp, so +/-0.005s is +/-160 bytes at 16 kHz mono, and
# wav headers vary with optional chunks. 4 KiB absorbs both; a truncated file is
# wrong by far more.
SIZE_TOL_BYTES = 4096


def check_existence(cli, rec: dict) -> list[str]:
    bad = []
    cur = rec["audio_filepath"]
    try:
        head = cli.head_object(Bucket=BUCKET, Key=key_of(cur))
    except Exception as e:
        return [f"curated MISSING {cur} ({type(e).__name__})"]
    got = head["ContentLength"]
    if got == 0:
        bad.append(f"curated EMPTY {cur}")
    else:
        want = expected_bytes(rec)
        if abs(got - want) > max(SIZE_TOL_BYTES, int(want * 0.02)):
            bad.append(f"SIZE {cur}: s3={got} expected~{want} "
                       f"({rec['duration_s']}s @ {rec['sample_rate']}Hz x{rec['channels']})")
    raw = rec.get("raw_filepath")
    if not raw:
        bad.append(f"no raw_filepath {cur} — original artifact not retained")
    else:
        try:
            cli.head_object(Bucket=BUCKET, Key=key_of(raw))
        except Exception:
            bad.append(f"raw MISSING {raw}")
    if not rec.get("raw_checksum_sha256"):
        bad.append(f"no raw_checksum_sha256 {cur} — original bytes unverifiable")
    return bad


def check_deep(cli, rec: dict) -> list[str]:
    """Hash the curated bytes AND the retained raw artifact.

    An earlier version hashed only the curated audio, which made this weaker
    than verify_corpus.py: provenance you never verify is not provenance, and a
    raw artifact that has rotted is discovered when someone tries to re-derive
    the corpus from it, which is the worst possible moment.
    """
    bad = []
    cur = rec["audio_filepath"]
    try:
        body = cli.get_object(Bucket=BUCKET, Key=key_of(cur))["Body"].read()
    except Exception as e:
        return [f"UNREADABLE {cur} ({type(e).__name__})"]
    got = hashlib.sha256(body).hexdigest()
    if got != rec["audio_checksum_sha256"]:
        bad.append(f"CHECKSUM {cur}: got {got[:12]} manifest {rec['audio_checksum_sha256'][:12]}")

    raw, raw_sha = rec.get("raw_filepath"), rec.get("raw_checksum_sha256")
    if raw and raw_sha:
        try:
            rb = cli.get_object(Bucket=BUCKET, Key=key_of(raw))["Body"].read()
            rgot = hashlib.sha256(rb).hexdigest()
            if rgot != raw_sha:
                bad.append(f"RAW CHECKSUM {raw}: got {rgot[:12]} manifest {raw_sha[:12]}")
        except Exception as e:
            bad.append(f"RAW UNREADABLE {raw} ({type(e).__name__})")
    import soundfile as sf
    try:
        info = sf.info(io.BytesIO(body))
    except Exception as e:
        return bad + [f"UNPARSEABLE {cur}: {e}"]
    if info.samplerate != rec["sample_rate"]:
        bad.append(f"SAMPLE RATE {cur}: {info.samplerate} != {rec['sample_rate']}")
    if info.channels != rec["channels"]:
        bad.append(f"CHANNELS {cur}: {info.channels} != {rec['channels']}")
    if abs(info.duration - rec["duration_s"]) > TOL_S:
        bad.append(f"DURATION {cur}: {info.duration:.3f}s != {rec['duration_s']:.3f}s")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep-sample", type=int, default=0,
                    help="deep-check N random rows per corpus")
    ap.add_argument("--deep-all", action="store_true",
                    help="deep-check EVERY row (downloads the whole corpus)")
    ap.add_argument("--split", default="train",
                    help="which split to deep-check: train, test, or all")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--out", default="/tmp/corpus_verification.json")
    ap.add_argument("--upload", action="store_true",
                    help="store the record in s3://medzen-speech/evidence/corpus/")
    a = ap.parse_args()

    cli = client()
    keys = manifests(cli)
    print(f"corpora: {len(keys)} manifests\n")

    record = {
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tiers": {
            "existence": ("every row: curated object present, non-empty, size consistent with "
                          "duration x rate x channels; raw artifact present; raw checksum recorded"),
            "deep_covers": "curated sha256 + RAW sha256 + sample rate, channels, duration",
            "deep": ("every row" if a.deep_all else
                     (f"{a.deep_sample} random rows per corpus" if a.deep_sample else "not run")),
        },
        "deep_split": a.split if (a.deep_all or a.deep_sample) else None,
        "corpora": {},
        "totals": {},
    }
    grand = {"rows": 0, "existence_checked": 0, "deep_checked": 0, "problems": 0}
    all_problems: list[str] = []

    for key in keys:
        _, lang, task, cfg, ver, _ = key.split("/")
        name = f"{lang}/{task}/{cfg}/{ver}"
        rows = [json.loads(l) for l in
                cli.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode().splitlines()
                if l.strip()]
        by_split = {}
        for r in rows:
            by_split.setdefault(r["split"], []).append(r)

        probs: list[str] = []
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            for bad in ex.map(lambda r: check_existence(cli, r), rows):
                probs.extend(bad)

        pool = []
        if a.deep_all or a.deep_sample:
            cand = rows if a.split == "all" else by_split.get(a.split, [])
            pool = cand if a.deep_all else random.sample(cand, min(a.deep_sample, len(cand)))
            with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
                for bad in ex.map(lambda r: check_deep(cli, r), pool):
                    probs.extend(bad)

        record["corpora"][name] = {
            "manifest": f"s3://{BUCKET}/{key}",
            "rows": len(rows),
            "splits": {k: len(v) for k, v in sorted(by_split.items())},
            "existence_checked": len(rows),
            "deep_checked": len(pool),
            "problems": probs[:20],
            "problem_count": len(probs),
            "ok": not probs,
        }
        grand["rows"] += len(rows)
        grand["existence_checked"] += len(rows)
        grand["deep_checked"] += len(pool)
        grand["problems"] += len(probs)
        all_problems += probs
        flag = "OK  " if not probs else "FAIL"
        print(f"  [{flag}] {name:<34} {len(rows):>5} rows  "
              f"existence {len(rows):>5}  deep {len(pool):>5}"
              + (f"  {len(probs)} PROBLEM(S)" if probs else ""))

    record["totals"] = grand
    record["ok"] = grand["problems"] == 0
    import pathlib
    pathlib.Path(a.out).write_text(json.dumps(record, indent=2) + "\n")
    print(f"\ntotals: {grand['rows']} rows, existence {grand['existence_checked']}, "
          f"deep {grand['deep_checked']}, problems {grand['problems']}")
    print(f"record: {a.out}")

    if all_problems:
        print(f"\n{len(all_problems)} PROBLEM(S), first 20:")
        for p in all_problems[:20]:
            print(f"  {p}")

    if a.upload:
        k = f"evidence/corpus/verification-{record['recorded_utc'].replace(':', '')}.json"
        cli.put_object(Bucket=BUCKET, Key=k,
                       Body=(json.dumps(record, indent=2) + "\n").encode())
        print(f"\nstored s3://{BUCKET}/{k}")

    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
