#!/usr/bin/env python3
"""Pidgin held-out curation — the committed, reproducible procedure
(Codex reviews #18 + #19).

ORIGINAL CARVE (2026-08-21, recorded here for the evidence trail):
from curated/pidgin/asr/av_pcm/v1/manifest.jsonl (331,333 rows / 737
speakers, sha b95bc789...):
  1. speaker order: sha256("pidgin-holdout-2026-08-21" + speaker_id),
     ascending — a salted deterministic shuffle nobody can steer toward
     or away from particular speakers after seeing the data;
  2. first 50 speakers in that order (51.1 h) became the held-out pool;
     the remaining 687 speakers' rows became train-v2 after a cap of 3
     rows per normalized text (sha 436c1c58..., adopted as gb8 pidgin);
  3. held-out rows were split dev/sealed by the same salted hash parity
     and truncated to 1,500 rows each (dev 25 speakers / sealed 24).

FINDING #19-1: those carved rows carried split=train +
allowed_use=["asr_train"] verbatim into the eval keys. The `relabel`
command below produces the EVALUATION-ONLY successor objects (…-e1
keys) from the hash-bound predecessors: split=test,
allowed_use=["asr_eval"], every row revalidated by
validate_eval_manifest before a byte is uploaded. Predecessor objects
are left in place, immutable, superseded in the ledger — never
overwritten.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_eval_manifest import validate_rows  # noqa: E402

PREDECESSORS = {
    "dev": ("eval/pidgin/asr/av-heldout-dev/manifest.jsonl",
            "d88fd26141f3fd4502d9e85ac6ab9c019147d7b2f512c29283568a7c5b987c62"),
    "sealed": ("eval/pidgin/asr/av-heldout-sealed/manifest.jsonl",
               "c52af8c7e8bb53b92f4d3fed48fb908c52288144581f5a2138b114b82293eba8"),
}
SUCCESSOR_KEYS = {
    "dev": "eval/pidgin/asr/av-heldout-dev-e1/manifest.jsonl",
    "sealed": "eval/pidgin/asr/av-heldout-sealed-e1/manifest.jsonl",
}


def relabel(src: Path, dst: Path) -> dict:
    """Deterministic transform: training labels -> evaluation labels.
    Field order is preserved so the output is byte-reproducible."""
    rows = []
    for line in open(src):
        if not line.strip():
            continue
        row = json.loads(line)
        row["split"] = "test"
        row["allowed_use"] = ["asr_eval"]
        rows.append(row)
    validate_rows(rows, str(dst))
    with open(dst, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False,
                               separators=(", ", ": ")) + "\n")
    return {"rows": len(rows), "sha256": sha256_file(dst),
            "speakers": len({r["speaker_id"] for r in rows})}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    print(json.dumps(relabel(src, dst), indent=1))
