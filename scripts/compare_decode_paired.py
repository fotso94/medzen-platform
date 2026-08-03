#!/usr/bin/env python3
"""Paired bootstrap for a decode-strategy comparison.

Two decode arms are scored on the SAME utterances, so their per-arm confidence
intervals are not independent. Comparing them by eye ("0.53 vs 0.57, and the
intervals overlap") both wastes the pairing and invites the wrong conclusion.
The right statistic is the distribution of the DIFFERENCE under a bootstrap
that resamples utterances once and scores both arms on that resample.

    python scripts/compare_decode_paired.py --language pidgin --task tts \
        --arm-a en_token --arm-b auto
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def corpus_wer(refs, hyps):
    import jiwer
    keep = [(r, h) for r, h in zip(refs, hyps) if r.strip()]
    if not keep:
        return float("nan")
    return jiwer.wer([r for r, _ in keep], [h for _, h in keep])


def load(language, task, arm):
    f = ROOT / "results" / "baseline" / arm / "detail" / f"{language}_{task}.jsonl"
    if not f.exists():
        sys.exit(f"missing detail for arm '{arm}': {f}")
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--arm-a", required=True)
    ap.add_argument("--arm-b", required=True)
    ap.add_argument("--iters", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    A, B = load(a.language, a.task, a.arm_a), load(a.language, a.task, a.arm_b)
    if [x["audio_filepath"] for x in A] != [x["audio_filepath"] for x in B]:
        sys.exit("arms are not utterance-aligned; cannot pair")

    refs = [x["reference"] for x in A]
    ha, hb = [x["hypothesis"] for x in A], [x["hypothesis"] for x in B]
    wa, wb = corpus_wer(refs, ha), corpus_wer(refs, hb)
    obs = wa - wb

    rng = random.Random(a.seed)
    n = len(refs)
    diffs = []
    for _ in range(a.iters):
        idx = [rng.randrange(n) for _ in range(n)]
        r = [refs[i] for i in idx]
        if any(x.strip() for x in r):
            diffs.append(corpus_wer(r, [ha[i] for i in idx])
                         - corpus_wer(r, [hb[i] for i in idx]))
    diffs.sort()
    lo, hi = diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs))]
    p_a = sum(1 for d in diffs if d < 0) / len(diffs)
    n_diff = sum(1 for x, y in zip(ha, hb) if x != y)
    speakers = len({x.get("speaker_id") for x in A})
    crosses = lo < 0 < hi

    out = {
        "language": a.language, "task": a.task,
        "arm_a": a.arm_a, "arm_b": a.arm_b,
        "wer_a": round(wa, 4), "wer_b": round(wb, 4),
        "observed_diff": round(obs, 4),
        "paired_ci95": [round(lo, 4), round(hi, 4)],
        "p_a_better": round(p_a, 3),
        "n_utterances": n, "n_speakers": speakers,
        "n_hypotheses_differing": n_diff,
        "distinguishable": not crosses,
        "verdict": ("PROVISIONAL — interval crosses zero; arms are not "
                    "statistically distinguishable on this eval set"
                    if crosses else "distinguishable at 95%"),
    }
    print(json.dumps(out, indent=2))
    p = ROOT / "results" / "baseline" / f"paired_{a.language}_{a.task}_{a.arm_a}_vs_{a.arm_b}.json"
    p.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
