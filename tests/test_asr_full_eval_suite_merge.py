"""The suite merge tool: pooled counts, gap-free coverage proof, refusals."""

import json
from pathlib import Path

import pytest

from scripts.asr_full_eval_suite_merge import (
    SuiteMergeRefusal,
    build_report,
)

POOL = {"alpha": 4, "beta": 2}


def _write_manifest(root: Path):
    path = root / "platform/manifests/ASR-FULL-EVAL-SUITE-SHARD-MANIFEST-2026-002.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pool": {"per_language_validated_rows": POOL}}))


def _write_run(root: Path, attempt: int, rows: list[tuple[str, int]], *, outcome="PASS_PILOT"):
    directory = root / f"platform/evidence/receipts/ASR-BASE-MODEL-TEST-A{attempt}-LIVE"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(json.dumps({"outcome": outcome, "attempt": attempt}))
    (directory / "pilot-selection.json").write_text(json.dumps({
        "rows": [{"language": lang, "selection_ordinal": ordinal} for lang, ordinal in rows],
    }))
    per_language = {}
    by_lang: dict[str, int] = {}
    for lang, _ in rows:
        by_lang[lang] = by_lang.get(lang, 0) + 1
    for lang, count in by_lang.items():
        per_language[f"modelX|unconditioned|{lang}"] = {
            "rows": count,
            "character_errors": 10 * count,
            "reference_characters": 100 * count,
            "word_errors": 4 * count,
            "reference_words": 10 * count,
            "cap_hits": 1 if attempt % 2 else 0,
            "eos_failures": 0,
            "latency_median_seconds": 0.5,
            "latency_p95_seconds": 1.0,
            "cer": 0.1,
            "wer": 0.4,
            "rtf_median": 0.1,
            "rtf_p95": 0.2,
        }
    (directory / "aggregate-report.json").write_text(json.dumps({
        "aggregate": {"per_language": per_language, "groups": {}},
    }))


def test_disjoint_runs_merge_to_gap_free_coverage(tmp_path):
    _write_manifest(tmp_path)
    _write_run(tmp_path, 32, [("alpha", 1), ("alpha", 2), ("beta", 1)])
    _write_run(tmp_path, 33, [("alpha", 3), ("alpha", 4), ("beta", 2)])
    report = build_report(tmp_path, 32)
    assert report["status"] == "PASS_GAP_FREE_COVERAGE"
    assert report["coverage"]["covered_rows"] == 6
    merged = report["metrics"]["per_language"]["modelX|unconditioned|alpha"]
    assert merged["rows"] == 4
    assert merged["cer"] == pytest.approx(0.1)
    assert merged["cap_hits"] == 1  # attempt 33 (odd) flagged one, attempt 32 (even) none
    groups = report["metrics"]["groups"]["modelX|unconditioned"]
    assert groups["rows"] == 6 and groups["reference_words"] == 60


def test_gap_is_reported_not_papered_over(tmp_path):
    _write_manifest(tmp_path)
    _write_run(tmp_path, 32, [("alpha", 1), ("alpha", 2), ("beta", 1), ("beta", 2)])
    report = build_report(tmp_path, 32)
    assert report["status"] == "COVERAGE_INCOMPLETE"
    alpha = report["coverage"]["languages"]["alpha"]
    assert alpha["state"] == "INCOMPLETE"
    assert alpha["missing_ranges"] == [[3, 4]]
    assert report["coverage"]["languages"]["beta"]["state"] == "COMPLETE"


def test_overlap_is_flagged(tmp_path):
    _write_manifest(tmp_path)
    _write_run(tmp_path, 32, [("alpha", 1), ("alpha", 2)])
    _write_run(tmp_path, 33, [("alpha", 2), ("alpha", 3), ("alpha", 4), ("beta", 1), ("beta", 2)])
    report = build_report(tmp_path, 32)
    assert report["status"] == "COVERAGE_INCOMPLETE"
    assert report["coverage"]["languages"]["alpha"]["overlapping_ordinals"] == [2]


def test_non_pass_and_pre_suite_runs_are_ignored(tmp_path):
    _write_manifest(tmp_path)
    _write_run(tmp_path, 31, [("alpha", 1)])
    _write_run(tmp_path, 33, [("alpha", 1)], outcome="FAILED_CLOSED_EXECUTION")
    with pytest.raises(SuiteMergeRefusal, match="no PASS suite runs"):
        build_report(tmp_path, 32)


def test_out_of_pool_language_refuses(tmp_path):
    _write_manifest(tmp_path)
    _write_run(tmp_path, 32, [("gamma", 1)])
    with pytest.raises(SuiteMergeRefusal, match="outside the pool"):
        build_report(tmp_path, 32)
