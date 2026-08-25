"""Adversarial tests for the Holm gate (owner blocker 2): Holm rejection is the
sole positive-qualification decision and is NOT the raw-CI decision."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from arm2_holm import holm_reject, qualifies, raw_ci_passes  # noqa: E402


def test_the_owner_24x_p04_counterexample():
    # 24 tests each raw p=0.04, FWER 0.05: raw rule passes ALL, Holm rejects 0
    p = [0.04] * 24
    assert sum(raw_ci_passes(p, 0.05)) == 24, "raw rule passes all 24"
    assert sum(holm_reject(p, 0.05)) == 0, "Holm must reject none"
    assert qualifies(p, 0.05) is False, "raw success is NOT qualification"


def test_holm_is_stricter_than_raw_in_general():
    # a family where the smallest clears Holm but a 0.04 does not
    p = [0.001, 0.04, 0.04]      # m=3: thresholds 0.0167, 0.025, 0.05
    rej = holm_reject(p, 0.05)
    assert rej == [True, False, False]  # step-down halts after the first
    assert raw_ci_passes(p, 0.05) == [True, True, True]
    assert qualifies(p, 0.05) is False


def test_holm_qualifies_when_every_test_clears_its_step():
    p = [0.001, 0.002, 0.003, 0.004, 0.005, 0.006]  # m=6, all small
    assert all(holm_reject(p, 0.05))
    assert qualifies(p, 0.05) is True


def test_holm_alignment_to_input_order():
    p = [0.05, 0.0001, 0.9]      # m=3
    rej = holm_reject(p, 0.05)
    # only the 0.0001 (needs <= 0.05/3=0.0167) is rejected; step halts at 0.05
    assert rej == [False, True, False]


def test_empty_family_does_not_qualify():
    assert qualifies([], 0.05) is False
