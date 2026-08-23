"""The pinned promotion scorer (B6v2 round 12, Codex: error counts must
RECOMPUTE from bound reference/hypothesis text — supplied numbers prove
nothing). Word-level Levenshtein errors, whitespace tokenization over
already-normalized text.

Round 13 (Codex, arbitrary code execution through bundled scorer.py):
this scorer is BAKED into the reviewed loader image as
medzen_model_loader/scorer_v1.py (byte-identical to this reference copy,
pinned by test) and resolved by scorer_id "scorer_v1" + the sha256 of the
baked module's own bytes. Bundles carry NO code; nothing from evidence is
ever compiled or executed.
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
