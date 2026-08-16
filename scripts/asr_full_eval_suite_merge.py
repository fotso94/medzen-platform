#!/usr/bin/env python3
"""Merge suite-shard aggregates into one 47-language report with coverage proof.

Every PASS suite run leaves three artifacts in its evidence directory: the
terminal result, the hash-verified aggregate, and the deterministic row
selection it evaluated. This tool merges the per-language error COUNTS
across runs (counts are exactly poolable; rates are recomputed from the
pooled counts) and mechanically proves row coverage: for every language in
the committed shard-manifest pool, the union of evaluated selection
ordinals must equal {1..n} with no gap and no overlap. The merged report
refuses to claim completeness on anything less.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

MANIFEST_PATH = "platform/manifests/ASR-FULL-EVAL-SUITE-SHARD-MANIFEST-2026-002.json"
RECEIPTS_GLOB = "platform/evidence/receipts/ASR-BASE-MODEL-*-LIVE"
COUNT_FIELDS = (
    "rows",
    "character_errors",
    "reference_characters",
    "word_errors",
    "reference_words",
    "cap_hits",
    "eos_failures",
)


class SuiteMergeRefusal(RuntimeError):
    pass


def _verified_salvage(root: Path, directory: Path) -> Path | None:
    """A refused attempt may still contribute if a committed salvage record
    binds this directory's salvaged aggregate by SHA-256. Tampered or
    unrecorded salvage never merges."""
    salvage_path = directory / "salvaged-aggregate.json"
    if not salvage_path.is_file():
        return None
    digest = hashlib.sha256(salvage_path.read_bytes()).hexdigest()
    for record_path in sorted((root / "platform/evidence").glob("*SALVAGE*.json")):
        record = json.loads(record_path.read_bytes())
        if (
            record.get("live_receipts", {}).get("directory") == str(directory.relative_to(root))
            and record.get("salvage", {}).get("aggregate_sha256") == digest
            and record.get("salvage", {}).get("aggregate_status") == "PASS_AGGREGATE"
        ):
            return salvage_path
    return None


def discover_pass_runs(root: Path, minimum_attempt: int) -> list[dict[str, Any]]:
    runs = []
    for directory in sorted(root.glob(RECEIPTS_GLOB)):
        result_path = directory / "result.json"
        aggregate_path = directory / "aggregate-report.json"
        selection_path = directory / "pilot-selection.json"
        if not (result_path.is_file() and selection_path.is_file()):
            continue
        result = json.loads(result_path.read_bytes())
        attempt = result.get("attempt")
        if not isinstance(attempt, int) or attempt < minimum_attempt:
            continue
        salvage = None
        if result.get("outcome") != "PASS_PILOT":
            salvage = _verified_salvage(root, directory)
            if salvage is None:
                continue
        elif not aggregate_path.is_file():
            continue
        source = salvage if salvage is not None else aggregate_path
        aggregate = json.loads(source.read_bytes())
        if salvage is not None:
            aggregate = {"aggregate": aggregate["aggregate"]}
        runs.append({
            "attempt": attempt,
            "directory": str(directory.relative_to(root)),
            "salvaged": salvage is not None,
            "aggregate": aggregate,
            "selection": json.loads(selection_path.read_bytes()),
        })
    duplicates = defaultdict(list)
    for run in runs:
        duplicates[run["attempt"]].append(run["directory"])
    doubled = {k: v for k, v in duplicates.items() if len(v) > 1}
    if doubled:
        raise SuiteMergeRefusal(f"multiple evidence directories claim one attempt: {doubled}")
    return sorted(runs, key=lambda run: run["attempt"])


def coverage_audit(pool: dict[str, int], runs: list[dict[str, Any]]) -> dict[str, Any]:
    seen: dict[str, dict[int, int]] = {language: {} for language in pool}
    for run in runs:
        for row in run["selection"]["rows"]:
            language = row["language"]
            ordinal = row["selection_ordinal"]
            if language not in seen:
                raise SuiteMergeRefusal(
                    f"attempt {run['attempt']} evaluated a language outside the pool: {language}"
                )
            if ordinal in seen[language]:
                seen[language][ordinal] = seen[language][ordinal] + 1
            else:
                seen[language][ordinal] = 1
    languages: dict[str, Any] = {}
    complete = True
    for language, total in sorted(pool.items()):
        ordinals = seen[language]
        expected = set(range(1, total + 1))
        covered = set(ordinals)
        missing = sorted(expected - covered)
        unexpected = sorted(covered - expected)
        overlaps = sorted(o for o, count in ordinals.items() if count > 1)
        state = "COMPLETE"
        if missing or unexpected or overlaps:
            complete = False
            state = "INCOMPLETE" if missing else "INCONSISTENT"
        languages[language] = {
            "pool_rows": total,
            "covered_rows": len(covered & expected),
            "missing_ranges": _ranges(missing),
            "unexpected_ordinals": unexpected[:20],
            "overlapping_ordinals": overlaps[:20],
            "state": state,
        }
    return {
        "status": "PASS_GAP_FREE_COVERAGE" if complete else "COVERAGE_INCOMPLETE",
        "pool_rows": sum(pool.values()),
        "covered_rows": sum(entry["covered_rows"] for entry in languages.values()),
        "languages": languages,
    }


def _ranges(values: list[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for value in values:
        if ranges and value == ranges[-1][1] + 1:
            ranges[-1][1] = value
        else:
            ranges.append([value, value])
    return ranges


def merge_metrics(runs: list[dict[str, Any]]) -> dict[str, Any]:
    pooled: dict[str, dict[str, int]] = {}
    medians: dict[str, dict[str, float]] = defaultdict(dict)
    for run in runs:
        per_language = run["aggregate"]["aggregate"]["per_language"]
        for key, entry in per_language.items():
            bucket = pooled.setdefault(key, {field: 0 for field in COUNT_FIELDS})
            for field in COUNT_FIELDS:
                bucket[field] += int(entry[field])
            medians[key][f"attempt_{run['attempt']}"] = entry["latency_median_seconds"]
    merged_languages = {}
    for key, counts in sorted(pooled.items()):
        merged_languages[key] = {
            **counts,
            "cer": round(counts["character_errors"] / counts["reference_characters"], 6)
            if counts["reference_characters"]
            else None,
            "wer": round(counts["word_errors"] / counts["reference_words"], 6)
            if counts["reference_words"]
            else None,
            "latency_median_seconds_by_attempt": dict(sorted(medians[key].items())),
        }
    groups: dict[str, dict[str, int]] = {}
    for key, counts in pooled.items():
        candidate, mode, _ = key.split("|", 2)
        bucket = groups.setdefault(f"{candidate}|{mode}", {field: 0 for field in COUNT_FIELDS})
        for field in COUNT_FIELDS:
            bucket[field] += counts[field]
    merged_groups = {
        key: {
            **counts,
            "cer": round(counts["character_errors"] / counts["reference_characters"], 6)
            if counts["reference_characters"]
            else None,
            "wer": round(counts["word_errors"] / counts["reference_words"], 6)
            if counts["reference_words"]
            else None,
        }
        for key, counts in sorted(groups.items())
    }
    return {"groups": merged_groups, "per_language": merged_languages}


def build_report(root: Path, minimum_attempt: int) -> dict[str, Any]:
    manifest_path = root / MANIFEST_PATH
    manifest = json.loads(manifest_path.read_bytes())
    pool = manifest["pool"]["per_language_validated_rows"]
    runs = discover_pass_runs(root, minimum_attempt)
    if not runs:
        raise SuiteMergeRefusal("no PASS suite runs discovered")
    coverage = coverage_audit(pool, runs)
    metrics = merge_metrics(runs)
    return {
        "record": "ASR_FULL_EVAL_SUITE_MERGED_REPORT",
        "schema_version": 1,
        "status": coverage["status"],
        "shard_manifest": {
            "path": MANIFEST_PATH,
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
        "source_runs": [
            {"attempt": run["attempt"], "directory": run["directory"],
             "rows": len(run["selection"]["rows"]), "salvaged": run.get("salvaged", False)}
            for run in runs
        ],
        "coverage": coverage,
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--minimum-attempt", type=int, default=32)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.root.resolve(), args.minimum_attempt)
    body = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if args.output:
        args.output.write_bytes(body)
    coverage = report["coverage"]
    print(f"{report['status']}: {coverage['covered_rows']}/{coverage['pool_rows']} rows "
          f"across {len(report['source_runs'])} runs")
    for language, entry in coverage["languages"].items():
        if entry["state"] != "COMPLETE":
            print(f"  {language}: {entry['state']} covered {entry['covered_rows']}/{entry['pool_rows']} "
                  f"missing {entry['missing_ranges'][:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
