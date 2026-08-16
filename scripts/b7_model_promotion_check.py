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
    except PromotionCheckRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "PASS_PROMOTION_CHECK", "languages": states}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
