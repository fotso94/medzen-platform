#!/usr/bin/env python3
"""Cross-language leakage matrix (committed implementation — Codex
review #19 lesser concern: outputs were hash-bound but the producing
script was not committed). Reads the CURRENT adopted dataset version
from the local gbN COMPLETE evidence copy given as argv[1]; every
corpus and pool input is sha-verified (fetched from S3 on cache
miss). Dimensions: byte (audio/raw sha), speaker_id, session_id.
Exit 1 on any FAIL cell."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import os
import tempfile
# local manifest cache — every file is sha-verified before use and
# fetched from S3 on miss/mismatch, so the cache is an optimization,
# never an authority
S = Path(os.environ.get("MEDZEN_CACHE_DIR")
         or tempfile.mkdtemp(prefix="medzen-leak-matrix-"))
BUCKET = "medzen-speech"

if len(sys.argv) < 2:
    raise SystemExit("usage: gb_leak_matrix.py <path-to-gbN-COMPLETE.json>")
print(f"cache dir: {S} (verified-inputs cache; persists for reuse —"
      " set MEDZEN_CACHE_DIR or delete it afterwards)", flush=True)
complete = json.load(open(sys.argv[1]))

# ---- corpora: name -> (local path, authoritative sha from gb7 COMPLETE rev2)
CORPUS_LOCAL = {}
for name, m in complete["manifests"].items():
    lang, _, cfg = name.split("/")
    if name == "english/asr/cv17_en":
        local = S / "english_cv17_gb9.jsonl"
    elif name == "kinyarwanda/asr/cv17_rw":
        local = S / "rw-v2-manifest.jsonl"
    elif name == "pidgin/asr/av_pcm":
        local = S / "pidgin-train-v2.jsonl"
    else:
        local = S / "gb6" / f"{lang}_asr_{cfg}.jsonl"
    CORPUS_LOCAL[name] = (local, m["sha256"], m["key"])

# ---- pools: (label, language, local path, key, authoritative sha)
POOLS = []
t1 = json.load(open("platform/evidence/B5-TIER2-HOLDOUTS-2026-001.json"))
for lang, pools in t1["pools"].items():
    for p in pools:
        for kind in ("tier2-dev", "tier2-sealed"):
            v = p[kind]
            key = v["key"]
            stem = key.split("/")[3].replace("-tier2-dev", "").replace(
                "-tier2-sealed", "")
            local = S / (f"tier2-{'dev-' if kind == 'tier2-dev' else 'sealed-'}"
                         f"eval_{lang}_asr_{stem}.jsonl")
            POOLS.append((key.split("/", 1)[1].rsplit("/", 1)[0], lang, local,
                          key, v["sha256"]))
t2 = json.load(open("platform/evidence/B5-TIER2-HOLDOUTS-2026-002.json"))
for p in t2["pools"]["pidgin"]:
    for kind, local in (("tier2-dev", "pidgin-tier2-dev.jsonl"),
                        ("tier2-sealed", "pidgin-tier2-sealed.jsonl")):
        v = p[kind]
        POOLS.append((v["key"].split("/", 1)[1].rsplit("/", 1)[0], "pidgin",
                      S / local, v["key"], v["sha256"]))
t4 = json.load(open("platform/evidence/B5-TIER2-HOLDOUTS-2026-004.json"))
for p in t4["pools"]["pidgin"]:
    for kind, local in (("tier2-dev", "pidgin-heldout-dev-e1.jsonl"),
                        ("tier2-sealed", "pidgin-heldout-sealed-e1.jsonl")):
        v = p[kind]
        POOLS.append((v["key"].split("/", 1)[1].rsplit("/", 1)[0], "pidgin",
                      S / local, v["key"], v["sha256"]))
imm = json.load(open("platform/evidence/B5-IMMUTABILITY-BINDINGS-2026-001.json"))
for kind, local in (("dev-selection", "rw-dev-selection.jsonl"),
                    ("universal-sealed", "rw-universal-sealed.jsonl")):
    v = imm["universal_kinyarwanda_holdout"][kind]
    POOLS.append((v["key"].split("/", 1)[1].rsplit("/", 1)[0], "kinyarwanda",
                  S / local, v["key"], v["sha256"]))
# the QUARANTINED old seal still guards promotion history — include it
POOLS.append(("kinyarwanda/asr/cv17-test-v1-sealed (QUARANTINED)",
              "kinyarwanda", S / "rw-sealed-manifest.jsonl",
              "eval/kinyarwanda/asr/cv17-test-v1-sealed/manifest.jsonl",
              "f6f50bcfc473a12026efefe94b1fbbebcf42e6006623860c18be21e6583e70b9"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verified(local: Path, key: str, want: str) -> Path:
    if local.is_file() and sha256_file(local) == want:
        return local
    alt = S / ("fetched-" + key.replace("/", "_"))
    subprocess.run(["aws", "s3", "cp", f"s3://{BUCKET}/{key}", str(alt),
                    "--quiet"], check=True)
    got = sha256_file(alt)
    if got != want:
        raise SystemExit(f"SHA MISMATCH for {key}: want {want[:12]} got {got[:12]}")
    return alt


# ---- load pools into membership sets
pool_sets = []
for label, lang, local, key, sha in POOLS:
    path = verified(local, key, sha)
    b, spk, ses = set(), set(), set()
    rows = 0
    for line in open(path):
        if not line.strip():
            continue
        r = json.loads(line)
        rows += 1
        # train manifests say audio_sha256; eval manifests audio_checksum_sha256
        b.add(r.get("audio_sha256") or r["audio_checksum_sha256"])
        if r.get("raw_checksum_sha256"):
            b.add(r["raw_checksum_sha256"])
        spk.add(str(r.get("speaker_id")))
        if r.get("session_id"):
            ses.add(str(r["session_id"]))
    pool_sets.append({"label": label, "language": lang, "key": key,
                      "sha256": sha, "rows": rows, "bytes": b,
                      "speakers": spk, "sessions": ses})
    print(f"pool {label}: {rows} rows verified", flush=True)

# ---- stream corpora, test membership against every pool
cells = {}
corpus_meta = {}
for name, (local, sha, key) in sorted(CORPUS_LOCAL.items()):
    path = verified(local, key, sha)
    clang = name.split("/")[0]
    hits = [{"bytes": 0, "speakers": set(), "sessions": set()}
            for _ in pool_sets]
    rows = 0
    corpus_speakers = set()
    for line in open(path):
        if not line.strip():
            continue
        r = json.loads(line)
        rows += 1
        a = r.get("audio_sha256") or r["audio_checksum_sha256"]
        rc = r.get("raw_checksum_sha256")
        spk, ses = str(r.get("speaker_id")), str(r.get("session_id") or "")
        corpus_speakers.add(spk)
        for i, p in enumerate(pool_sets):
            if a in p["bytes"] or (rc and rc in p["bytes"]):
                hits[i]["bytes"] += 1
            if spk in p["speakers"]:
                hits[i]["speakers"].add(spk)
            if ses and ses in p["sessions"]:
                hits[i]["sessions"].add(ses)
    corpus_meta[name] = {"rows": rows, "sha256": sha, "key": key,
                         "distinct_speakers": len(corpus_speakers)}
    for i, p in enumerate(pool_sets):
        h = hits[i]
        if h["bytes"] or h["speakers"] or h["sessions"]:
            same = clang == p["language"]
            # FLEURS-style exports carry ONE corpus-level placeholder
            # speaker_id on every row (cardinality 1 on both sides): the
            # id names the corpus, not a person, so "overlap" is
            # tautological and disjointness is unverifiable locally.
            placeholder = (len(corpus_speakers) == 1
                           and len(p["speakers"]) == 1
                           and not h["bytes"] and not h["sessions"])
            if h["bytes"]:
                verdict = "FAIL"
            elif same and placeholder:
                verdict = "PLACEHOLDER_SPEAKER_ID_UNVERIFIABLE"
            elif same:
                verdict = "FAIL"
            else:
                # Codex review #20: cross-language speaker overlap was
                # merely reported — the exact defect gb9 exists to
                # prevent. Zero tolerance now that the datasets are clean.
                verdict = "FAIL_CROSS_LANGUAGE_SPEAKER"
            cells[f"{name} x {p['label']}"] = {
                "byte_overlap_rows": h["bytes"],
                "speaker_overlap": sorted(h["speakers"])[:20],
                "speaker_overlap_count": len(h["speakers"]),
                "session_overlap_count": len(h["sessions"]),
                "same_language": same, "verdict": verdict}
    print(f"corpus {name}: {rows} rows checked", flush=True)

fails = {k: v for k, v in cells.items()
         if v["verdict"].startswith("FAIL")}
out = {
    "matrix": "gb9 corpora x all frozen evaluation pools",
    "corpora": corpus_meta,
    "pools": [{k: p[k] for k in ("label", "language", "key", "sha256", "rows")}
              for p in pool_sets],
    "cells_total": len(CORPUS_LOCAL) * len(pool_sets),
    "dimensions": ["byte (audio_sha256 or raw_checksum_sha256)",
                   "speaker_id", "session_id"],
    "nonzero_cells": cells,
    "fail_cells": len(fails),
    "clean": not fails,
}
json.dump(out, open(S / "gb9-leak-matrix.json", "w"), indent=1, sort_keys=True)
print(json.dumps({"cells": out["cells_total"], "nonzero": len(cells),
                  "FAIL": len(fails), "clean": out["clean"]}, indent=1))
for k, v in cells.items():
    print("NONZERO:", k, "->", v["verdict"], "bytes:", v["byte_overlap_rows"],
          "speakers:", v["speaker_overlap_count"])
sys.exit(1 if fails else 0)
