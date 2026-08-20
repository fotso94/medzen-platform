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


def _protocol_record() -> dict:
    path = (Path(__file__).resolve().parents[1]
            / "platform/decisions/PROMOTION-PROTOCOL-2026-001.json")
    return json.loads(path.read_bytes())


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        c in "0123456789abcdef" for c in value.lower())


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
    if not (digest.startswith("sha256:") and _hex(digest[7:], 64)):
        raise PromotionCheckRefusal(
            "candidate_digest must be the full sha256:<64 HEX> of the ONE "
            "production artifact (Codex review #8: non-hex passed)")
    for block in ("code_switch_evidence", "operational_evidence"):
        blob = report.get(block)
        if not isinstance(blob, dict) or not blob:
            raise PromotionCheckRefusal(f"gate report lacks the {block} block")
        if blob.get("state") != "PASS":
            raise PromotionCheckRefusal(
                f"{block}.state is {blob.get('state')!r}, not PASS — "
                "evidence must PASS, not merely exist (Codex review #8)")
    # ATOMIC GATE (Codex review #8): the one production artifact promotes
    # for the frozen mandatory set or not at all — a requested subset can
    # never bypass the languages it left out.
    mandatory = _protocol_record().get("mandatory_languages", [])
    checked = sorted(set(requested) | set(mandatory))
    counts = report.get("gate_state_counts", {})
    actual_pass = sum(1 for e in report["languages"].values()
                      if isinstance(e, dict) and e.get("state") == "PASS")
    if counts.get("PASS") != actual_pass:
        raise PromotionCheckRefusal(
            f"gate_state_counts.PASS={counts.get('PASS')} but the languages "
            f"map holds {actual_pass} PASS entries — inconsistent report")
    for language in checked:
        entry = report["languages"].get(language) or {}
        holdout = str(entry.get("holdout_manifest_sha256", ""))
        if len(holdout) != 64:
            raise PromotionCheckRefusal(
                f"{language}: holdout_manifest_sha256 missing — the sealed "
                "set identity must be bound")
        if (entry.get("state")) != "PASS":
            raise PromotionCheckRefusal(
                f"{language}: mandatory language state is "
                f"{entry.get('state')!r} — the atomic gate covers the whole "
                "mandatory set")
        stats = entry.get("non_inferiority") or entry.get("improvement")
        if not isinstance(stats, dict):
            raise PromotionCheckRefusal(
                f"{language}: no non_inferiority/improvement statistics block")
        for field in ("margin", "upper_ci", "method", "clusters"):
            if field not in stats:
                raise PromotionCheckRefusal(
                    f"{language}: statistics block lacks '{field}'")
        if stats["method"] not in ("paired_clustered_bootstrap",
                                    "paired_clustered_bootstrap_relative"):
            raise PromotionCheckRefusal(
                f"{language}: method {stats['method']!r} is not a "
                "predeclared paired clustered bootstrap")
        # Codex review #8: presence was accepted as truth. The VERDICT
        # fields must actually pass and be internally coherent.
        verdict_key = ("non_inferior" if "non_inferiority" in entry
                       else "improved")
        if stats.get(verdict_key) is not True:
            raise PromotionCheckRefusal(
                f"{language}: {verdict_key}={stats.get(verdict_key)!r} — a "
                "failing or absent statistical verdict cannot promote")
        clusters = stats["clusters"]
        if not isinstance(clusters, int) or clusters < 2:
            raise PromotionCheckRefusal(
                f"{language}: clusters={clusters!r} is not a valid cluster "
                "count")
        margin, upper = float(stats["margin"]), float(stats["upper_ci"])
        if not (margin > 0):
            raise PromotionCheckRefusal(f"{language}: margin must be positive")
        if "non_inferiority" in entry and not (upper < margin):
            raise PromotionCheckRefusal(
                f"{language}: upper_ci {upper} does not clear the margin "
                f"{margin} — the numbers contradict the claimed verdict")


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
