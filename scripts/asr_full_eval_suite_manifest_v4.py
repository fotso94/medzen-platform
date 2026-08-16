#!/usr/bin/env python3
"""Rebuild the full-evaluation-suite shard manifest from measured evidence.

Schema v4 replaces v3's uniform row balancing, which modeled every shard at
~1.35h and was refuted live: attempt 31 hit the Job cap at 13,780 of 14,464
row-inferences. The v4 time model prices each language from the pilot's
committed per-language latency medians (attempt 28 aggregate) scaled by a
calibration factor derived from attempt 31's measured wall rates, then packs
shards deterministically under an explicit inference budget inside the
18,000s window's 16,200s Job cap. Shard 1 is preserved verbatim from v3:
its bundle is already frozen, uploaded and proof-bound.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

V3_PATH = ROOT / "platform/manifests/ASR-FULL-EVAL-SUITE-SHARD-MANIFEST-2026-001.json"
PILOT_AGGREGATE_PATH = (
    ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-003A-A28-LIVE/aggregate-report.json"
)
OUTPUT_PATH = ROOT / "platform/manifests/ASR-FULL-EVAL-SUITE-SHARD-MANIFEST-2026-002.json"

GROUPS = (
    "omniASR_CTC_1B_v2|unconditioned",
    "omniASR_LLM_1B_v2|unconditioned",
    "omniASR_LLM_1B_v2|conditioned",
    "whisper-large-v3|unconditioned",
    "whisper-large-v3|conditioned",
)

# Attempt-31 measured wall rates vs the pilot's kinyarwanda medians:
# CTC 0.0322 s/row measured vs 0.0294 median (x1.09); whisper unconditioned
# 0.952 s/row measured vs 0.740 median (x1.29). 1.35 stays above both.
MEDIAN_TO_MEAN_CALIBRATION = 1.35

ATTEMPT_WINDOW_SECONDS = 18_000
JOB_ACTIVE_DEADLINE_SECONDS = 16_200
# Model verification + five model loads + journal setup measured ~9 minutes
# in attempt 31 (PILOT_START 22:08:55 after job start ~22:00).
SHARD_OVERHEAD_SECONDS = 900
# Pack to 12,000s of priced inference: with overhead that is ~80% of the
# Job cap, leaving 3,300s (~25% of priced work) for model drift.
SHARD_INFERENCE_BUDGET_SECONDS = 12_000

PER_SHARD_CEILING_USD = 10
AGGREGATE_CEILING_USD = 400


def language_second_rates(aggregate: dict) -> dict[str, float]:
    per_language = aggregate["aggregate"]["per_language"]
    rates: dict[str, float] = {}
    languages = {key.split("|")[2] for key in per_language}
    for language in languages:
        per_row = 0.0
        for group in GROUPS:
            entry = per_language.get(f"{group}|{language}")
            if entry is not None:
                per_row += entry["latency_median_seconds"] * MEDIAN_TO_MEAN_CALIBRATION
        rates[language] = per_row
    return rates


def build() -> dict:
    v3 = json.loads(V3_PATH.read_bytes())
    aggregate = json.loads(PILOT_AGGREGATE_PATH.read_bytes())
    rates = language_second_rates(aggregate)
    pool = v3["pool"]["per_language_validated_rows"]
    missing = sorted(set(pool) - set(rates))
    if missing:
        raise SystemExit(f"languages without pilot latency evidence: {missing}")

    shard_one = json.loads(json.dumps(v3["shards"][0]))
    shard_one["estimated_inference_seconds"] = round(
        sum(
            (unit["row_end"] - unit["row_start"]) * rates[unit["language"]]
            for unit in shard_one["units"]
        ),
        1,
    )
    del shard_one["projected_job_hours"]

    # Remaining coverage: the full pool minus what shard 1 already binds.
    covered: dict[str, list[tuple[int, int]]] = {}
    for unit in shard_one["units"]:
        covered.setdefault(unit["language"], []).append((unit["row_start"], unit["row_end"]))
    remaining: list[tuple[str, int, int]] = []
    for language, total_rows in sorted(pool.items()):
        spans = sorted(covered.get(language, []))
        cursor = 0
        for start, end in spans:
            if start > cursor:
                remaining.append((language, cursor, start))
            cursor = max(cursor, end)
        if cursor < total_rows:
            remaining.append((language, cursor, total_rows))

    # Split any span whose priced time exceeds the budget, then first-fit-
    # decreasing over (seconds, language, row_start) for determinism.
    chunks: list[tuple[float, str, int, int]] = []
    for language, start, end in remaining:
        rate = rates[language]
        span_rows = end - start
        max_rows = span_rows if rate <= 0 else max(1, int(SHARD_INFERENCE_BUDGET_SECONDS // rate))
        cursor = start
        while cursor < end:
            piece_end = min(end, cursor + max_rows)
            chunks.append(((piece_end - cursor) * rate, language, cursor, piece_end))
            cursor = piece_end
    chunks.sort(key=lambda item: (-item[0], item[1], item[2]))

    bins: list[dict] = []
    for seconds, language, start, end in chunks:
        placed = None
        for candidate in bins:
            if candidate["estimated_inference_seconds"] + seconds <= SHARD_INFERENCE_BUDGET_SECONDS:
                placed = candidate
                break
        if placed is None:
            placed = {"estimated_inference_seconds": 0.0, "units": []}
            bins.append(placed)
        placed["estimated_inference_seconds"] += seconds
        placed["units"].append({"language": language, "row_start": start, "row_end": end})

    shards = [shard_one]
    for index, candidate in enumerate(bins, start=2):
        units = sorted(candidate["units"], key=lambda u: (u["language"], u["row_start"]))
        shards.append(
            {
                "shard": index,
                "rows": sum(u["row_end"] - u["row_start"] for u in units),
                "estimated_inference_seconds": round(candidate["estimated_inference_seconds"], 1),
                "units": units,
            }
        )
    shard_one["shard"] = 1

    total_rows = sum(s["rows"] for s in shards)
    pool_total = sum(pool.values())
    if total_rows != pool_total:
        raise SystemExit(f"row coverage differs from the validated pool: {total_rows} != {pool_total}")
    for language, total in pool.items():
        spans = sorted(
            (u["row_start"], u["row_end"])
            for s in shards
            for u in s["units"]
            if u["language"] == language
        )
        cursor = 0
        for start, end in spans:
            if start != cursor or end <= start:
                raise SystemExit(f"row spans for {language} overlap or leave a gap at {start}")
            cursor = end
        if cursor != total:
            raise SystemExit(f"row spans for {language} cover {cursor} of {total}")

    manifest = {
        "id": "ASR-FULL-EVAL-SUITE-SHARD-MANIFEST-2026-002",
        "schema_version": 4,
        "supersedes": v3["id"],
        "design": (
            f"measured-rate shards: per-language seconds/row from the attempt-28 "
            f"pilot aggregate medians x{MEDIAN_TO_MEAN_CALIBRATION} (calibrated against "
            f"attempt-31 measured wall rates), packed first-fit-decreasing under a "
            f"{SHARD_INFERENCE_BUDGET_SECONDS}s inference budget inside the "
            f"{JOB_ACTIVE_DEADLINE_SECONDS}s Job cap of the {ATTEMPT_WINDOW_SECONDS}s window; "
            f"shard 1 preserved verbatim from v3 (bundle frozen and proof-bound); "
            f"oversized languages split by the same checksum-sorted row ranges"
        ),
        "attempt_window": {
            "seconds_each": ATTEMPT_WINDOW_SECONDS,
            "job_active_deadline_seconds": JOB_ACTIVE_DEADLINE_SECONDS,
        },
        "shard_overhead_seconds": SHARD_OVERHEAD_SECONDS,
        "shard_inference_budget_seconds": SHARD_INFERENCE_BUDGET_SECONDS,
        "time_model": {
            "source_aggregate": str(PILOT_AGGREGATE_PATH.relative_to(ROOT)),
            "source_aggregate_sha256": hashlib.sha256(PILOT_AGGREGATE_PATH.read_bytes()).hexdigest(),
            "median_to_mean_calibration": MEDIAN_TO_MEAN_CALIBRATION,
            "calibration_evidence": {
                "attempt": 31,
                "measured": {
                    "omniASR_CTC_1B_v2_unconditioned_rows_per_min": 1864,
                    "whisper_large_v3_unconditioned_rows_per_min": 63,
                    "completed_row_inferences_at_9000s_job_cap": 13780,
                },
            },
            "per_language_seconds_per_row": {
                language: round(rates[language], 4) for language in sorted(pool)
            },
        },
        "pool": v3["pool"],
        "ceilings": {
            "per_shard_usd": PER_SHARD_CEILING_USD,
            "shards": len(shards),
            "total_ceilings_usd": PER_SHARD_CEILING_USD * len(shards),
            "aggregate_ceiling_usd": AGGREGATE_CEILING_USD,
        },
        "shards": shards,
    }
    return manifest


def main() -> int:
    manifest = build()
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if OUTPUT_PATH.exists():
        raise SystemExit(f"refusing to overwrite {OUTPUT_PATH}")
    OUTPUT_PATH.write_bytes(body)
    total = sum(s["estimated_inference_seconds"] for s in manifest["shards"])
    print(f"shards={len(manifest['shards'])} rows={sum(s['rows'] for s in manifest['shards'])} "
          f"priced_inference_hours={total / 3600:.1f} sha256={hashlib.sha256(body).hexdigest()}")
    for shard in manifest["shards"]:
        hours = (shard["estimated_inference_seconds"] + SHARD_OVERHEAD_SECONDS) / 3600
        print(f"  shard {shard['shard']:2d}: rows={shard['rows']:5d} est_job={hours:4.2f}h "
              + ", ".join(f"{u['language']}[{u['row_start']}:{u['row_end']}]" for u in shard["units"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
