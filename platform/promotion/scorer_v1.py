"""The pinned promotion scorer (B6v2 round 12, Codex: error counts must
RECOMPUTE from bound reference/hypothesis text — supplied numbers prove
nothing). Word-level Levenshtein errors, whitespace tokenization over
already-normalized text. This file's sha256 is the packet's
scorer_sha256; its bytes ship in every promotion bundle and BOTH the
admission pipeline and the runtime execute it (after signature
verification) to recompute every row.
"""
from __future__ import annotations


def reference_words(reference: str) -> int:
    return len(reference.split())


def score_errors(reference: str, hypothesis: str) -> int:
    """Word-level edit distance (substitutions+insertions+deletions)."""
    ref = reference.split()
    hyp = hypothesis.split()
    previous = list(range(len(hyp) + 1))
    for i, ref_token in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_token in enumerate(hyp, start=1):
            cost = 0 if ref_token == hyp_token else 1
            current[j] = min(previous[j] + 1,        # deletion
                              current[j - 1] + 1,     # insertion
                              previous[j - 1] + cost) # substitution
        previous = current
    return previous[len(hyp)]
