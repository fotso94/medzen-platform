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
            / "platform/decisions/PROMOTION-PROTOCOL-2026-002.json")
    return json.loads(path.read_bytes())


def _authoritative_holdouts_by_language() -> dict[str, set[str]]:
    """Codex review #11: the checker accepted ANY known sealed sha for ANY
    language. The mapping is now language-specific — a report citing
    swahili's holdout for english refuses."""
    root = Path(__file__).resolve().parents[1] / "platform/evidence"
    mapping: dict[str, set[str]] = {}
    tier2 = json.loads((root / "B5-TIER2-HOLDOUTS-2026-001.json").read_bytes())
    for language, pools in tier2["pools"].items():
        for pool in pools:
            mapping.setdefault(language, set()).add(
                pool["tier2-sealed"]["sha256"])
    bindings = json.loads(
        (root / "B5-IMMUTABILITY-BINDINGS-2026-001.json").read_bytes())
    mapping.setdefault("kinyarwanda", set()).add(
        bindings["universal_kinyarwanda_holdout"]["universal-sealed"]["sha256"])
    # the v2 sealed half is QUARANTINED (ledger entry 7) and deliberately
    # NOT in this mapping until an owner-approved release
    return mapping


ABSOLUTE_SCHEMA = {"margin", "upper_ci", "method", "clusters", "rows",
                   "non_inferior", "seed", "iterations", "alpha"}
RELATIVE_SCHEMA = {"min_relative_gain", "lower_ci", "method", "clusters",
                   "rows", "improved", "seed", "iterations", "alpha"}


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        c in "0123456789abcdef" for c in value.lower())


def require_protocol_evidence(report: dict, requested: list[str]) -> None:
    """PROMOTION-PROTOCOL-2026-001 became binding on 2026-08-21 (Codex
    review #7: a fabricated bare-PASS report was accepted). A promotion
    report must carry the full identity + evidence chain; a report that
    predates the protocol simply cannot promote."""
    if report.get("protocol_id") != "PROMOTION-PROTOCOL-2026-002":
        raise PromotionCheckRefusal(
            "gate report does not bind PROMOTION-PROTOCOL-2026-002 — "
            "pre-protocol reports cannot promote")
    digest = str(report.get("candidate_digest", ""))
    if not (digest.startswith("sha256:") and _hex(digest[7:], 64)):
        raise PromotionCheckRefusal(
            "candidate_digest must be the full sha256:<64 HEX> of the ONE "
            "production artifact (Codex review #8: non-hex passed)")
    required_evidence_fields = {
        "code_switch_evidence": {"state", "set", "manifest_sha256", "rows"},
        "operational_evidence": {"state", "latency_p95_ms", "vram_gb"},
    }
    for block, needed in required_evidence_fields.items():
        blob = report.get(block)
        if not isinstance(blob, dict) or not blob:
            raise PromotionCheckRefusal(f"gate report lacks the {block} block")
        missing_fields = needed - set(blob)
        if missing_fields:
            raise PromotionCheckRefusal(
                f"{block} lacks substantive fields {sorted(missing_fields)} "
                "— a bare state flag is not evidence (Codex review #9)")
        if blob.get("state") != "PASS":
            raise PromotionCheckRefusal(
                f"{block}.state is {blob.get('state')!r}, not PASS")
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
        holdout = str(entry.get("holdout_manifest_sha256", ""))
        if not _hex(holdout, 64):
            raise PromotionCheckRefusal(
                f"{language}: holdout_manifest_sha256 is not 64 hex chars")
        if holdout not in _authoritative_holdouts_by_language().get(
                language, set()):
            raise PromotionCheckRefusal(
                f"{language}: holdout {holdout[:16]}… is not a recorded "
                f"sealed set FOR {language} (Codex review #11: cross-"
                "language holdout substitution refused)")
        # Codex review #9: SEPARATE STRICT SCHEMAS — the checker used to
        # demand absolute-mode fields from relative-mode results, refusing
        # legitimate evidence while accepting fabricated wrong-field blocks.
        stats = entry.get("non_inferiority") or entry.get("improvement")
        if not isinstance(stats, dict):
            raise PromotionCheckRefusal(
                f"{language}: no non_inferiority/improvement statistics block")
        if "non_inferiority" in entry:
            schema, method, verdict_key = (
                ABSOLUTE_SCHEMA, "paired_clustered_bootstrap", "non_inferior")
        else:
            schema, method, verdict_key = (
                RELATIVE_SCHEMA, "paired_clustered_bootstrap_relative",
                "improved")
        missing_fields = schema - set(stats)
        if missing_fields:
            raise PromotionCheckRefusal(
                f"{language}: statistics block lacks {sorted(missing_fields)}")
        if stats["method"] != method:
            raise PromotionCheckRefusal(
                f"{language}: method {stats['method']!r} does not match the "
                f"block type (expected {method})")
        if stats.get(verdict_key) is not True:
            raise PromotionCheckRefusal(
                f"{language}: {verdict_key}={stats.get(verdict_key)!r} — a "
                "failing or absent statistical verdict cannot promote")
        clusters = stats["clusters"]
        if not isinstance(clusters, int) or clusters < 2:
            raise PromotionCheckRefusal(
                f"{language}: clusters={clusters!r} is not a valid cluster "
                "count")
        if "non_inferiority" in entry:
            margin, upper = float(stats["margin"]), float(stats["upper_ci"])
            if not (margin > 0):
                raise PromotionCheckRefusal(
                    f"{language}: margin must be positive")
            if not (upper < margin):
                raise PromotionCheckRefusal(
                    f"{language}: upper_ci {upper} does not clear the margin "
                    f"{margin} — the numbers contradict the claimed verdict")
        else:
            gain, lower = (float(stats["min_relative_gain"]),
                           float(stats["lower_ci"]))
            if not (0 < gain < 1):
                raise PromotionCheckRefusal(
                    f"{language}: min_relative_gain must be in (0, 1)")
            if not (lower > gain):
                raise PromotionCheckRefusal(
                    f"{language}: lower_ci {lower} does not clear "
                    f"min_relative_gain {gain} — the numbers contradict the "
                    "claimed verdict")


def recompute_statistics(report: dict, results_dir: Path,
                          requested: list[str]) -> None:
    """Codex review #9 rec 3: the gate RECOMPUTES the statistics from
    hash-bound per-row results. Self-reported summaries alone can no
    longer promote — the rows file must exist, hash-match, and reproduce
    the claimed verdict AND bounds exactly (deterministic under seed)."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hashlib as _hashlib

    from noninferiority import (clustered_noninferiority,
                                 clustered_relative_improvement)
    mandatory = _protocol_record().get("mandatory_languages", [])
    for language in sorted(set(requested) | set(mandatory)):
        entry = report["languages"][language]
        rows_path = results_dir / f"{language}.rows.jsonl"
        if not rows_path.is_file():
            raise PromotionCheckRefusal(
                f"{language}: per-row results file absent at {rows_path} — "
                "summaries alone cannot promote (Codex review #9)")
        body = rows_path.read_bytes()
        claimed_sha = str(entry.get("rows_sha256", ""))
        if _hashlib.sha256(body).hexdigest() != claimed_sha:
            raise PromotionCheckRefusal(
                f"{language}: rows file hash does not match the report's "
                "rows_sha256")
        rows = [json.loads(line) for line in body.decode().splitlines()
                if line.strip()]
        stats = entry.get("non_inferiority") or entry.get("improvement")
        kwargs = {k: stats[k] for k in ("iterations", "seed", "alpha")}
        if "non_inferiority" in entry:
            actual = clustered_noninferiority(
                rows, margin=stats["margin"], **kwargs)
            claims = {k: stats[k] for k in ("upper_ci", "non_inferior")}
            facts = {k: actual[k] for k in ("upper_ci", "non_inferior")}
        else:
            actual = clustered_relative_improvement(
                rows, min_relative_gain=stats["min_relative_gain"], **kwargs)
            claims = {k: stats[k] for k in ("lower_ci", "improved")}
            facts = {k: actual[k] for k in ("lower_ci", "improved")}
        if claims != facts:
            raise PromotionCheckRefusal(
                f"{language}: recomputed statistics {facts} do not match "
                f"the claimed {claims} — the report is not derived from "
                "these rows")


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
    parser.add_argument("--languages", default="",
                        help="DEPRECATED subset selector — promotion is "
                             "atomic over the mandatory set; empty means "
                             "exactly that set")
    parser.add_argument("--results-dir", type=Path, default=None,
                        help="directory of per-language hash-bound "
                             "<language>.rows.jsonl result files; REQUIRED "
                             "for promotion (recompute gate)")
    args = parser.parse_args()
    try:
        report = load_gate_report(args.gate_report)
        requested = [x.strip() for x in args.languages.split(",") if x.strip()]
        if not requested:
            requested = list(_protocol_record().get("mandatory_languages", []))
        states = promotable_languages(report, requested)
        require_protocol_evidence(report, requested)
        if args.results_dir is None:
            raise PromotionCheckRefusal(
                "--results-dir is required: promotion statistics must be "
                "recomputed from hash-bound rows (Codex review #9)")
        recompute_statistics(report, args.results_dir, requested)
    except PromotionCheckRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "PASS_PROMOTION_CHECK", "languages": states}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
