#!/usr/bin/env python3
"""Derive the sealed gate report from the sealed run's own row files.

Every number in the report is COMPUTED here by the same shared statistics
module the b7 checker uses to RECOMPUTE them (promotion_check.recompute_
statistics) — the report cannot claim anything the rows do not reproduce.
Thresholds, statistics parameters and holdout identities come verbatim from
the predeclared candidate packet; nothing is chosen after observation.

Usage:
  python3 scripts/build_sealed_gate_report.py \
      --packet platform/decisions/SEALED-EVAL-ARM1-PACKET-2026-004.json \
      --results-dir <dir with <language>.rows.jsonl> \
      --out <report path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/model-loader"))
from medzen_model_loader.noninferiority import (  # noqa: E402
    clustered_noninferiority,
    clustered_relative_improvement,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    packet = json.loads(args.packet.read_bytes())
    languages: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for language, cfg in sorted(packet["languages"].items()):
        body = (args.results_dir / f"{language}.rows.jsonl").read_bytes()
        rows = [json.loads(line) for line in body.decode().splitlines()
                if line.strip()]
        kwargs = {"iterations": int(cfg["iterations"]),
                  "seed": int(cfg["seed"]), "alpha": float(cfg["alpha"])}
        clusters = {r.get("cluster_id") for r in rows}
        if len(clusters) < 2:
            # FLEURS-derived dev-grade holdouts carry a single corpus-level
            # speaker, so the PREDECLARED clustered bootstrap cannot run at
            # all. That is a property of the holdout, not of the candidate:
            # recorded as UNEVALUABLE, never as PASS or a fabricated CI.
            state = "UNEVALUABLE_SINGLE_CLUSTER_HOLDOUT"
            counts[state] = counts.get(state, 0) + 1
            total_ref = sum(int(r["reference_words"]) for r in rows)
            languages[language] = {
                "state": state,
                "rows_sha256": hashlib.sha256(body).hexdigest(),
                "holdout_manifest_sha256": cfg["holdout_manifest_sha256"],
                "unevaluable_reason": (
                    "the sealed holdout manifest binds every row to one "
                    "corpus-level speaker, so the predeclared "
                    "paired_clustered_bootstrap needs >= 2 clusters and "
                    "cannot produce a confidence bound"),
                "observed": {
                    "rows": len(rows), "clusters": len(clusters),
                    "candidate_errors": sum(int(r["candidate_errors"]) for r in rows),
                    "baseline_errors": sum(int(r["baseline_errors"]) for r in rows),
                    "reference_words": total_ref},
            }
            continue
        if cfg["mode"] == "absolute":
            actual = clustered_noninferiority(
                rows, margin=float(cfg["margin"]), **kwargs)
            stats_key, verdict = "non_inferiority", bool(actual["non_inferior"])
            stats = {"margin": cfg["margin"],
                     "upper_ci": actual["upper_ci"],
                     "non_inferior": actual["non_inferior"]}
        else:
            actual = clustered_relative_improvement(
                rows, min_relative_gain=float(cfg["min_relative_gain"]),
                **kwargs)
            stats_key, verdict = "improvement", bool(actual["improved"])
            stats = {"min_relative_gain": cfg["min_relative_gain"],
                     "lower_ci": actual["lower_ci"],
                     "improved": actual["improved"]}
        stats.update(kwargs)
        stats.update({"method": cfg["method"],
                      "rows": actual["rows"], "clusters": actual["clusters"]})
        state = "PASS" if verdict else "FAIL"
        counts[state] = counts.get(state, 0) + 1
        languages[language] = {
            "state": state,
            "rows_sha256": hashlib.sha256(body).hexdigest(),
            "holdout_manifest_sha256": cfg["holdout_manifest_sha256"],
            stats_key: stats,
        }
    report = {
        "schema_version": 1,
        "record": "SEALED-GATE-REPORT-ARM1-2026-001",
        "candidate_digest": packet["candidate_digest"],
        "protocol_id": packet["protocol_id"],
        "scorer_id": packet["scorer_id"],
        "scorer_sha256": packet["scorer_sha256"],
        "decoding_config_sha256": packet["decoding_config_sha256"],
        "sealed_run_job": {
            "name": packet["sealed_run_contract"]["job_name"]},
        # predeclared expectation: the licensed code-switch registry is
        # EMPTY, so no code-switch rows can exist — mirrored verbatim
        "code_switch_evidence": {
            "non_inferiority": dict(packet["code_switch"])},
        "languages": languages,
        "gate_state_counts": counts,
    }
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    for language, entry in languages.items():
        stats = entry.get("non_inferiority") or entry.get("improvement")
        if stats is None:
            obs = entry["observed"]
            print(f"{entry['state']} {language:12s} rows={obs['rows']:>5} "
                  f"clusters={obs['clusters']:>4} cand_err={obs['candidate_errors']} "
                  f"base_err={obs['baseline_errors']} ref_words={obs['reference_words']}")
            continue
        bound = stats.get("upper_ci", stats.get("lower_ci"))
        print(f"{entry['state']:4s} {language:12s} rows={stats['rows']:>5} "
              f"clusters={stats['clusters']:>4} ci_bound={bound:+.5f}")
    print(f"gate_state_counts: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
