"""Executable Holm-Bonferroni step-down — the SOLE positive-qualification gate
for the Arm-2 KD comparison (rev 007).

Owner blocker 2: raw 95%-CI success (raw per-test p <= 0.05) is NOT equivalent to
Holm-adjusted significance for any family size m > 1. E.g. 24 tests each with raw
p = 0.04 all pass the raw rule but Holm rejects NONE (0.04 > 0.05/24). The raw CI
is DESCRIPTIVE only; qualification is decided solely by `qualifies()` below.
"""
from __future__ import annotations


def holm_reject(pvalues, alpha: float = 0.05) -> list[bool]:
    """Return, aligned to the INPUT order, which hypotheses Holm rejects at
    family-wise error rate `alpha`. Step-down: sort ascending; reject the
    rank-j ordered p iff p(i) <= alpha/(m-i+1) for ALL i <= j (stop at the
    first failure)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    rejected = [False] * m
    for rank, idx in enumerate(order):          # rank is 0-based
        threshold = alpha / (m - rank)          # = alpha/(m - (rank+1) + 1)
        if pvalues[idx] <= threshold:
            rejected[idx] = True
        else:
            break                               # step-down halts
    return rejected


def qualifies(pvalues, alpha: float = 0.05) -> bool:
    """A candidate QUALIFIES iff EVERY one of its positive tests is Holm-
    rejected at FWER `alpha`. This is the sole positive-gate decision; the
    per-test raw CI/p is never the gate."""
    if not pvalues:
        return False
    return all(holm_reject(pvalues, alpha))


def raw_ci_passes(pvalues, alpha: float = 0.05) -> list[bool]:
    """DESCRIPTIVE ONLY (never the gate): the raw one-sided per-test decision
    (raw p <= alpha == the raw 95% CI clears its threshold). Provided so the
    contradiction is explicit and testable: raw_ci_passes can be all-True while
    qualifies() is False."""
    return [p <= alpha for p in pvalues]
