#!/usr/bin/env python3
"""Decode-strategy evidence pack from the suite (B3.2 / Correction 3).

The suite runs both decode arms of the adopted omniASR family over every
language: conditioned (language identifier forced) and unconditioned
(no identifier). This tool derives the per-language decode_strategy
verdict from the committed, hash-bound aggregates — the registry may only
ever record a decode choice with evidence behind it, and this pack IS
that evidence.

Fail-closed rules:
  * a language gets a verdict only when its pool coverage is COMPLETE in
    the merge report — partial coverage is INSUFFICIENT_EVIDENCE;
  * each compared arm needs at least MIN_ROWS scored rows;
  * deltas smaller than the tie band yield TIE_DEFAULT_UNCONDITIONED
    (unconditioned needs no conditioning identifier and the suite shows
    conditioning adds little — the tie default is the simpler deployment);
  * languages outside the conditioning set are UNCONDITIONED_ONLY;
  * the B3.2 re-run rule is stamped into the pack: the experiment repeats
    whenever a new ASR artifact is promoted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.asr_full_eval_suite_merge import build_report

CANDIDATE = "omniASR_LLM_1B_v2"
MIN_ROWS = 100
TIE_BAND_RELATIVE = 0.02  # CER deltas under 2% relative are noise, not signal


def language_verdict(
    language: str,
    coverage_state: str,
    conditioned: dict[str, Any] | None,
    unconditioned: dict[str, Any] | None,
    pool_rows: int,
) -> dict[str, Any]:
    if coverage_state != "COMPLETE":
        return {"verdict": "INSUFFICIENT_EVIDENCE",
                "reason": f"pool coverage is {coverage_state}"}
    # A small language's whole pool IS the maximum obtainable evidence: the
    # floor is min(MIN_ROWS, pool) so a 60-row pool can still conclude, while
    # the unconditioned arm must always cover the full pool.
    floor = min(MIN_ROWS, pool_rows)
    if unconditioned is None or unconditioned.get("rows", 0) < pool_rows:
        return {"verdict": "INSUFFICIENT_EVIDENCE",
                "reason": "unconditioned arm does not cover the full pool"}
    if conditioned is None or conditioned.get("rows", 0) == 0:
        return {
            "verdict": "UNCONDITIONED_ONLY",
            "reason": "no approved conditioning identifier for this language",
            "unconditioned_cer": unconditioned["cer"],
            "rows": unconditioned["rows"],
        }
    if conditioned["rows"] < floor:
        return {"verdict": "INSUFFICIENT_EVIDENCE",
                "reason": f"conditioned arm below the {floor}-row floor"}
    cer_c, cer_u = conditioned["cer"], unconditioned["cer"]
    baseline = max(cer_u, 1e-9)
    relative_delta = (cer_u - cer_c) / baseline
    body = {
        "conditioned_cer": cer_c,
        "unconditioned_cer": cer_u,
        "conditioned_rows": conditioned["rows"],
        "unconditioned_rows": unconditioned["rows"],
        "relative_cer_delta": round(relative_delta, 6),
    }
    if abs(relative_delta) < TIE_BAND_RELATIVE:
        return {"verdict": "TIE_DEFAULT_UNCONDITIONED", **body}
    if relative_delta > 0:
        return {"verdict": "CONDITIONED", **body}
    return {"verdict": "UNCONDITIONED", **body}


VERDICT_TO_MODE = {
    "CONDITIONED": "language_forced",
    "UNCONDITIONED": "auto",
    "TIE_DEFAULT_UNCONDITIONED": "auto",
    "UNCONDITIONED_ONLY": "auto",
}


def build_pack(root: Path, minimum_attempt: int = 32) -> dict[str, Any]:
    report = build_report(root, minimum_attempt)
    per_language = report["metrics"]["per_language"]
    coverage = report["coverage"]["languages"]
    verdicts: dict[str, Any] = {}
    registry_blocks: dict[str, Any] = {}
    run_ids = [f"attempt-{r['attempt']}" for r in report["source_runs"]]
    for language, cov in sorted(coverage.items()):
        conditioned = per_language.get(f"{CANDIDATE}|conditioned|{language}")
        unconditioned = per_language.get(f"{CANDIDATE}|unconditioned|{language}")
        verdict = language_verdict(language, cov["state"], conditioned, unconditioned,
                                   cov["pool_rows"])
        verdicts[language] = verdict
        mode = VERDICT_TO_MODE.get(verdict["verdict"])
        if mode is not None:
            registry_blocks[language] = {
                "mode": mode,
                "chosen_by_run": "+".join(run_ids),
                "frozen_eval": report["shard_manifest"]["sha256"],
            }
    return {
        "record": "ASR_DECODE_STRATEGY_EVIDENCE_PACK",
        "schema_version": 1,
        "candidate": CANDIDATE,
        "method": {
            "arms": ["conditioned (language identifier forced)", "unconditioned (auto)"],
            "minimum_rows_per_arm": MIN_ROWS,
            "tie_band_relative_cer": TIE_BAND_RELATIVE,
            "tie_default": "unconditioned — no conditioning identifier needed at serving",
            "rerun_rule": (
                "repeat whenever a new ASR artifact is promoted — the winner can "
                "change as the model learns (B3.2); the registry PR is the record"
            ),
        },
        "source_runs": report["source_runs"],
        "coverage_status": report["status"],
        "verdicts": verdicts,
        "registry_decode_strategy_blocks": registry_blocks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    pack = build_pack(args.root.resolve())
    body = json.dumps(pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if args.output:
        args.output.write_bytes(body)
    concluded = {k: v["verdict"] for k, v in pack["verdicts"].items()
                 if v["verdict"] != "INSUFFICIENT_EVIDENCE"}
    print(f"{pack['coverage_status']}: verdicts for {len(concluded)}/{len(pack['verdicts'])} languages")
    for language, verdict in sorted(concluded.items()):
        print(f"  {language}: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
