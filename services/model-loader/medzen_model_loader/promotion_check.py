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


def require_candidate_packet(report: dict, *,
                             candidate_packet: Mapping[str, Any]) -> None:
    """Round 7 (Codex): the protocol PREDECLARES every threshold before
    any sealed observation — a bundle without an immutable candidate
    packet permits post-result threshold selection. Every statistical
    parameter in the report must equal the packet's predeclared value,
    and the packet must name the same candidate."""
    if report.get("candidate_digest") != candidate_packet.get("candidate_digest"):
        raise PromotionCheckRefusal(
            "candidate packet predeclares a different candidate digest")
    if report.get("protocol_id") != candidate_packet.get("protocol_id"):
        raise PromotionCheckRefusal(
            "candidate packet predeclares a different protocol")
    declared = candidate_packet.get("languages")
    if not isinstance(declared, Mapping) or not declared:
        raise PromotionCheckRefusal(
            "candidate packet predeclares no per-language parameters")
    for language, predeclared in declared.items():
        entry = report.get("languages", {}).get(language)
        if not isinstance(entry, Mapping):
            raise PromotionCheckRefusal(
                f"{language}: predeclared in the packet but absent from "
                "the gate report")
        stats = entry.get("non_inferiority") or entry.get("improvement")
        if not isinstance(stats, Mapping):
            raise PromotionCheckRefusal(
                f"{language}: no statistics block to compare against the "
                "predeclared packet")
        threshold_key = ("margin" if "non_inferiority" in entry
                          else "min_relative_gain")
        for key in (threshold_key, "alpha", "method", "seed", "iterations"):
            if stats.get(key) != predeclared.get(key):
                raise PromotionCheckRefusal(
                    f"{language}: {key}={stats.get(key)!r} does not match "
                    f"the PREDECLARED {predeclared.get(key)!r} — thresholds "
                    "are chosen before sealed observation, never after")
        packet_holdout = str(predeclared.get("holdout_manifest_sha256", ""))
        if entry.get("holdout_manifest_sha256") != packet_holdout:
            raise PromotionCheckRefusal(
                f"{language}: sealed set differs from the predeclared one")
    operational_thresholds = candidate_packet.get("operational_thresholds")
    if not isinstance(operational_thresholds, Mapping) or not {
        "max_latency_p95_ms", "max_vram_gb"
    } <= set(operational_thresholds):
        raise PromotionCheckRefusal(
            "candidate packet predeclares no operational thresholds")


def require_operational_receipt(report: dict, *,
                                candidate_packet: Mapping[str, Any],
                                artifact_tree_sha256: str) -> None:
    """Round 7 (Codex, FABRICATED_..._OPERATIONAL_EVIDENCE_ACCEPTED):
    operational evidence must be a measurement RECEIPT tied to the exact
    artifact tree, the serving image and the hardware, and the measured
    values must clear the PREDECLARED thresholds — a PASS flag with two
    numbers is an assertion."""
    receipt = report.get("operational_evidence")
    if not isinstance(receipt, Mapping):
        raise PromotionCheckRefusal("gate report lacks operational evidence")
    needed = {"state", "latency_p95_ms", "vram_gb", "artifact_tree_sha256",
              "serving_image_digest", "instance_type", "measured_utc"}
    missing = needed - set(receipt)
    if missing:
        raise PromotionCheckRefusal(
            f"operational receipt lacks {sorted(missing)} — measurements "
            "must be attributable to artifact, image and hardware")
    if receipt.get("artifact_tree_sha256") != artifact_tree_sha256:
        raise PromotionCheckRefusal(
            "operational receipt measures a DIFFERENT artifact tree")
    if not str(receipt.get("serving_image_digest", "")).startswith("sha256:"):
        raise PromotionCheckRefusal(
            "operational receipt must pin the serving image digest")
    if not str(receipt.get("instance_type") or "").strip():
        raise PromotionCheckRefusal(
            "operational receipt must name the measured instance type")
    thresholds = candidate_packet.get("operational_thresholds", {})
    try:
        latency = float(receipt["latency_p95_ms"])
        vram = float(receipt["vram_gb"])
        max_latency = float(thresholds["max_latency_p95_ms"])
        max_vram = float(thresholds["max_vram_gb"])
    except (TypeError, ValueError, KeyError) as exc:
        raise PromotionCheckRefusal(
            "operational receipt values are not numeric") from exc
    if latency > max_latency or vram > max_vram:
        raise PromotionCheckRefusal(
            f"operational measurements (p95 {latency}ms, {vram}GB) exceed "
            f"the PREDECLARED thresholds ({max_latency}ms, {max_vram}GB)")


def require_holdout_grades(report: dict, *,
                           grades_by_holdout: Mapping[str, str],
                           mandatory: list[str]) -> None:
    """Round 7 (Codex): the protocol grades sealed sets — development-
    grade pools (placeholder speakers) may support but never BE the
    promotion evidence, and conditional sets carry disclosed caveats."""
    for language in mandatory:
        entry = report.get("languages", {}).get(language) or {}
        holdout = str(entry.get("holdout_manifest_sha256", ""))
        grade = grades_by_holdout.get(holdout)
        if grade is None:
            raise PromotionCheckRefusal(
                f"{language}: sealed set carries no recorded grade")
        if grade == "development_grade_only":
            raise PromotionCheckRefusal(
                f"{language}: a development-grade pool cannot be sole "
                "promotion evidence (placeholder speakers)")
        if grade == "conditional" and not str(
            entry.get("conditional_caveat_ack") or ""
        ).strip():
            raise PromotionCheckRefusal(
                f"{language}: a conditional set requires the disclosed "
                "caveat to be acknowledged in the report entry")
        if grade not in ("promotion_grade", "conditional"):
            raise PromotionCheckRefusal(
                f"{language}: unknown holdout grade {grade!r}")


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
        # Round 7 (Codex, FABRICATED_ROW_AND_CLUSTER_COUNTS_ACCEPTED):
        # the reported row/cluster counts are claims too — they must
        # equal what the rows actually contain.
        for count_key in ("rows", "clusters"):
            if stats.get(count_key) != actual[count_key]:
                raise PromotionCheckRefusal(
                    f"{language}: reported {count_key}={stats.get(count_key)!r} "
                    f"but the evidence contains {actual[count_key]} — "
                    "counts must derive from the rows")
        if claims != facts:
            raise PromotionCheckRefusal(
                f"{language}: recomputed statistics {facts} do not match "
                f"the claimed {claims} — the report is not derived from "
                "these rows")


def require_sealed_row_identity(report: dict, mandatory: list[str], *,
                                rows_bytes: Callable[[str], bytes | None],
                                manifest_bytes: Callable[[str], bytes | None],
                                ) -> None:
    """Round 7 (Codex): every result row must belong to the EXACT sealed
    set — the rows' utterance-id set must equal the sealed manifest's,
    once each, and the manifest bytes must hash to the report's bound
    holdout_manifest_sha256. Rows for utterances the sealed set never
    contained (or a convenient subset of it) are not sealed evidence."""
    for language in mandatory:
        entry = report["languages"][language]
        manifest_raw = manifest_bytes(language)
        if manifest_raw is None:
            raise PromotionCheckRefusal(
                f"{language}: sealed holdout manifest absent — row "
                "identity cannot be verified")
        if hashlib.sha256(manifest_raw).hexdigest() != str(
            entry.get("holdout_manifest_sha256", "")
        ):
            raise PromotionCheckRefusal(
                f"{language}: holdout manifest bytes do not hash to the "
                "report's bound sealed-set identity")
        manifest = json.loads(manifest_raw)
        sealed_ids = manifest.get("utterance_ids")
        if (not isinstance(sealed_ids, list) or not sealed_ids
                or len(set(sealed_ids)) != len(sealed_ids)):
            raise PromotionCheckRefusal(
                f"{language}: sealed manifest carries no unique "
                "utterance_ids list")
        body = rows_bytes(language)
        if body is None:
            raise PromotionCheckRefusal(
                f"{language}: rows absent for sealed-identity check")
        row_ids = [json.loads(line).get("utterance_id")
                   for line in body.decode().splitlines() if line.strip()]
        if any(not isinstance(i, str) or not i for i in row_ids):
            raise PromotionCheckRefusal(
                f"{language}: every result row must carry its utterance_id")
        if len(set(row_ids)) != len(row_ids):
            raise PromotionCheckRefusal(
                f"{language}: duplicate utterance rows — each sealed "
                "utterance is scored exactly once")
        if set(row_ids) != set(sealed_ids):
            raise PromotionCheckRefusal(
                f"{language}: result rows do not cover EXACTLY the sealed "
                "utterance set (missing or invented utterances)")


def recompute_code_switch(report: dict, *,
                          rows_bytes: Callable[[str], bytes | None]) -> None:
    """Round 7 (Codex, FABRICATED_CODE_SWITCH_..._ACCEPTED): the
    code-switch gate is evidence too — its statistics recompute from its
    own hash-bound rows exactly like every language gate."""
    evidence = report.get("code_switch_evidence")
    if not isinstance(evidence, Mapping):
        raise PromotionCheckRefusal("gate report lacks code_switch_evidence")
    stats = evidence.get("non_inferiority") or evidence.get("improvement")
    if not isinstance(stats, Mapping):
        raise PromotionCheckRefusal(
            "code_switch_evidence carries no statistics block — a PASS "
            "flag is not evidence")
    body = rows_bytes("code_switch")
    if body is None:
        raise PromotionCheckRefusal(
            "code-switch rows absent — the gate recomputes, never trusts")
    if hashlib.sha256(body).hexdigest() != str(
        evidence.get("rows_sha256", "")
    ):
        raise PromotionCheckRefusal(
            "code-switch rows do not hash to the report's rows_sha256")
    rows = [json.loads(line) for line in body.decode().splitlines()
            if line.strip()]
    kwargs = {k: stats[k] for k in ("iterations", "seed", "alpha")}
    try:
        if "non_inferiority" in evidence:
            actual = clustered_noninferiority(
                rows, margin=stats["margin"], **kwargs)
            claims = {k: stats[k] for k in ("upper_ci", "non_inferior",
                                              "rows", "clusters")}
            facts = {k: actual[k] for k in ("upper_ci", "non_inferior",
                                              "rows", "clusters")}
        else:
            actual = clustered_relative_improvement(
                rows, min_relative_gain=stats["min_relative_gain"], **kwargs)
            claims = {k: stats[k] for k in ("lower_ci", "improved",
                                              "rows", "clusters")}
            facts = {k: actual[k] for k in ("lower_ci", "improved",
                                              "rows", "clusters")}
    except ValueError as exc:
        raise PromotionCheckRefusal(
            f"code-switch rows are not valid paired evidence ({exc})")
    if claims != facts:
        raise PromotionCheckRefusal(
            f"code-switch recomputation {facts} does not match the "
            f"claimed {claims}")
