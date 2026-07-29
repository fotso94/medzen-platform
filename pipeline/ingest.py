#!/usr/bin/env python3
"""B2.3 — ingest a source into the A3 zones.

    adapter -> decode/resample -> raw/ (audio) -> manifest -> validate -> curated/

Nothing reaches curated/ that has not passed scripts/validate_manifest.py.
A rejected manifest leaves the zone untouched: a partial corpus is worse than
no corpus, because it looks complete.

    python -m pipeline.ingest --source waxalnlp --language pidgin --limit 200
    python -m pipeline.ingest --source waxalnlp --language pidgin --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"


def s3(*args: str) -> None:
    subprocess.run(["aws", "--profile", PROFILE, "--region", REGION, "s3", *args],
                   check=True, capture_output=True)


def build_adapter(source: str, language: str):
    if source == "waxalnlp":
        from .adapters.waxalnlp import WaxalNLPAdapter
        return WaxalNLPAdapter(language)
    raise SystemExit(f"unknown source '{source}'")


def assign_splits(rows: list[dict], test_frac: float = 0.15) -> None:
    """Split on whatever the corpus must generalise to.

    ASR  -> unseen SPEAKERS, so whole speakers move together.
    TTS  -> unseen TEXT. Parallel TTS corpora have every speaker reading the
            same script, so a speaker split leaves identical sentences on both
            sides. Splitting by text is the only split that means anything.
    """
    strategy = rows[0].get("split_strategy", "speaker_disjoint")

    if strategy == "text_disjoint":
        import hashlib
        texts = sorted({r["text_normalized"] for r in rows})
        h = {t: int(hashlib.sha256(t.encode()).hexdigest()[:8], 16) for t in texts}
        cutoff = int(0xFFFFFFFF * test_frac)
        test_texts = {t for t in texts if h[t] < cutoff}
        for r in rows:
            r["split"] = "test" if r["text_normalized"] in test_texts else "train"
        n = sum(1 for r in rows if r["split"] == "test")
        print(f"  split by TEXT (parallel TTS corpus): "
              f"{len(test_texts)}/{len(texts)} sentences held out -> {n} rows")
        return

    speakers = sorted({r["speaker_id"] for r in rows})
    if len(speakers) < 2:
        print(f"  WARNING: only {len(speakers)} speaker(s) — a speaker-disjoint "
              f"test split is impossible. All rows tagged 'train'; this source "
              f"cannot supply an eval set on its own.")
        for r in rows:
            r["split"] = "train"
        return
    n_test = max(1, round(len(speakers) * test_frac))
    test = set(speakers[-n_test:])
    for r in rows:
        r["split"] = "test" if r["speaker_id"] in test else "train"
    print(f"  split by SPEAKER: train={sorted(set(speakers) - test)} test={sorted(test)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--dry-run", action="store_true", help="build + validate, write nothing")
    a = ap.parse_args()

    import librosa, numpy as np, soundfile as sf  # noqa: E401
    from datasets import Audio, load_dataset  # noqa: F401

    adapter = build_adapter(a.source, a.language)
    print(f"source   {adapter.spec.source_id}")
    print(f"release  {adapter.spec.dataset_release}")
    print(f"licence  {adapter.spec.license_policy}   use={adapter.spec.allowed_use}")

    rows = list(adapter.rows(limit=a.limit))
    if not rows:
        print("no rows produced"); return 1
    print(f"\nbuilt {len(rows)} records")
    assign_splits(rows)

    with tempfile.TemporaryDirectory() as td:
        mpath = Path(td) / "manifest.jsonl"
        mpath.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        print("\nvalidating against A3...")
        v = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_manifest.py"), str(mpath),
             "--registry", str(ROOT / "registry" / "languages")],
            capture_output=True, text=True)
        print("\n".join("  " + l for l in v.stdout.strip().splitlines()[-6:]))
        if v.returncode != 0:
            print("\nREJECTED — curated/ untouched.")
            return 1

        if a.dry_run:
            print("\ndry run: nothing written")
            return 0

        prefix = f"curated/{a.language}/{a.version}"
        s3("cp", str(mpath), f"s3://{DATA_BUCKET}/{prefix}/manifest.jsonl")
        print(f"\nwrote s3://{DATA_BUCKET}/{prefix}/manifest.jsonl")

        # eval/ is written ONCE and is write-denied to the trainer role
        test_rows = [r for r in rows if r["split"] == "test"]
        if test_rows:
            epath = Path(td) / "eval.jsonl"
            epath.write_text("\n".join(json.dumps(r) for r in test_rows) + "\n")
            s3("cp", str(epath), f"s3://{DATA_BUCKET}/eval/{a.language}/{a.version}/manifest.jsonl")
            print(f"wrote s3://{DATA_BUCKET}/eval/{a.language}/{a.version}/manifest.jsonl "
                  f"({len(test_rows)} rows, FROZEN)")
        else:
            print("no test split — eval set not written (see warning above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
