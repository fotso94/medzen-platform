"""Machine-derived Arm-2 exposure index (Codex/owner design re-review, rev 003).

Deterministically derives, FROM THE COMMITTED SOURCE RECORDS, the audio identity
(audio_checksum_sha256) sets of every IN-REPO used unsealed surface, tagged by
EXPOSURE CLASS (CANDIDATE_EXPOSED / BASE_EXPOSED / TRAINING_EXPOSED / SEALED), so
phase-specific eligibility (Phase-A nomination allows BASE_EXPOSED rows; Phase-B
confirmation excludes them) is a per-row class test, not whole-pool exclusion. Surfaces whose per-row rows
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
        "exposure_class": "CANDIDATE_EXPOSED",
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
        "exposure_class": "CANDIDATE_EXPOSED",
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
            "exposure_class": "CANDIDATE_EXPOSED",
            "row_count": len(cks),
            "unique_checksums": n,
            "checksums_aggregate_sha256": agg,
        })
        union |= set(cks)

    # 4) EVERY S3-pinned exposure source, CLASSIFIED and PINNED (rev 006).
    #    Per-row identities are not in the repo, so each source is recorded by
    #    its pinned identity (sha256 + s3_version_id) so a mint-time per-row
    #    class assignment can fetch it. A pool's eval-dev half is BASE_EXPOSED
    #    iff the zero-shot base model scored it (fleurs/soreva/aaf/cv17 were in
    #    the base 47/54-language suite; pidgin av-heldout was NEVER scored by
    #    the base, so its dev half is base-blind AND candidate-blind).
    BASE_SCORED_POOL_TAGS = ("fleurs-v1", "soreva-v1", "aaf-test", "cv17-test")
    pinned_sources = []
    tier2 = ROOT / "platform/evidence"
    for rec in sorted(tier2.glob("B5-TIER2-HOLDOUTS-2026-0*.json")):
        rel = str(rec.relative_to(ROOT))
        hold = json.loads(rec.read_bytes())
        for lang, entries in sorted((hold.get("pools") or {}).items()):
            for e in entries:
                pool = e.get("pool", "")
                base_scored = any(t in pool for t in BASE_SCORED_POOL_TAGS)
                dev = e.get("tier2-dev") or {}
                sealed = e.get("tier2-sealed") or {}
                if dev:
                    pinned_sources.append({
                        "class": "BASE_EXPOSED" if base_scored
                        else "BASE_BLIND_CANDIDATE_ELIGIBLE",
                        "role": "eval_dev_half",
                        "base_scored_by_zero_shot_base": base_scored,
                        "language": lang, "pool": pool,
                        "key": dev.get("key"), "rows": dev.get("rows"),
                        "sha256": dev.get("sha256"),
                        "s3_version_id": dev.get("s3_version_id"),
                        "source_record": rel,
                    })
                if sealed:
                    pinned_sources.append({
                        "class": "SEALED", "role": "sealed_holdout_half",
                        "language": lang, "pool": pool,
                        "key": sealed.get("key"), "rows": sealed.get("rows"),
                        "sha256": sealed.get("sha256"),
                        "s3_version_id": sealed.get("s3_version_id"),
                        "source_record": rel,
                    })
    for rec, tag in (("B5-GB9-ADOPTION-2026-001.json", "gb9"),
                     ("B5-GB8-ADOPTION-2026-001.json", "gb8")):
        adopt = json.loads((tier2 / rec).read_bytes())
        pinned_sources.append({
            "class": "TRAINING_EXPOSED", "role": "training_corpus",
            "dataset": tag,
            "complete_raw_sha256": adopt.get("complete_raw_sha256"),
            "source_record": f"platform/evidence/{rec}",
        })
    pinned_sources.append({
        "class": "TRAINING_EXPOSED", "role": "training_corpus",
        "dataset": "gb3-kinyarwanda",
        "source_record": "platform/manifests/"
                         "B5-KINYARWANDA-FULL-SAGEMAKER-BINDINGS-2026-001.json",
        "note": "kinyarwanda-only training at gb3 (300h cap); pinned by the "
                "manifest's dataset identity at mint",
    })

    union_count, union_agg = _agg(union)
    return {
        "record": "B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001",
        "status": "PENDING_DESIGN_REVIEW",
        "generator": "scripts/build_arm2_exposure_index.py",
        "note": "MACHINE-DERIVED (rev 006): rebuilt by the generator from "
                "committed source records; `--check` proves the committed file "
                "byte-equals a fresh build. In-repo surfaces (CANDIDATE_EXPOSED) "
                "carry full per-row identities; EVERY S3-pinned source is "
                "classified + pinned in pinned_sources; phase_eligibility "
                "defines per-row eligibility per phase.",
        "identity_key": "audio_checksum_sha256",
        "surfaces": surfaces,
        "pinned_sources": pinned_sources,
        "candidate_exposed_union": {
            "unique_checksums": union_count,
            "checksums_aggregate_sha256": union_agg,
            "note": "the in-repo CANDIDATE_EXPOSED identities (dev-selection + "
                    "sentinels); split_is_disjoint() checks a candidate split "
                    "against THIS set (the Phase-A exclusion of the in-repo "
                    "class).",
        },
        "exposure_classes": {
            "CANDIDATE_EXPOSED": "rows the CANDIDATE-selection process saw "
                "(dev-selection used to pick Arm-1 checkpoints + sentinel rows "
                "scored on candidate checkpoints). In-repo, full per-row "
                "identities above. Excluded from BOTH Phase-A nomination and "
                "Phase-B confirmation.",
            "BASE_EXPOSED": "rows the ZERO-SHOT BASE model scored (the full "
                "fleurs/soreva/aaf pools per the base-eval receipts). S3-pinned "
                "(s3_pinned_pools). Candidate-blind but NOT base-blind: "
                "ELIGIBLE for Phase-A nomination, EXCLUDED from Phase-B "
                "confirmation.",
            "TRAINING_EXPOSED": "rows in the training corpus (gb9/gb8/gb3). "
                "S3-pinned (adoption records). Excluded from ALL evaluation.",
            "SEALED": "the per-language sealed promotion holdout. NEVER touched "
                "by Part-2.",
            "BASE_BLIND_CANDIDATE_ELIGIBLE": "an eval-dev half the base model "
                "NEVER scored (pidgin av-heldout): base-blind AND candidate-"
                "blind, so eligible for BOTH Phase-A nomination and Phase-B "
                "confirmation (minus the 60 CANDIDATE_EXPOSED selection rows).",
        },
        "phase_eligibility": {
            "phase_A_nomination": "a row is eligible iff its "
                "audio_checksum_sha256 is NOT in CANDIDATE_EXPOSED, NOT in "
                "TRAINING_EXPOSED, and NOT in SEALED. BASE_EXPOSED rows ARE "
                "eligible (nomination compares CANDIDATES; base-blindness is "
                "not required here). This keeps english/french/swahili splits "
                "non-empty.",
            "phase_B_confirmation": "additionally excludes BASE_EXPOSED "
                "(base-blind); among existing data only pidgin av-heldout "
                "qualifies -> the rest require new licensed acquisition.",
        },
        "disjointness_contract": "class-based per-row eligibility by "
            "audio_checksum_sha256 (never whole-pool count subtraction). At "
            "mint the exact class membership is enumerated from the in-repo "
            "identities here PLUS the pinned_sources (BASE/SEALED/TRAINING) adoption + "
            "sealed-half manifests fetched by pinned sha/version_id; the mint "
            "commits the row-level proof that the split satisfies the "
            "phase_eligibility rule for its phase.",
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
