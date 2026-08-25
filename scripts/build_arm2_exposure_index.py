"""Machine-derived Arm-2 exposure index (Codex/owner design re-review, rev 003).

Deterministically derives, FROM THE COMMITTED SOURCE RECORDS, the audio identity
(audio_checksum_sha256) sets of every IN-REPO used unsealed surface, plus the
USED_UNION that a future held-out development NOMINATION split (and any Phase-B
confirmation split) must be provably disjoint from. Surfaces whose per-row rows
are S3-pinned and not materialised in the repo (training corpora, base-eval
pools) are recorded by their pinned POOL-LEVEL identity so disjointness against
them is checked at mint time against the pinned S3 manifest.

Re-runnable and pure: `python -m scripts.build_arm2_exposure_index --check`
rebuilds the index and asserts it byte-equals the committed file, so the
committed index is provably machine-derived (never hand-edited).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json"

DEV_SELECTION = ROOT / "platform/manifests/B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json"
LINGALA_SENTINEL = ROOT / "platform/manifests/B5-ARM1-LINGALA-SENTINEL-2026-001.json"
SENTINEL_JSONL = {
    "lingala": ROOT / "platform/manifests/dev-sentinels/lingala.jsonl",
    "swahili": ROOT / "platform/manifests/dev-sentinels/swahili.jsonl",
}


def _agg(checksums):
    """Return (unique-count, aggregate sha256 over the sorted unique set)."""
    uniq = sorted(set(checksums))
    return len(uniq), hashlib.sha256("\n".join(uniq).encode()).hexdigest()


def _rows_checksums(rows):
    return [r["audio_checksum_sha256"] for r in rows]


def build() -> dict:
    surfaces = []
    union: set[str] = set()

    # 1) Arm-1 dev-selection (420 rows, 7 languages) — full per-row identities
    ds = json.loads(DEV_SELECTION.read_bytes())
    ds_cks = _rows_checksums(ds["rows"])
    per_lang = {}
    for r in ds["rows"]:
        per_lang.setdefault(r["language"], []).append(r["audio_checksum_sha256"])
    n, agg = _agg(ds_cks)
    surfaces.append({
        "surface": "arm1-dev-selection",
        "path": "platform/manifests/B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json",
        "exposure": "USED_DEV_SELECTION",
        "row_count": len(ds["rows"]),
        "unique_checksums": n,
        "checksums_aggregate_sha256": agg,
        "per_language_unique": {k: _agg(v)[0] for k, v in sorted(per_lang.items())},
    })
    union |= set(ds_cks)

    # 2) lingala regression sentinel record (386 rows) — full per-row identities
    ls = json.loads(LINGALA_SENTINEL.read_bytes())
    ls_cks = _rows_checksums(ls["rows"])
    n, agg = _agg(ls_cks)
    surfaces.append({
        "surface": "arm1-lingala-sentinel-386",
        "path": "platform/manifests/B5-ARM1-LINGALA-SENTINEL-2026-001.json",
        "exposure": "USED_DEV_SENTINEL",
        "row_count": len(ls["rows"]),
        "unique_checksums": n,
        "checksums_aggregate_sha256": agg,
    })
    union |= set(ls_cks)

    # 3) frozen 60-row dev sentinels (subsets of the dev-selection)
    for lang, path in sorted(SENTINEL_JSONL.items()):
        cks = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                cks.append(json.loads(line)["audio_checksum_sha256"])
        n, agg = _agg(cks)
        surfaces.append({
            "surface": f"dev-sentinel-{lang}-60",
            "path": f"platform/manifests/dev-sentinels/{lang}.jsonl",
            "exposure": "USED_DEV_SENTINEL",
            "row_count": len(cks),
            "unique_checksums": n,
            "checksums_aggregate_sha256": agg,
        })
        union |= set(cks)

    # 4) S3-pinned pools (per-row identities NOT in repo): recorded by pinned
    #    pool-level identity so a mint-time disjointness check can fetch them.
    pinned_pools = []
    for pool, meta in sorted((ds.get("sources") or {}).items()):
        pinned_pools.append({
            "pool": pool,
            "pool_rows": meta.get("pool_rows"),
            "selected_rows": meta.get("selected_rows"),
            "sha256": meta.get("sha256"),
            "version_id": meta.get("version_id"),
            "etag": meta.get("etag"),
            "pinned_by": meta.get("pinned_by"),
            "note": "per-row audio_checksum_sha256 not materialised in-repo; "
                    "disjointness checked at mint against this pinned S3 manifest",
        })

    union_count, union_agg = _agg(union)
    return {
        "record": "B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001",
        "status": "PENDING_DESIGN_REVIEW",
        "generator": "scripts/build_arm2_exposure_index.py",
        "note": "MACHINE-DERIVED (Codex r31 design re-review): rebuilt by the "
                "generator from committed source records; `--check` proves the "
                "committed file byte-equals a fresh build. The USED_UNION is the "
                "identity set a future held-out development NOMINATION split (and "
                "any Phase-B confirmation split) MUST be disjoint from.",
        "identity_key": "audio_checksum_sha256",
        "surfaces": surfaces,
        "s3_pinned_pools": pinned_pools,
        "used_union": {
            "unique_checksums": union_count,
            "checksums_aggregate_sha256": union_agg,
        },
        "used_exact_definition": "USED_EXACT(language) = the EXACT set of "
            "audio_checksum_sha256 of (a) the dev-selection rows + (b) the "
            "sentinel rows [both enumerated above] UNION (c) every row of the "
            "base-eval pools the base model was scored on (the FULL "
            "fleurs/soreva/aaf pools per the base-eval receipts; pidgin's "
            "av-heldout was NEVER base-scored). SEALED_EXACT(language) = the "
            "exact set of the sealed half's rows. Neither is a pool-size count.",
        "disjointness_contract": "the nomination split is defined by EXACT "
            "PER-ROW SET DIFFERENCE, never whole-pool count subtraction: "
            "split(language) = { r in source pool : audio_checksum_sha256(r) "
            "NOT in USED_EXACT(language) AND NOT in SEALED_EXACT(language) }. "
            "At mint the exact USED_EXACT/SEALED_EXACT are enumerated from the "
            "in-repo identities here PLUS the s3_pinned_pools and sealed-half "
            "manifests fetched by their pinned sha/version_id; the mint commits "
            "the row-level disjointness proof.",
    }


def used_union_checksums() -> set:
    """The set of every in-repo used audio_checksum_sha256 (dev-selection +
    lingala 386 sentinel + the 60-row sentinels). A held-out nomination /
    confirmation split must be disjoint from this."""
    union: set = set()
    ds = json.loads(DEV_SELECTION.read_bytes())
    union |= {r["audio_checksum_sha256"] for r in ds["rows"]}
    ls = json.loads(LINGALA_SENTINEL.read_bytes())
    union |= {r["audio_checksum_sha256"] for r in ls["rows"]}
    for path in SENTINEL_JSONL.values():
        for line in path.read_text().splitlines():
            if line.strip():
                union.add(json.loads(line)["audio_checksum_sha256"])
    return union


def split_is_disjoint(candidate_checksums) -> tuple:
    """Intersection test: (is_disjoint, sorted overlap) of a candidate split's
    audio_checksum_sha256 values against the in-repo USED_UNION. (S3-pinned
    pools are additionally checked at mint against their pinned manifests.)"""
    overlap = set(candidate_checksums) & used_union_checksums()
    return (not overlap), sorted(overlap)


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="assert the committed index equals a fresh build")
    args = ap.parse_args(argv)
    fresh = _dump(build())
    if args.check:
        current = INDEX.read_text() if INDEX.exists() else ""
        if current != fresh:
            raise SystemExit("exposure index is STALE — re-run the generator")
        print("exposure index is machine-derived and current")
        return 0
    INDEX.write_text(fresh)
    print(f"wrote {INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
