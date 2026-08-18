"""Task E tests: the recommendation rules, ordered and fail-closed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from b5_training_recommendation import (  # noqa: E402
    RecommendationRefusal,
    build_recommendation,
    licence_hours_from_manifest_rows,
    markdown_table,
    recommend_language,
)


def _row(policy: str, seconds: float = 3600.0, split: str = "train") -> dict:
    return {"split": split, "allowed_use": ["asr_train"],
            "license_policy": policy, "duration_s": seconds}


def _merge(per_language: dict, status: str = "PASS_GAP_FREE_COVERAGE") -> dict:
    return {"coverage": {"status": status},
            "metrics": {"per_language": per_language}}


BASE = "omniASR_CTC_1B_v2|unconditioned"


def test_licence_hours_bucket_correctly():
    # sharealike cleared by LIC-2026-002 (owner-attested legal sign-off):
    # it now buckets as ATTRIBUTION-class (the obligation follows the weights)
    hours = licence_hours_from_manifest_rows([
        _row("cc0"), _row("commercial_ok"), _row("cc_by_4_0"),
        _row("sharealike_review", 7200), _row("cc0", split="eval"),
    ])
    assert hours == {"clear": 2.0, "attribution": 3.0,
                     "blocked_sharealike": 0.0, "never_train": 0.0}


def test_unknown_policy_refuses():
    with pytest.raises(RecommendationRefusal, match="unusable"):
        licence_hours_from_manifest_rows([_row("wtfpl")])


def test_rule_order_blocked_before_baseline():
    """A language with ONLY sharealike data is BLOCKED even without a
    baseline — the legal wall comes first."""
    entry = recommend_language("serer", None, {
        "clear": 0.0, "attribution": 0.0, "blocked_sharealike": 12.0,
        "never_train": 0.0})
    assert entry["recommendation"] == "BLOCKED_PENDING_LEGAL"


def test_no_baseline_holds_training():
    entry = recommend_language("newlang", None, {
        "clear": 50.0, "attribution": 0.0, "blocked_sharealike": 0.0,
        "never_train": 0.0})
    assert entry["recommendation"] == "NO_EVAL_BASELINE"


def test_insufficient_data_threshold():
    entry = recommend_language("tiny", {"wer": 0.5, "cer": 0.2}, {
        "clear": 0.5, "attribution": 0.0, "blocked_sharealike": 0.0,
        "never_train": 0.0})
    assert entry["recommendation"] == "INSUFFICIENT_DATA"


def test_train_with_attribution_flag():
    entry = recommend_language("wolof", {"wer": 0.4, "cer": 0.15}, {
        "clear": 3.0, "attribution": 2.0, "blocked_sharealike": 0.0,
        "never_train": 0.0})
    assert entry["recommendation"] == "TRAIN"
    assert entry["attribution_required"] is True
    assert entry["trainable_hours"] == 5.0


def test_incomplete_coverage_refuses_by_default():
    with pytest.raises(RecommendationRefusal, match="incomplete"):
        build_recommendation(_merge({}, status="COVERAGE_INCOMPLETE"), {})
    result = build_recommendation(_merge({}, status="COVERAGE_INCOMPLETE"),
                                  {}, allow_incomplete=True)
    assert result["coverage_status"] == "COVERAGE_INCOMPLETE"


def test_table_sorts_train_first_by_hours_and_summarizes():
    merge = _merge({
        f"{BASE}|biglang": {"wer": 0.3, "cer": 0.1},
        f"{BASE}|smalllang": {"wer": 0.5, "cer": 0.2},
        f"{BASE}|nodata": {"wer": 0.4, "cer": 0.1},
        "omniASR_LLM_1B_v2|unconditioned|biglang": {"wer": 0.2, "cer": 0.08},
    })
    rows = {
        "biglang": [_row("cc0", 3600 * 40)],
        "smalllang": [_row("cc_by_4_0", 3600 * 5)],
        "blockedlang": [_row("sharealike_review", 3600 * 9)],
    }
    result = build_recommendation(merge, rows)
    order = [(e["language"], e["recommendation"]) for e in result["languages"]]
    assert order[0] == ("biglang", "TRAIN")
    assert order[1] == ("smalllang", "TRAIN")
    # post-LIC-2026-002 the sharealike language is trainable; it lacks an
    # eval baseline so the gate-first rule holds it there instead
    assert ("blockedlang", "NO_EVAL_BASELINE") in order
    assert ("nodata", "INSUFFICIENT_DATA") in order
    assert result["summary"]["TRAIN"] == 2
    # LLM rows must not create baseline entries
    assert all(e["language"] != "omniASR_LLM_1B_v2" for e in result["languages"])
    text = markdown_table(result)
    assert "biglang" in text and "TRAIN" in text
