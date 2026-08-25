"""Adversarial tests for the Holm gate (owner blocker 2 + final-review helper
correction): Holm rejection is the sole positive-qualification decision, it is
NOT the raw-CI decision, Stage-1 qualification corrects over the COMPLETE 24-test
family and indexes each candidate's six results, and all inputs are validated."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from arm2_holm import (  # noqa: E402
    holm_reject, qualifies, candidate_qualifies, raw_ci_passes)


# ---- the owner's counterexample + raw-vs-Holm ----------------------------

def test_the_owner_24x_p04_counterexample():
    p = [0.04] * 24
    assert sum(raw_ci_passes(p, 0.05)) == 24      # raw rule passes all 24
    assert sum(holm_reject(p, 0.05)) == 0         # Holm rejects none
    assert qualifies(p, 0.05) is False


def test_holm_step_down_halts_and_aligns_to_input_order():
    p = [0.05, 0.0001, 0.9]                        # m=3
    assert holm_reject(p, 0.05) == [False, True, False]


# ---- the mandated correction: full-family Holm, then index the candidate --

def test_stage1_holm_is_over_the_full_24_family_then_indexed():
    # candidate A = 6 tiny p-values; the other 18 are large. Over the FULL 24,
    # Holm rejects exactly A's 6 (0.0005 <= 0.05/19) and halts at the 0.5s.
    family = [0.0005] * 6 + [0.5] * 18
    A = list(range(0, 6))
    B = list(range(6, 12))
    assert candidate_qualifies(family, A) is True
    assert candidate_qualifies(family, B) is False
    # mixed: A qualifies, a candidate mixing one large test does not
    A_with_one_bad = [0, 1, 2, 3, 4, 6]           # index 6 is a 0.5
    assert candidate_qualifies(family, A_with_one_bad) is False


def test_full_family_correction_is_stricter_than_six_in_isolation():
    # six p=0.008 in isolation (Stage-2 m=6) qualify: 0.008 <= 0.05/6.
    six = [0.008] * 6
    assert qualifies(six, 0.05) is True
    # but the SAME six inside the 24-test Stage-1 family do NOT (0.008 > 0.05/24)
    family = [0.008] * 6 + [0.008] * 18           # all 24 = 0.008
    assert candidate_qualifies(family, list(range(6))) is False
    # this is exactly the bug the correction fixes: never Holm-over-six for
    # a Stage-1 candidate.


def test_stage2_qualifies_when_the_whole_six_family_clears():
    six = [0.001, 0.002, 0.003, 0.004, 0.005, 0.006]   # m=6
    assert all(holm_reject(six, 0.05)) and qualifies(six, 0.05) is True


# ---- malformed-input validation ------------------------------------------

@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_invalid_pvalues_are_rejected(bad):
    with pytest.raises(ValueError):
        holm_reject([0.01, bad], 0.05)
    with pytest.raises(ValueError):
        raw_ci_passes([0.01, bad], 0.05)


def test_empty_family_is_rejected():
    with pytest.raises(ValueError):
        holm_reject([], 0.05)


@pytest.mark.parametrize("bad_alpha", [0.0, 1.0, -0.1, 1.5, float("nan"), True])
def test_invalid_alpha_is_rejected(bad_alpha):
    with pytest.raises(ValueError):
        holm_reject([0.01, 0.02], bad_alpha)


def test_candidate_indices_must_be_valid():
    family = [0.001] * 6
    with pytest.raises(ValueError):
        candidate_qualifies(family, [])            # empty
    with pytest.raises(ValueError):
        candidate_qualifies(family, [0, 6])        # out of range
    with pytest.raises(ValueError):
        candidate_qualifies(family, [0, 0])        # duplicate
