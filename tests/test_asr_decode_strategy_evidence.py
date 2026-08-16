"""B3.2 evidence pack: verdicts only with complete coverage and real deltas."""

import pytest

from scripts.asr_decode_strategy_evidence import (
    MIN_ROWS,
    language_verdict,
)

POOL = 500


def _arm(cer, rows=POOL):
    return {"cer": cer, "rows": rows}


def test_incomplete_coverage_never_concludes():
    v = language_verdict("x", "INCOMPLETE", _arm(0.05), _arm(0.10), POOL)
    assert v["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_conditioned_wins_on_a_real_delta():
    v = language_verdict("x", "COMPLETE", _arm(0.080), _arm(0.100), POOL)
    assert v["verdict"] == "CONDITIONED"
    assert v["relative_cer_delta"] == pytest.approx(0.2)


def test_unconditioned_wins_when_conditioning_hurts():
    v = language_verdict("x", "COMPLETE", _arm(0.120), _arm(0.100), POOL)
    assert v["verdict"] == "UNCONDITIONED"


def test_noise_band_defaults_to_unconditioned():
    v = language_verdict("x", "COMPLETE", _arm(0.1000), _arm(0.1010), POOL)
    assert v["verdict"] == "TIE_DEFAULT_UNCONDITIONED"


def test_missing_conditioning_identifier_is_its_own_state():
    v = language_verdict("x", "COMPLETE", None, _arm(0.10), POOL)
    assert v["verdict"] == "UNCONDITIONED_ONLY"


def test_thin_conditioned_arm_refuses():
    v = language_verdict("x", "COMPLETE", _arm(0.08, rows=MIN_ROWS - 1), _arm(0.10), POOL)
    assert v["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_a_small_pool_can_still_conclude():
    """A 60-row language's full pool is the maximum obtainable evidence."""
    v = language_verdict("oromo", "COMPLETE", _arm(0.08, rows=60), _arm(0.10, rows=60), 60)
    assert v["verdict"] == "CONDITIONED"


def test_full_pool_coverage_is_required_for_the_unconditioned_arm():
    v = language_verdict("x", "COMPLETE", _arm(0.08), _arm(0.10, rows=POOL - 1), POOL)
    assert v["verdict"] == "INSUFFICIENT_EVIDENCE"
