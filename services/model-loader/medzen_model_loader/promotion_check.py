"""The SHARED promotion-gate semantics (Codex serving review round 6).

Round 5's bundle checked PASS labels — Codex fabricated a detailed
all-PASS bundle and it promoted (FABRICATED_DETAILED_PASS_BUNDLE_
ACCEPTED). The label check and the AUTHORITATIVE checker
(scripts/b7_model_promotion_check.py) were two different codebases.

This module is now the ONE implementation of the report semantics and
the statistical recomputation: the repo-side checker delegates here with
git/evidence-record authorities, and the runtime bundle verification
delegates here with bundle-pinned authorities. Evidence is DATA, not
labels — per-language hash-bound row files must exist and REPRODUCE the
claimed clustered-bootstrap verdicts and bounds exactly (deterministic
under seed), holdout identities must be the recorded sealed sets FOR
that language, and the atomic mandatory-set rule cannot be subset away.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from .noninferiority import (
    clustered_noninferiority,
    clustered_relative_improvement,
)


class PromotionCheckRefusal(RuntimeError):
    pass


ABSOLUTE_SCHEMA = {"margin", "upper_ci", "method", "clusters", "rows",
                   "non_inferior", "seed", "iterations", "alpha"}
RELATIVE_SCHEMA = {"min_relative_gain", "lower_ci", "method", "clusters",
                   "rows", "improved", "seed", "iterations", "alpha"}


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        c in "0123456789abcdef" for c in value.lower())


def validate_report_structure(report: Any) -> dict:
    if not isinstance(report, dict):
        raise PromotionCheckRefusal("gate report must be a JSON object")
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


def require_protocol_evidence(report: dict, requested: list[str], *,
                              protocol: Mapping[str, Any],
                              holdouts_by_language: Mapping[str, set[str]],
                              ) -> None:
    """PROMOTION-PROTOCOL-2026-001 became binding on 2026-08-21 (Codex
    review #7: a fabricated bare-PASS report was accepted). A promotion
    report must carry the full identity + evidence chain; a report that
    predates the protocol simply cannot promote."""
    current_protocol = protocol.get("record")
    if report.get("protocol_id") != current_protocol:
        raise PromotionCheckRefusal(
            f"gate report binds protocol {report.get('protocol_id')!r} "
            f"but the current protocol is {current_protocol} — stale or "
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
    mandatory = protocol.get("mandatory_languages", [])
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
        if holdout not in holdouts_by_language.get(language, set()):
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


def recompute_statistics(report: dict, requested: list[str], *,
                         mandatory: list[str],
                         rows_bytes: Callable[[str], bytes | None]) -> None:
    """Codex review #9 rec 3 (and serving round 6): the gate RECOMPUTES
    the statistics from hash-bound per-row results. Self-reported
    summaries alone can no longer promote — the rows must exist,
    hash-match, and reproduce the claimed verdict AND bounds exactly
    (deterministic under seed). rows_bytes(language) supplies the raw
    rows file from whatever authority the caller trusts (results dir on
    the repo side, the pinned bundle at runtime)."""
    for language in sorted(set(requested) | set(mandatory)):
        entry = report["languages"][language]
        body = rows_bytes(language)
        if body is None:
            raise PromotionCheckRefusal(
                f"{language}: per-row results file absent — "
                "summaries alone cannot promote (Codex review #9)")
        claimed_sha = str(entry.get("rows_sha256", ""))
        if hashlib.sha256(body).hexdigest() != claimed_sha:
            raise PromotionCheckRefusal(
                f"{language}: rows file hash does not match the report's "
                "rows_sha256")
        rows = [json.loads(line) for line in body.decode().splitlines()
                if line.strip()]
        stats = entry.get("non_inferiority") or entry.get("improvement")
        kwargs = {k: stats[k] for k in ("iterations", "seed", "alpha")}
        try:
            if "non_inferiority" in entry:
                actual = clustered_noninferiority(
                    rows, margin=stats["margin"], **kwargs)
                claims = {k: stats[k] for k in ("upper_ci", "non_inferior")}
                facts = {k: actual[k] for k in ("upper_ci", "non_inferior")}
            else:
                actual = clustered_relative_improvement(
                    rows, min_relative_gain=stats["min_relative_gain"],
                    **kwargs)
                claims = {k: stats[k] for k in ("lower_ci", "improved")}
                facts = {k: actual[k] for k in ("lower_ci", "improved")}
        except ValueError as exc:
            raise PromotionCheckRefusal(
                f"{language}: rows are not valid paired evidence ({exc})")
        if claims != facts:
            raise PromotionCheckRefusal(
                f"{language}: recomputed statistics {facts} do not match "
                f"the claimed {claims} — the report is not derived from "
                "these rows")
