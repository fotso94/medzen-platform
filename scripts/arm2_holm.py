"""Executable Holm-Bonferroni step-down — the SOLE positive-qualification gate
for the Arm-2 KD comparison (rev 007; helper corrected at implementation).

Owner blocker 2: raw 95%-CI success (raw per-test p <= 0.05) is NOT equivalent to
Holm-adjusted significance for any family size m > 1. E.g. 24 tests each with raw
p = 0.04 all pass the raw rule but Holm rejects NONE (0.04 > 0.05/24). The raw CI
is DESCRIPTIVE only; qualification is decided solely by the Holm gate below.

Implementation correction (final-review mandate): Stage-1 qualification MUST Holm-
correct over the COMPLETE family (m = 24 = 4 candidates x 6 positive tests) and
then check a single candidate's six INDEXED results against that full-family mask
-- never re-run Holm over six p-values in isolation. Use `candidate_qualifies`
for Stage 1. `qualifies` is the m == family case (Stage 2, where the finalist's
six tests ARE the whole family). All inputs are validated.
"""
from __future__ import annotations

import math


def _validate_pvalues(pvalues) -> None:
    seq = list(pvalues)
    if not seq:
        raise ValueError("empty p-value family")
    for p in seq:
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            raise ValueError(f"p-value {p!r} is not a real number")
        if not math.isfinite(p) or p < 0.0 or p > 1.0:
            raise ValueError(f"p-value {p!r} is not a finite value in [0, 1]")


def _validate_alpha(alpha) -> None:
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) \
            or not math.isfinite(alpha) or not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha {alpha!r} must be a real number in (0, 1)")


def holm_reject(pvalues, alpha: float = 0.05) -> list[bool]:
    """Return, aligned to the INPUT order, which hypotheses Holm rejects at
    family-wise error rate `alpha` OVER THE WHOLE `pvalues` FAMILY. Step-down:
    sort ascending; reject the rank-j ordered p iff p(i) <= alpha/(m-i+1) for
    ALL i <= j (stop at the first failure). Validates its inputs."""
    _validate_pvalues(pvalues)
    _validate_alpha(alpha)
    seq = list(pvalues)
    m = len(seq)
    order = sorted(range(m), key=lambda i: seq[i])
    rejected = [False] * m
    for rank, idx in enumerate(order):          # rank is 0-based
        if seq[idx] <= alpha / (m - rank):      # = alpha/(m-(rank+1)+1)
            rejected[idx] = True
        else:
            break                               # step-down halts
    return rejected


def candidate_qualifies(family_pvalues, candidate_indices,
                        alpha: float = 0.05) -> bool:
    """Stage-1 gate: Holm-correct over the COMPLETE family, then this candidate
    QUALIFIES iff EVERY one of its indexed tests is rejected in that full-family
    mask. `candidate_indices` selects the candidate's tests within
    `family_pvalues` (e.g. its 6 tests inside the 24-test Stage-1 family)."""
    mask = holm_reject(family_pvalues, alpha)   # validates family + alpha
    idxs = list(candidate_indices)
    if not idxs:
        raise ValueError("candidate_indices is empty")
    m = len(mask)
    for i in idxs:
        if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < m):
            raise ValueError(f"candidate index {i!r} out of range [0, {m})")
    if len(set(idxs)) != len(idxs):
        raise ValueError("candidate_indices has duplicates")
    return all(mask[i] for i in idxs)


def qualifies(pvalues, alpha: float = 0.05) -> bool:
    """The m == family case (Stage 2): a family QUALIFIES iff EVERY test is
    Holm-rejected at FWER `alpha`. For Stage 1 use `candidate_qualifies` with
    the full 24-test family, NOT this over six isolated p-values."""
    return all(holm_reject(pvalues, alpha))


def raw_ci_passes(pvalues, alpha: float = 0.05) -> list[bool]:
    """DESCRIPTIVE ONLY (never the gate): the raw one-sided per-test decision
    (raw p <= alpha == the raw 95% CI clears its threshold). Provided so the
    contradiction is explicit and testable: raw_ci_passes can be all-True while
    the Holm gate qualifies nothing."""
    _validate_pvalues(pvalues)
    _validate_alpha(alpha)
    return [p <= alpha for p in pvalues]
