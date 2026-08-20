#!/usr/bin/env python3
"""Paired, clustered non-inferiority validator (PROMOTION-PROTOCOL-2026-001).

Input rows: one per sealed-holdout utterance, with the SAME utterance
scored by baseline and candidate (paired), and a cluster id (speaker where
real, session otherwise — the Tier-2 record says which per pool).

Method: cluster bootstrap on the paired per-utterance WER-difference
(candidate - baseline), resampling CLUSTERS with replacement — utterances
of one speaker are correlated, so utterance-level resampling understates
variance. Non-inferior iff the upper percentile CI bound of the mean
difference is below the predeclared margin. Deterministic under seed.
"""
from __future__ import annotations

import json
import random
import sys


def clustered_noninferiority(rows: list[dict], *, margin: float,
                              iterations: int = 10_000, seed: int = 20260821,
                              alpha: float = 0.05) -> dict:
    if margin <= 0:
        raise ValueError("a non-inferiority margin must be positive")
    if not rows:
        raise ValueError("no paired rows supplied")
    clusters: dict[str, list[float]] = {}
    for r in rows:
        base = float(r["baseline_errors"])
        cand = float(r["candidate_errors"])
        ref = float(r["reference_words"])
        # Codex review #8: invalid inputs were not refused
        if base < 0 or cand < 0 or ref <= 0 or not all(
                x == x and abs(x) != float("inf") for x in (base, cand, ref)):
            raise ValueError(
                f"invalid paired row (errors must be >= 0, reference_words "
                f"> 0, all finite): {r}")
        diff = cand - base
        clusters.setdefault(str(r["cluster_id"]), []).append((diff, ref))
    if len(clusters) < 2:
        raise ValueError("need >= 2 clusters for a clustered bootstrap")
    ids = sorted(clusters)
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        err = words = 0.0
        for cid in (rng.choice(ids) for _ in ids):
            for diff, ref in clusters[cid]:
                err += diff
                words += ref
        means.append(err / max(words, 1.0))
    means.sort()
    upper = means[min(len(means) - 1, int((1 - alpha) * iterations))]
    point = (sum(d for c in clusters.values() for d, _ in c)
             / max(sum(w for c in clusters.values() for _, w in c), 1.0))
    return {"method": "paired_clustered_bootstrap", "margin": margin,
            "alpha": alpha, "iterations": iterations, "seed": seed,
            "clusters": len(clusters), "rows": len(rows),
            "point_wer_diff": point, "upper_ci": upper,
            "non_inferior": upper < margin}


def clustered_relative_improvement(rows: list[dict], *,
                                    min_relative_gain: float,
                                    iterations: int = 10_000,
                                    seed: int = 20260821,
                                    alpha: float = 0.05) -> dict:
    """The kinyarwanda-style TARGET decision (Codex review #8: absolute
    non-inferiority and relative improvement are different decisions).
    Cluster bootstrap on relative WER gain (baseline - candidate) /
    baseline; improved iff the LOWER CI bound exceeds min_relative_gain."""
    if not (0 < min_relative_gain < 1):
        raise ValueError("min_relative_gain must be in (0, 1)")
    if not rows:
        raise ValueError("no paired rows supplied")
    clusters: dict[str, list[tuple[float, float, float]]] = {}
    for r in rows:
        base = float(r["baseline_errors"])
        cand = float(r["candidate_errors"])
        ref = float(r["reference_words"])
        if base < 0 or cand < 0 or ref <= 0 or not all(
                x == x and abs(x) != float("inf") for x in (base, cand, ref)):
            raise ValueError(f"invalid paired row: {r}")
        clusters.setdefault(str(r["cluster_id"]), []).append((base, cand, ref))
    if len(clusters) < 2:
        raise ValueError("need >= 2 clusters for a clustered bootstrap")
    ids = sorted(clusters)
    rng = random.Random(seed)
    gains = []
    for _ in range(iterations):
        b = c = w = 0.0
        for cid in (rng.choice(ids) for _ in ids):
            for base, cand, ref in clusters[cid]:
                b += base
                c += cand
                w += ref
        base_wer = b / max(w, 1.0)
        cand_wer = c / max(w, 1.0)
        gains.append((base_wer - cand_wer) / max(base_wer, 1e-12))
    gains.sort()
    lower = gains[max(0, int(alpha * iterations) - 1)]
    tb = sum(x for cl in clusters.values() for x, _, _ in cl)
    tc = sum(x for cl in clusters.values() for _, x, _ in cl)
    tw = max(sum(x for cl in clusters.values() for _, _, x in cl), 1.0)
    point = ((tb / tw) - (tc / tw)) / max(tb / tw, 1e-12)
    return {"method": "paired_clustered_bootstrap_relative",
            "min_relative_gain": min_relative_gain, "alpha": alpha,
            "iterations": iterations, "seed": seed,
            "clusters": len(clusters), "rows": len(rows),
            "point_relative_gain": point, "lower_ci": lower,
            "improved": lower > min_relative_gain}


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps(clustered_noninferiority(
        payload["rows"], margin=payload["margin"],
        **{k: payload[k] for k in ("iterations", "seed", "alpha")
           if k in payload}), sort_keys=True))
