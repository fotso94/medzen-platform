#!/usr/bin/env python3
"""B2.3 — ingest a source into the A3 zones, audio included.

    adapter -> raw/ (original bytes)
            -> curated/{lang}/{task}/v1/audio/*.wav   (16 kHz mono PCM_16)
            -> manifest referencing the CURATED objects
            -> validate -> curated/.../manifest.jsonl + frozen eval/

A manifest whose audio was never uploaded is not a corpus. Both forms are
stored: raw/ so a curated object can be re-derived and audited, curated/ because
that is what training reads.

    python -m pipeline.ingest --source waxalnlp --language akan --task asr --limit 300
    python -m pipeline.ingest --source waxalnlp --language akan --dry-run
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_BUCKET = "medzen-speech"
MIN_CLUSTERS = 8      # below this, no meaningful text-disjoint split exists
PROFILE = "medzen"
REGION = "eu-central-1"


def s3_client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def prefix_exists(cli, prefix: str) -> bool:
    r = cli.list_objects_v2(Bucket=DATA_BUCKET, Prefix=prefix, MaxKeys=1)
    return r.get("KeyCount", 0) > 0


def build_adapter(source: str, language: str, task: str | None = None,
                  version: str = "v1"):
    if source == "waxalnlp":
        from .adapters.waxalnlp import WaxalNLPAdapter
        return WaxalNLPAdapter(language, task=task, version=version)
    # green-bucket-aggregation-2026-08 sources (see registry/data_sources/)
    if source == "meta_omnilingual":
        from .adapters.meta_omnilingual import MetaOmnilingualAdapter
        return MetaOmnilingualAdapter(language, task=task, version=version)
    if source == "fleurs":
        from .adapters.fleurs import FleursAdapter
        return FleursAdapter(language, task=task, version=version)
    if source == "common_voice":
        from .adapters.common_voice import CommonVoiceAdapter
        return CommonVoiceAdapter(language, task=task, version=version)
    if source == "kallaama":
        from .adapters.kallaama import KallaamaAdapter
        return KallaamaAdapter(language, task=task, version=version)
    raise SystemExit(f"unknown source '{source}'")


def assign_splits(rows: list[dict], test_frac: float = 0.15) -> None:
    """Split on whatever the corpus must generalise to.

    ASR -> unseen SPEAKERS. TTS -> unseen TEXT: parallel TTS corpora have every
    speaker reading the same script, so a speaker split leaves identical
    sentences on both sides and means nothing.
    """
    strategy = rows[0].get("split_strategy", "speaker_disjoint")

    if strategy == "text_disjoint":
        # Hashing EXACT text is not enough: sentences at Jaccard 0.8-0.95 are
        # different strings, hash independently, and land on opposite sides -
        # which the near-dup validator then correctly rejects (seen on
        # acholi/tts). Cluster near-duplicates first; move whole clusters.
        sys.path.insert(0, str(ROOT / "scripts"))
        from validate_manifest import near_duplicate_pairs

        texts = sorted({r["text_normalized"] for r in rows})
        idx = {t: i for i, t in enumerate(texts)}
        parent = list(range(len(texts)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b, _ in near_duplicate_pairs([{"text_normalized": t} for t in texts]):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        clusters: dict[int, list[str]] = {}
        for t in texts:
            clusters.setdefault(find(idx[t]), []).append(t)

        # RANK clusters by hash and take the first k, rather than thresholding
        # each hash independently. Thresholding is unbiased only for many
        # clusters; with a few dozen it frequently draws 0 (observed: 16
        # clusters, 0 selected). Ranking is equally deterministic but
        # guarantees a non-empty, correctly-proportioned test split.
        ranked = sorted(clusters.values(),
                        key=lambda m: hashlib.sha256(min(m).encode()).hexdigest())
        k = max(1, round(len(ranked) * test_frac))
        held: set[str] = {t for m in ranked[:k] for t in m}

        for r in rows:
            r["split"] = "test" if r["text_normalized"] in held else "train"
        n_test = sum(1 for r in rows if r["split"] == "test")
        merged = sum(1 for m in clusters.values() if len(m) > 1)
        print(f"  split by TEXT CLUSTER: {len(clusters)} clusters / {len(texts)} "
              f"sentences ({merged} near-dup clusters) -> {n_test} test rows")

        # Degenerate case: a heavily templated corpus (e.g. number/arithmetic
        # prompts) collapses into very few clusters, so no split can be both
        # non-empty and text-disjoint. Clustering is right; the corpus simply
        # cannot supply an eval set. Say so rather than emit a lopsided split
        # that the validator would reject anyway.
        if n_test == 0 or n_test == len(rows) or len(clusters) < MIN_CLUSTERS:
            print(f"  WARNING: {len(clusters)} distinct text cluster(s) is too few for a "
                  f"text-disjoint split (need >= {MIN_CLUSTERS}). This corpus is heavily "
                  f"templated. All rows tagged 'train'; it CANNOT supply an eval set "
                  f"on its own — pair it with a separate held-out source.")
            for r in rows:
                r["split"] = "train"
        return

    speakers = sorted({r["speaker_id"] for r in rows})
    if len(speakers) < 2:
        print(f"  WARNING: only {len(speakers)} speaker(s) — a speaker-disjoint "
              f"test split is impossible. All rows 'train'; this source cannot "
              f"supply an eval set on its own.")
        for r in rows:
            r["split"] = "train"
        return
    n_test = max(1, round(len(speakers) * test_frac))
    held = set(speakers[-n_test:])
    for r in rows:
        r["split"] = "test" if r["speaker_id"] in held else "train"
    print(f"  split by SPEAKER: {len(speakers) - len(held)} train / {len(held)} test")


def upload_all(cli, items: list[dict], workers: int = 16) -> None:
    def put(it):
        rec = it["record"]
        raw_key = rec["raw_filepath"].split(f"{DATA_BUCKET}/", 1)[1]
        cur_key = rec["audio_filepath"].split(f"{DATA_BUCKET}/", 1)[1]
        with open(it["raw_path"], "rb") as fh:
            cli.put_object(Bucket=DATA_BUCKET, Key=raw_key, Body=fh,
                           ContentType=f"audio/{it['raw_ext']}")
        with open(it["wav_path"], "rb") as fh:
            cli.put_object(Bucket=DATA_BUCKET, Key=cur_key, Body=fh,
                           ContentType="audio/wav")

    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(put, items):
            done += 1
            if done % 100 == 0:
                print(f"    uploaded {done}/{len(items)} pairs", flush=True)
    print(f"  uploaded {done} raw + {done} curated audio objects")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--task", default=None, choices=["asr", "tts"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-eval", action="store_true",
                    help="allow replacing an existing frozen eval version")
    ap.add_argument("--no-eval-split", action="store_true",
                    help="all rows -> curated/ (train); carve no eval split. Use "
                         "when eval is supplied by a separate source (e.g. FLEURS), "
                         "so multiple training sources per language do not collide "
                         "on the shared eval/ prefix.")
    a = ap.parse_args()

    adapter = build_adapter(a.source, a.language, a.task, a.version)
    base = f"{a.language}/{adapter.task}/{adapter.config}"
    cur_prefix = f"curated/{base}/{a.version}"
    eval_prefix = f"eval/{a.language}/{adapter.task}/{a.version}"

    print(f"source   {adapter.spec.source_id}")
    print(f"release  {adapter.spec.dataset_release}")
    print(f"licence  {adapter.spec.license_policy}   use={adapter.spec.allowed_use}")

    cli = None if a.dry_run else s3_client()

    # ---- eval immutability guard ------------------------------------------
    # An eval set is a measurement instrument. Silently replacing one makes
    # every previously reported number incomparable and unreproducible.
    if (cli and not a.no_eval_split and not a.force_eval
            and prefix_exists(cli, eval_prefix + "/")):
        print(f"\nREFUSING: s3://{DATA_BUCKET}/{eval_prefix}/ already exists.")
        print("An eval version is frozen once written. Use a new --version, or")
        print("--force-eval only if you accept invalidating prior results.")
        return 1

    items = list(adapter.items(limit=a.limit))
    if not items:
        print("no usable rows produced"); return 1
    rows = [it["record"] for it in items]
    print(f"\nbuilt {len(rows)} records")
    eval_only = getattr(adapter, "tier", None) == "eval_only"
    if eval_only:
        # A whole-source frozen benchmark (e.g. FLEURS): every row is held out,
        # nothing enters training. No speaker/text split is carved, and only an
        # eval/ manifest is written (never curated/).
        for r in rows:
            r["split"] = "test"
        print(f"  eval-only source -> all {len(rows)} rows frozen under {eval_prefix}/")
    elif a.no_eval_split:
        for r in rows:
            r["split"] = "train"
        print(f"  all-train: {len(rows)} rows -> {cur_prefix}/ "
              f"(no eval split; eval comes from a separate source)")
    else:
        assign_splits(rows)

    with tempfile.TemporaryDirectory() as td:
        mpath = Path(td) / "manifest.jsonl"
        mpath.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        print("\nvalidating against A3...")
        v = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_manifest.py"), str(mpath),
             "--registry", str(ROOT / "registry" / "languages")],
            capture_output=True, text=True)
        print("\n".join("  " + l for l in v.stdout.strip().splitlines()[-5:]))
        if v.returncode != 0:
            print("\nREJECTED — nothing uploaded.")
            return 1

        if a.dry_run:
            print(f"\ndry run: would upload {len(items)} raw + {len(items)} curated objects")
            return 0

        print(f"\nuploading audio...")
        upload_all(cli, items)

        if eval_only:
            body = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
            cli.put_object(Bucket=DATA_BUCKET, Key=f"{eval_prefix}/manifest.jsonl",
                           Body=body, ContentType="application/x-ndjson")
            print(f"  wrote s3://{DATA_BUCKET}/{eval_prefix}/manifest.jsonl "
                  f"({len(rows)} rows, FROZEN eval — no curated/ written)")
            return 0

        cli.put_object(Bucket=DATA_BUCKET, Key=f"{cur_prefix}/manifest.jsonl",
                       Body=mpath.read_bytes(), ContentType="application/x-ndjson")
        print(f"  wrote s3://{DATA_BUCKET}/{cur_prefix}/manifest.jsonl")

        test_rows = [r for r in rows if r["split"] == "test"]
        if test_rows:
            body = ("\n".join(json.dumps(r) for r in test_rows) + "\n").encode()
            cli.put_object(Bucket=DATA_BUCKET, Key=f"{eval_prefix}/manifest.jsonl",
                           Body=body, ContentType="application/x-ndjson")
            print(f"  wrote s3://{DATA_BUCKET}/{eval_prefix}/manifest.jsonl "
                  f"({len(test_rows)} rows, FROZEN)")
        else:
            print("  no test split — eval set not written")
    return 0


if __name__ == "__main__":
    # No os._exit(): the local-shard reader owns no background threads, so a
    # normal exit must work. If this ever hangs again, that is a real resource
    # leak to find — not something to mask with a hard exit.
    sys.exit(main())
