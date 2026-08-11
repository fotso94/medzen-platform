#!/usr/bin/env python3
"""Audit local copies of frozen ASR evaluation manifests.

This is deliberately a metadata-only discovery boundary. It never downloads
audio, loads a model, writes AWS state, changes language scope or turns an
invalid input set into a passing freeze. The caller supplies a local directory
whose layout mirrors ``s3://medzen-speech/eval/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROSPECTIVE_VERSIONS = {
    "aaf-test-v1",
    "cv17-test-v1",
    "fleurs-v1",
    "soreva-v1",
}


class AuditRefusal(ValueError):
    """A manifest cannot safely enter the base-model evaluation freeze."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record(path: Path, line_number: int, raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AuditRefusal(f"{path}:{line_number}: malformed JSON") from exc
    if not isinstance(value, dict):
        raise AuditRefusal(f"{path}:{line_number}: row is not an object")
    required = {
        "audio_filepath",
        "audio_checksum_sha256",
        "duration_s",
        "text_normalized",
        "primary_language",
        "split",
        "allowed_use",
        "source_id",
        "dataset_release",
    }
    if not required.issubset(value):
        missing = sorted(required - set(value))
        raise AuditRefusal(f"{path}:{line_number}: missing fields {missing}")
    return value


def audit(
    root: Path,
    *,
    data_commit: str,
    source_inventory_sha256: str,
    correction_record_sha256: str,
    correction_addendum_sha256: str,
    recorded_utc: str,
) -> dict[str, Any]:
    if not root.is_dir():
        raise AuditRefusal("manifest root is absent")
    if not GIT_SHA_RE.fullmatch(data_commit):
        raise AuditRefusal("data commit must be a full lowercase Git SHA")
    if not SHA256_RE.fullmatch(source_inventory_sha256):
        raise AuditRefusal("source inventory SHA-256 is malformed")
    if not SHA256_RE.fullmatch(correction_record_sha256):
        raise AuditRefusal("correction record SHA-256 is malformed")
    if not SHA256_RE.fullmatch(correction_addendum_sha256):
        raise AuditRefusal("correction addendum SHA-256 is malformed")
    if not recorded_utc.endswith("Z"):
        raise AuditRefusal("recorded time must be an explicit UTC value")

    paths: list[Path] = []
    for directory in sorted(path for path in root.glob("*/asr/*") if path.is_dir()):
        original = directory / "manifest.jsonl"
        corrected = directory / "manifest.r2.jsonl"
        if corrected.exists() and not original.exists():
            raise AuditRefusal(
                f"{corrected.relative_to(root)}: r2 exists without frozen original"
            )
        if original.exists():
            paths.append(corrected if corrected.exists() else original)
    if not paths:
        raise AuditRefusal("no ASR evaluation manifests found")

    all_rows = 0
    all_seconds = 0.0
    languages: set[str] = set()
    prospective_rows = 0
    prospective_seconds = 0.0
    prospective_languages: set[str] = set()
    prospective_manifests = 0
    selected_generations = Counter()
    checksum_first: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    policy = Counter()
    policy_manifests: dict[str, set[str]] = {
        "asr_train_allowed": set(),
        "missing_license_tier": set(),
        "non_test_split": set(),
    }
    manifests: list[dict[str, Any]] = []

    for path in paths:
        relative = path.relative_to(root).as_posix()
        language, task, version, filename = relative.split("/")
        if task != "asr" or filename not in {"manifest.jsonl", "manifest.r2.jsonl"}:
            raise AuditRefusal(f"unexpected manifest layout: {relative}")
        generation = "r2" if filename == "manifest.r2.jsonl" else "original"
        selected_generations[generation] += 1
        row_count = 0
        seconds = 0.0
        sources: set[str] = set()
        releases: set[str] = set()
        tiers: set[str] = set()

        with path.open("rb") as stream:
            for line_number, raw in enumerate(stream, 1):
                value = _record(path, line_number, raw)
                if value["primary_language"] != language:
                    raise AuditRefusal(
                        f"{relative}:{line_number}: path/language mismatch"
                    )
                if value["split"] != "test":
                    policy["non_test_split_rows"] += 1
                    policy_manifests["non_test_split"].add(relative)
                uses = value["allowed_use"]
                if not isinstance(uses, list) or "asr_eval" not in uses:
                    raise AuditRefusal(
                        f"{relative}:{line_number}: ASR evaluation use is absent"
                    )
                checksum = value["audio_checksum_sha256"]
                if not isinstance(checksum, str) or not SHA256_RE.fullmatch(checksum):
                    raise AuditRefusal(
                        f"{relative}:{line_number}: audio SHA-256 is malformed"
                    )
                if not isinstance(value["audio_filepath"], str):
                    raise AuditRefusal(
                        f"{relative}:{line_number}: audio path is not a string"
                    )
                if not value["audio_filepath"].startswith("s3://medzen-speech/"):
                    raise AuditRefusal(
                        f"{relative}:{line_number}: audio path leaves the MedZen bucket"
                    )
                duration = value["duration_s"]
                if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                    raise AuditRefusal(
                        f"{relative}:{line_number}: duration is not numeric"
                    )
                if duration <= 0 or duration > 30:
                    raise AuditRefusal(
                        f"{relative}:{line_number}: duration is outside (0, 30]"
                    )
                if not isinstance(value["text_normalized"], str) or not value[
                    "text_normalized"
                ].strip():
                    raise AuditRefusal(
                        f"{relative}:{line_number}: normalized reference is empty"
                    )

                location = f"{relative}:{line_number}"
                first = checksum_first.get(checksum)
                if first is None:
                    checksum_first[checksum] = location
                else:
                    duplicates.append(
                        {"audio_checksum_sha256": checksum, "first": first, "duplicate": location}
                    )

                if "asr_train" in uses:
                    policy["asr_train_rows_in_eval"] += 1
                    policy_manifests["asr_train_allowed"].add(relative)
                tier = value.get("license_tier")
                if tier is None:
                    policy["missing_license_tier_rows"] += 1
                    policy_manifests["missing_license_tier"].add(relative)
                else:
                    tiers.add(str(tier))

                row_count += 1
                seconds += float(duration)
                sources.add(str(value["source_id"]))
                releases.add(str(value["dataset_release"]))

        if row_count == 0:
            raise AuditRefusal(f"{relative}: manifest is empty")
        all_rows += row_count
        all_seconds += seconds
        languages.add(language)
        if version in PROSPECTIVE_VERSIONS:
            prospective_rows += row_count
            prospective_seconds += seconds
            prospective_languages.add(language)
            prospective_manifests += 1
        manifests.append(
            {
                "path": f"eval/{relative}",
                "sha256": _sha256(path),
                "language": language,
                "version": version,
                "rows": row_count,
                "hours": round(seconds / 3600, 6),
                "sources": sorted(sources),
                "dataset_releases": sorted(releases),
                "license_tiers": sorted(tiers),
                "prospective_independent": version in PROSPECTIVE_VERSIONS,
                "selected_generation": generation,
            }
        )

    hard_findings = []
    if duplicates:
        hard_findings.append("DUPLICATE_AUDIO_SHA256")
    if policy["asr_train_rows_in_eval"]:
        hard_findings.append("EVAL_ROWS_ADVERTISE_TRAINING_USE")
    if policy["missing_license_tier_rows"]:
        hard_findings.append("EVAL_ROWS_MISSING_LICENSE_TIER")
    if policy["non_test_split_rows"]:
        hard_findings.append("EVAL_MANIFEST_CONTAINS_NON_TEST_SPLIT")

    return {
        "record": "ASR_BASE_MODEL_EVALUATION_INPUT_DISCOVERY",
        "id": "B6-ASR-BASE-MODEL-DISCOVERY-2026-001",
        "status": "REFUSED_INPUT_FREEZE" if hard_findings else "PASS_INPUT_FREEZE",
        "recorded_utc": recorded_utc,
        "scope": "Metadata-only audit; no audio decode, model inference, training, AWS mutation, language reactivation or promotion.",
        "data_source": {
            "git_commit": data_commit,
            "inventory_path": "registry/data_sources/ingest_results.yaml",
            "inventory_sha256": source_inventory_sha256,
            "correction_record": "registry/data_sources/eval-corrections-2026-08-11.json",
            "correction_record_sha256": correction_record_sha256,
            "correction_addendum": "registry/data_sources/eval-corrections-2026-08-11.json.note",
            "correction_addendum_sha256": correction_addendum_sha256,
            "live_prefix": "s3://medzen-speech/eval/",
        },
        "evaluation_boundary": {
            "rule": "eval/<language>/**",
            "applies_to": "manifest namespace",
            "audio_object_location_is_not_scope_signal": True,
            "audio_object_requirement": "s3://medzen-speech/** plus bound SHA-256",
            "leakage_control": "full adopted train/eval audio SHA-256 disjointness",
        },
        "inventory": {
            "manifests": len(manifests),
            "rows": all_rows,
            "hours": round(all_seconds / 3600, 6),
            "languages": len(languages),
            "language_aliases": sorted(languages),
            "selected_manifest_generations": {
                "original": selected_generations["original"],
                "r2": selected_generations["r2"],
                "preference": "manifest.r2.jsonl when present; otherwise manifest.jsonl",
            },
        },
        "prospective_independent_suite": {
            "included_versions": sorted(PROSPECTIVE_VERSIONS),
            "historical_b4_v1_and_v2_holdout_excluded": True,
            "manifests": prospective_manifests,
            "rows": prospective_rows,
            "hours": round(prospective_seconds / 3600, 6),
            "languages": len(prospective_languages),
            "language_aliases": sorted(prospective_languages),
        },
        "hard_findings": hard_findings,
        "duplicates": duplicates,
        "policy_findings": {
            "asr_train_rows_in_eval": policy["asr_train_rows_in_eval"],
            "asr_train_manifests": sorted(policy_manifests["asr_train_allowed"]),
            "missing_license_tier_rows": policy["missing_license_tier_rows"],
            "missing_license_tier_manifests": sorted(
                policy_manifests["missing_license_tier"]
            ),
            "non_test_split_rows": policy["non_test_split_rows"],
            "non_test_split_manifests": sorted(
                policy_manifests["non_test_split"]
            ),
        },
        "manifests": manifests,
        "execution": {
            "zero_shot_scoring_started": False,
            "gpu_started": False,
            "training_started": False,
            "aws_mutations": 0,
            "languages_reactivated": 0,
            "required_next_state": (
                "CORRECT_DATA_METADATA_AND_DUPLICATES_THEN_REAUDIT"
                if hard_findings
                else "QUALIFY_EVALUATION_RUNTIME_THEN_REVIEW_PILOT_PACKET"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--data-commit", required=True)
    parser.add_argument("--source-inventory-sha256", required=True)
    parser.add_argument("--correction-record-sha256", required=True)
    parser.add_argument("--correction-addendum-sha256", required=True)
    parser.add_argument("--recorded-utc", required=True)
    args = parser.parse_args()
    try:
        result = audit(
            args.manifest_root,
            data_commit=args.data_commit,
            source_inventory_sha256=args.source_inventory_sha256,
            correction_record_sha256=args.correction_record_sha256,
            correction_addendum_sha256=args.correction_addendum_sha256,
            recorded_utc=args.recorded_utc,
        )
    except AuditRefusal as exc:
        result = {
            "record": "ASR_BASE_MODEL_EVALUATION_INPUT_DISCOVERY",
            "id": "B6-ASR-BASE-MODEL-DISCOVERY-2026-001",
            "status": "REFUSED_MALFORMED_INPUT",
            "reason": str(exc),
        }
        print(canonical_json(result).decode("utf-8"), end="")
        return 2
    print(canonical_json(result).decode("utf-8"), end="")
    return 2 if result["status"].startswith("REFUSED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
