#!/usr/bin/env python3
"""A5 gate engine — scores a candidate against its language's thresholds.

This is what stands between a training run and production. It is deliberately
mechanical: thresholds come from registry/gates/, metrics come from the run,
and the verdict is arithmetic. No judgement call at promotion time, because
judgement at promotion time is how deadlines win arguments against evidence.

    python scripts/evaluate_gates.py --language pidgin --metrics run.json
    python scripts/evaluate_gates.py --language pidgin --metrics run.json --json

Metrics JSON (only the sections you are gating need to be present):
  {"asr": {"wer": 0.19, "baseline_wer": 0.26, "slices": {"douala": 0.21},
           "baseline_slices": {"douala": 0.22},
           "general_replay_wer": 0.11, "baseline_general_replay_wer": 0.10,
           "named_entity_wer": 0.22, "code_switch_wer": 0.23,
           "monolingual_avg_wer": 0.19, "code_switch_span_langid": 0.88,
           "artifact_is_quantized": true},
   "tts": {...}, "understanding": {...}, "platform": {...}}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "registry" / "languages"
GATE_DIR = ROOT / "registry" / "gates"


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str, bool | None]] = []

    def add(self, section: str, name: str, actual, limit, ok: bool | None,
            fmt: str = "{:.4f}") -> None:
        f = (lambda v: fmt.format(v) if isinstance(v, (int, float)) else str(v))
        self.rows.append((section, name, f(actual), f(limit), ok))

    @property
    def failed(self) -> list[tuple]:
        return [r for r in self.rows if r[4] is False]

    @property
    def skipped(self) -> list[tuple]:
        return [r for r in self.rows if r[4] is None]


def resolve_gates(alias: str) -> dict:
    lang = yaml.safe_load((LANG_DIR / f"{alias}.yaml").read_text())
    ref = ROOT / "registry" / lang["thresholds_ref"]
    doc = yaml.safe_load(ref.read_text()) or {}
    merged = yaml.safe_load((GATE_DIR / doc["inherits"]).read_text()) if doc.get("inherits") else {}
    for section, vals in (doc.get("overrides") or {}).items():
        merged.setdefault(section, {}).update(vals)
    return merged


def g(d: dict, *path, default=None):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return default
        d = d[p]
    return d


# --------------------------------------------------------------------------- #
def gate_asr(m: dict, t: dict, r: Result) -> None:
    if not m:
        return
    S = "asr"

    # relative gain vs frozen baseline
    wer, base = m.get("wer"), m.get("baseline_wer")
    lim = t.get("relative_wer_gain_min")
    if wer is not None and base:
        gain = (base - wer) / base
        r.add(S, "relative WER gain", gain, lim, gain >= lim, "{:.1%}")
    else:
        r.add(S, "relative WER gain", "no baseline", lim, None, "{}")

    # absolute ceilings
    if wer is not None and t.get("absolute_wer_max") is not None:
        r.add(S, "absolute WER", wer, t["absolute_wer_max"], wer <= t["absolute_wer_max"])
    cer, cer_max = m.get("cer"), t.get("absolute_cer_max")
    if cer_max is not None:
        r.add(S, "absolute CER", cer if cer is not None else "MISSING", cer_max,
              (cer <= cer_max) if cer is not None else None)

    # no slice may regress
    lim = t.get("slice_regression_max")
    slices, bslices = m.get("slices") or {}, m.get("baseline_slices") or {}
    worst, worst_name = None, None
    for name, val in slices.items():
        b = bslices.get(name)
        if b is None:
            continue
        delta = val - b
        if worst is None or delta > worst:
            worst, worst_name = delta, name
    if worst is None:
        r.add(S, "slice regression", "no slice baselines", lim, None, "{}")
    else:
        r.add(S, f"worst slice regression ({worst_name})", worst, lim, worst <= lim)

    # general-language replay must not regress
    gr, gb = m.get("general_replay_wer"), m.get("baseline_general_replay_wer")
    lim = t.get("general_replay_regression_max")
    if gr is not None and gb is not None:
        r.add(S, "general replay regression", gr - gb, lim, (gr - gb) <= lim)
    else:
        r.add(S, "general replay regression", "missing", lim, None, "{}")

    # named entities — the clinically dangerous error class
    ne, lim = m.get("named_entity_wer"), t.get("named_entity_wer_max")
    if ne is not None:
        r.add(S, "named-entity WER", ne, lim, ne <= lim)
        prev = m.get("previous_named_entity_wer")
        if t.get("named_entity_never_worse_than_previous") and prev is not None:
            r.add(S, "named-entity vs previous release", ne, prev, ne <= prev)
    else:
        r.add(S, "named-entity WER", "MISSING", lim, None, "{}")

    # code-switching
    cs, mono = m.get("code_switch_wer"), m.get("monolingual_avg_wer")
    lim = t.get("code_switch_wer_ratio_max")
    if cs is not None and mono:
        r.add(S, "code-switch WER ratio", cs / mono, lim, (cs / mono) <= lim, "{:.2f}")
    else:
        r.add(S, "code-switch WER ratio", "missing", lim, None, "{}")
    lid, lim = m.get("code_switch_span_langid"), t.get("code_switch_span_langid_min")
    if lid is not None:
        r.add(S, "code-switch span language-ID", lid, lim, lid >= lim)
    else:
        r.add(S, "code-switch span language-ID", "MISSING", lim, None, "{}")

    # THE trap: you evaluated fp16 and shipped int8
    q = m.get("artifact_is_quantized")
    r.add(S, "metrics from the DEPLOYED (quantized) artifact", bool(q), True,
          q is True, "{}")


def gate_generic(m: dict, t: dict, r: Result, section: str,
                 mins: dict[str, str], maxs: dict[str, str]) -> None:
    if not m:
        return
    for metric, thresh in mins.items():
        v, lim = m.get(metric), t.get(thresh)
        if lim is None:
            continue
        r.add(section, metric, v if v is not None else "MISSING", lim,
              (v >= lim) if v is not None else None)
    for metric, thresh in maxs.items():
        v, lim = m.get(metric), t.get(thresh)
        if lim is None:
            continue
        r.add(section, metric, v if v is not None else "MISSING", lim,
              (v <= lim) if v is not None else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language", required=True)
    ap.add_argument("--metrics", required=True, type=Path)
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--allow-missing", action="store_true",
                    help="treat missing metrics as skip rather than block")
    a = ap.parse_args()

    if not (LANG_DIR / f"{a.language}.yaml").exists():
        print(f"unknown language '{a.language}'", file=sys.stderr)
        return 2
    t = resolve_gates(a.language)
    m = json.loads(a.metrics.read_text())
    r = Result()

    gate_asr(m.get("asr"), t.get("asr", {}), r)
    gate_generic(m.get("understanding"), t.get("understanding", {}), r, "understanding",
                 mins={"native_naturalness": "native_naturalness_min",
                       "intent_accuracy": "intent_accuracy_min",
                       "citation_grounding": "citation_grounding_required",
                       "refusal_correctness": "refusal_correctness_min"},
                 maxs={"redteam_critical_failures": "redteam_critical_failures_max"})
    gate_generic(m.get("tts"), t.get("tts", {}), r, "tts",
                 mins={"native_mos": "native_mos_min",
                       "accent_authenticity": "accent_authenticity_min",
                       "pronunciation_set_accuracy": "pronunciation_set_accuracy_min"},
                 maxs={"asr_backtest_wer": "asr_backtest_wer_max",
                       "streaming_dropped_chunks": "streaming_dropped_chunks_max"})
    gate_generic(m.get("platform"), t.get("platform", {}), r, "platform",
                 mins={"availability": "availability_min"},
                 maxs={"file_first_audio_p50_ms": "file_first_audio_p50_ms",
                       "file_first_audio_p95_ms": "file_first_audio_p95_ms",
                       "stream_first_partial_p95_ms": "stream_first_partial_p95_ms",
                       "stream_first_audio_p95_ms": "stream_first_audio_p95_ms",
                       "error_rate_24h": "error_rate_24h_max"})

    blocked = bool(r.failed) or (bool(r.skipped) and not a.allow_missing)
    verdict = "BLOCKED" if blocked else "PASS"

    if a.json:
        print(json.dumps({
            "language": a.language, "verdict": verdict,
            "failed": [{"section": s, "criterion": n, "actual": act, "threshold": lim}
                       for s, n, act, lim, ok in r.failed],
            "skipped": [{"section": s, "criterion": n} for s, n, _, _, ok in r.skipped],
            "checks": len(r.rows),
        }, indent=2))
        return 0 if verdict == "PASS" else 1

    print(f"gate evaluation — {a.language}  (thresholds v1-initial)\n")
    cur = None
    for section, name, actual, limit, ok in r.rows:
        if section != cur:
            print(f"  [{section}]")
            cur = section
        mark = "PASS" if ok else ("FAIL" if ok is False else "SKIP")
        print(f"    {mark}  {name:<44} {actual:>10}  (limit {limit})")
    print(f"\n  {len(r.rows)} checks · {len(r.failed)} failed · {len(r.skipped)} skipped")
    if r.skipped and not a.allow_missing:
        print("  Missing metrics BLOCK promotion. A gate you did not measure is a")
        print("  gate you did not pass — rerun with --allow-missing only for a")
        print("  partial/diagnostic evaluation, never for a promotion decision.")
    print(f"\n  VERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
