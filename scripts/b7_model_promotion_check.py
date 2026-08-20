#!/usr/bin/env python3
"""Model-pipeline gate verification (B7 Phase 3). FAILS CLOSED.

The model workflow may open a registry PR only when a committed gate
report proves it: overall PASS is not enough — every language the PR
would bump must itself be PASS in the report, and languages that failed
stay pinned (the §A4 per-language approval mechanism). No report, an
unparseable report, a BLOCKED report or a FAIL anywhere in the requested
set refuses the run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class PromotionCheckRefusal(RuntimeError):
    pass


def load_gate_report(path: Path) -> dict:
    if not path.is_file():
        raise PromotionCheckRefusal(f"gate report absent: {path}")
    try:
        report = json.loads(path.read_bytes())
    except Exception as exc:
        raise PromotionCheckRefusal(f"gate report unparseable: {exc}")
    for field in ("schema_version", "languages", "gate_state_counts"):
        if field not in report:
            raise PromotionCheckRefusal(f"gate report lacks '{field}'")
    return report


def require_protocol_evidence(report: dict, requested: list[str]) -> None:
    """PROMOTION-PROTOCOL-2026-001 became binding on 2026-08-21 (Codex
    review #7: a fabricated bare-PASS report was accepted). A promotion
    report must carry the full identity + evidence chain; a report that
    predates the protocol simply cannot promote."""
    if report.get("protocol_id") != "PROMOTION-PROTOCOL-2026-001":
        raise PromotionCheckRefusal(
            "gate report does not bind PROMOTION-PROTOCOL-2026-001 — "
            "pre-protocol reports cannot promote")
    digest = str(report.get("candidate_digest", ""))
    if not (digest.startswith("sha256:") and len(digest) == 71):
        raise PromotionCheckRefusal(
            "candidate_digest must be the full sha256:<64hex> of the ONE "
            "production artifact (ARCH-2026-001)")
    for block in ("code_switch_evidence", "operational_evidence"):
        if not isinstance(report.get(block), dict) or not report[block]:
            raise PromotionCheckRefusal(f"gate report lacks the {block} block")
    for language in requested:
        entry = report["languages"].get(language) or {}
        holdout = str(entry.get("holdout_manifest_sha256", ""))
        if len(holdout) != 64:
            raise PromotionCheckRefusal(
                f"{language}: holdout_manifest_sha256 missing — the sealed "
                "set identity must be bound")
        stats = entry.get("non_inferiority") or entry.get("improvement")
        if not isinstance(stats, dict):
            raise PromotionCheckRefusal(
                f"{language}: no non_inferiority/improvement statistics block")
        for field in ("margin", "upper_ci", "method", "clusters"):
            if field not in stats:
                raise PromotionCheckRefusal(
                    f"{language}: statistics block lacks '{field}'")
        if stats["method"] != "paired_clustered_bootstrap":
            raise PromotionCheckRefusal(
                f"{language}: method {stats['method']!r} is not the "
                "predeclared paired_clustered_bootstrap")


def promotable_languages(report: dict, requested: list[str]) -> dict[str, str]:
    """Return {language: state} for the requested set; refuse on any non-PASS."""
    if not requested:
        raise PromotionCheckRefusal("no languages requested for promotion")
    states: dict[str, str] = {}
    problems: list[str] = []
    for language in requested:
        entry = report["languages"].get(language)
        if entry is None:
            problems.append(f"{language}: not in the gate report")
            continue
        state = entry.get("state")
        states[language] = state
        if state != "PASS":
            problems.append(f"{language}: state {state}")
    if problems:
        raise PromotionCheckRefusal(
            "refusing promotion — only PASS languages may bump approved_version: "
            + "; ".join(problems)
        )
    return states


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--languages", required=True,
                        help="comma-separated languages the PR intends to bump")
    args = parser.parse_args()
    try:
        report = load_gate_report(args.gate_report)
        requested = [x.strip() for x in args.languages.split(",") if x.strip()]
        states = promotable_languages(report, requested)
        require_protocol_evidence(report, requested)
    except PromotionCheckRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "PASS_PROMOTION_CHECK", "languages": states}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
