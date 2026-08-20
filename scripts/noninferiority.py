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
        diff = float(r["candidate_errors"]) - float(r["baseline_errors"])
        clusters.setdefault(str(r["cluster_id"]), []).append(
            (diff, float(r["reference_words"])))
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


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps(clustered_noninferiority(
        payload["rows"], margin=payload["margin"],
        **{k: payload[k] for k in ("iterations", "seed", "alpha")
           if k in payload}), sort_keys=True))
