"""The canonical scorer must never destroy a tone mark, and must never pick a
tone policy on the caller's behalf.

Regression cover for the v1 defect recorded in
platform/evidence/CM-PILOT-SCORING-VERDICT-2026-001.json
(error_decomposition_in_domain): a combining mark with no precomposed NFC
form is category Mn, is not alphanumeric, and v1 mapped it to a SPACE —
splitting the word and destroying the mark in one step, while precomposed
vowels passed through untouched.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "services" / "asr-eval-runtime"
for entry in (str(ROOT), str(PACKAGE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from medzen_asr_eval.harness import EvaluationRefusal
from medzen_asr_eval.metrics import (
    LEGACY_V1,
    POLICY_LABELS,
    TONE_INSENSITIVE,
    TONE_SENSITIVE,
    aggregate,
    error_counts,
    normalize_text,
)


# The three verified cases. Each carries a combining mark on a vowel that has
# NO precomposed form (schwa, open-o), which is exactly where v1 broke.
DESTROYED = [
    ("pə́tsəm", "pə tsəm", "pətsəm"),
    ("ntsɔ́b", "ntsɔ b", "ntsɔb"),
    ("menɔ̀ɔn", "menɔ ɔn", "menɔɔn"),
]

# The control. Precomposed vowels are single alphanumeric codepoints, so they
# were never damaged — v1 and the tone-sensitive policy must agree here, which
# is what made the v1 damage SELECTIVE and therefore easy to miss.
PRECOMPOSED = ["nkáb", "lǎʼ"]


@pytest.mark.parametrize("written,v1_scored,detoned", DESTROYED)
def test_combining_mark_stays_attached_to_its_base_character(
    written: str, v1_scored: str, detoned: str
) -> None:
    assert normalize_text(written, policy=TONE_SENSITIVE) == written
    assert normalize_text(written, policy=TONE_SENSITIVE).split() == [written]
    # the defect, pinned so it cannot come back unnoticed
    assert normalize_text(written, policy=LEGACY_V1) == v1_scored
    # a declared tone-insensitive read drops the mark but must NOT split
    assert normalize_text(written, policy=TONE_INSENSITIVE) == detoned


@pytest.mark.parametrize("written", PRECOMPOSED)
def test_precomposed_vowels_are_unchanged(written: str) -> None:
    assert normalize_text(written, policy=TONE_SENSITIVE) == written
    assert normalize_text(written, policy=LEGACY_V1) == written


def test_reference_characters_does_not_silently_drop_marks() -> None:
    """v1 lost exactly one character per destroyed mark, so CER was computed
    against a reference that was shorter than the reference actually is."""
    written = "pə́tsəm ntsɔ́b menɔ̀ɔn"
    marks = sum(unicodedata.category(char) == "Mn"
                for char in unicodedata.normalize("NFC", written))
    assert marks == 3

    tone_sensitive = error_counts(written, written, policy=TONE_SENSITIVE)
    legacy = error_counts(written, written, policy=LEGACY_V1)

    expected = len(unicodedata.normalize("NFC", written).replace(" ", ""))
    assert tone_sensitive["reference_characters"] == expected
    assert legacy["reference_characters"] == expected - marks
    # and the split inflated the word denominator on top of that
    assert tone_sensitive["reference_words"] == 3
    assert legacy["reference_words"] == 6


def test_tone_insensitive_is_consistent_and_is_not_the_legacy_normaliser() -> None:
    """Deleting marks is a POLICY, not the repair. Declared tone-insensitive
    scoring removes tone from precomposed vowels too; v1 removed it only from
    the vowels that could not precompose, which is why v1 is neither a
    tone-sensitive nor a tone-insensitive metric."""
    assert normalize_text("nkáb", policy=TONE_INSENSITIVE) == "nkab"
    assert normalize_text("nkáb", policy=LEGACY_V1) == "nkáb"
    for written, _, detoned in DESTROYED:
        assert normalize_text(written, policy=TONE_INSENSITIVE) == detoned
        assert normalize_text(written, policy=LEGACY_V1) != detoned


@pytest.mark.parametrize("policy", sorted(POLICY_LABELS))
def test_every_policy_is_idempotent(policy: str) -> None:
    """pilot.py normalises the hypothesis and then hands it to error_counts,
    which normalises again. A non-idempotent normaliser would score a second
    pass differently from the first."""
    for written in [w for w, _, _ in DESTROYED] + PRECOMPOSED + [
        "  HÉLLO,\nWORLD! ", "Ésáŋ nkab gie ngwɔ́ mé lɔg gÿo láʼ ngú’ tʉ̂a."
    ]:
        once = normalize_text(written, policy=policy)
        assert normalize_text(once, policy=policy) == once


def test_a_mark_with_no_surviving_base_never_becomes_its_own_token() -> None:
    """Turning a mark into a space is the defect. A stranded mark is dropped."""
    assert normalize_text("́abc", policy=TONE_SENSITIVE) == "abc"
    assert normalize_text("a!́b", policy=TONE_SENSITIVE) == "a b"


def test_the_policy_must_be_named_and_is_never_guessed() -> None:
    with pytest.raises(TypeError):
        normalize_text("nkáb")  # type: ignore[call-arg]
    with pytest.raises(EvaluationRefusal, match="unknown normalization policy"):
        normalize_text("nkáb", policy="whatever-seems-reasonable")
    with pytest.raises(EvaluationRefusal, match="must be a string"):
        normalize_text(None, policy=TONE_SENSITIVE)  # type: ignore[arg-type]


def _row(policy: str | None) -> dict:
    errors = {"word_errors": 1, "reference_words": 2,
              "character_errors": 1, "reference_characters": 6}
    if policy is not None:
        errors["normalization_policy"] = policy
    return {"status": "PASS_ROW_INFERENCE", "candidate": "c", "mode": "unconditioned",
            "language": "ngiemboon", "source_id": "cvcm1", "errors": errors,
            "latency_seconds": 1.0, "rtf": 0.5, "eos_failure": False, "cap_hit": False}


def test_every_row_carries_its_policy_and_the_aggregate_labels_it() -> None:
    counts = error_counts("nkáb", "nkab", policy=TONE_SENSITIVE)
    assert counts["normalization_policy"] == TONE_SENSITIVE

    summary = aggregate([_row(TONE_SENSITIVE)], [100.0])
    assert summary["normalization_policy"] == TONE_SENSITIVE
    assert summary["normalization_policy_label"] == POLICY_LABELS[TONE_SENSITIVE]


def test_aggregate_refuses_undeclared_or_mixed_policies() -> None:
    with pytest.raises(EvaluationRefusal, match="no normalization_policy"):
        aggregate([_row(None)], [100.0])
    with pytest.raises(EvaluationRefusal, match="mix normalization policies"):
        aggregate([_row(TONE_SENSITIVE), _row(TONE_INSENSITIVE)], [100.0])
