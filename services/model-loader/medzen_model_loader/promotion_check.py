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
                             candidate_packet: Mapping[str, Any],
                             mandatory: list[str]) -> None:
    """Round 7 (Codex): the protocol PREDECLARES every threshold before
    any sealed observation — a bundle without an immutable candidate
    packet permits post-result threshold selection. Every statistical
    parameter in the report must equal the packet's predeclared value,
    and the packet must name the same candidate.

    Round 8 (Codex, CANDIDATE_PACKET_1_OF_7_ACCEPTED): the packet's
    language set must equal the protocol's mandatory set EXACTLY — a
    packet predeclaring only english cannot cover a seven-language
    atomic promotion. And POSTHOC_CODESWITCH_MARGIN_ACCEPTED: the
    code-switch gate's parameters are predeclared in the packet too."""
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
    if set(declared) != set(mandatory):
        raise PromotionCheckRefusal(
            f"candidate packet predeclares {sorted(declared)} but the "
            f"protocol's mandatory set is {sorted(mandatory)} — the "
            "packet covers the WHOLE atomic set or it covers nothing")
    cs_declared = candidate_packet.get("code_switch")
    if not isinstance(cs_declared, Mapping):
        raise PromotionCheckRefusal(
            "candidate packet predeclares no code-switch parameters")
    cs_evidence = report.get("code_switch_evidence") or {}
    cs_stats = (cs_evidence.get("non_inferiority")
                or cs_evidence.get("improvement") or {})
    cs_threshold_key = ("margin" if "non_inferiority" in cs_evidence
                        else "min_relative_gain")
    for key in (cs_threshold_key, "alpha", "method", "seed", "iterations"):
        if cs_stats.get(key) != cs_declared.get(key):
            raise PromotionCheckRefusal(
                f"code_switch {key}={cs_stats.get(key)!r} does not match "
                f"the PREDECLARED {cs_declared.get(key)!r}")
    for key in ("set", "manifest_sha256"):
        if cs_evidence.get(key) != cs_declared.get(key):
            raise PromotionCheckRefusal(
                f"code_switch {key} differs from the predeclared packet")
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
    # round 10 (Codex): the scorer is part of the predeclared method
    scorer = str(candidate_packet.get("scorer_sha256", ""))
    if not _hex(scorer, 64):
        raise PromotionCheckRefusal(
            "candidate packet must predeclare the scorer_sha256 — the "
            "scoring code is part of the method")
    if report.get("scorer_sha256") != scorer:
        raise PromotionCheckRefusal(
            "gate report scorer does not match the predeclared scorer")


def _finite_nonneg(value: Any, label: str) -> float:
    """Round 8 (Codex, FAKE_OPERATIONAL_RECEIPT_ACCEPTED): NaN evaded
    the > comparison and negative values passed."""
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PromotionCheckRefusal(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise PromotionCheckRefusal(
            f"{label}={value!r} must be finite and non-negative")
    return number


def _utc_ok(value: Any) -> bool:
    from datetime import datetime
    try:
        datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")
        return True
    except (ValueError, TypeError):
        return False


def require_operational_receipt(report: dict, *,
                                candidate_packet: Mapping[str, Any],
                                artifact_tree_sha256: str) -> None:
    """Round 7 (Codex, FABRICATED_..._OPERATIONAL_EVIDENCE_ACCEPTED):
    operational evidence must be a measurement RECEIPT tied to the exact
    artifact tree, the serving image and the hardware. Round 8 (Codex,
    FAKE_OPERATIONAL_RECEIPT_ACCEPTED — NaN latency, -9 GB, sha256:x,
    instance 'banana', empty timestamp): every value is now validated
    for shape and physics, the instance must be on the packet's
    predeclared allowlist, and the p95 must RECOMPUTE from the raw
    latency samples the receipt carries."""
    receipt = report.get("operational_evidence")
    if not isinstance(receipt, Mapping):
        raise PromotionCheckRefusal("gate report lacks operational evidence")
    needed = {"state", "latency_p95_ms", "vram_gb", "artifact_tree_sha256",
              "serving_image_digest", "instance_type", "measured_utc",
              "latency_samples_ms", "sample_count"}
    missing = needed - set(receipt)
    if missing:
        raise PromotionCheckRefusal(
            f"operational receipt lacks {sorted(missing)} — measurements "
            "must be attributable to artifact, image and hardware")
    if receipt.get("artifact_tree_sha256") != artifact_tree_sha256:
        raise PromotionCheckRefusal(
            "operational receipt measures a DIFFERENT artifact tree")
    image = str(receipt.get("serving_image_digest", ""))
    if not (image.startswith("sha256:") and _hex(image[7:], 64)):
        raise PromotionCheckRefusal(
            "serving_image_digest must be a full sha256:<64 hex> identity")
    if not _utc_ok(receipt.get("measured_utc")):
        raise PromotionCheckRefusal(
            "measured_utc must be a valid YYYY-MM-DDTHH:MM:SSZ timestamp")
    allowed_instances = candidate_packet.get("allowed_instance_types")
    if not isinstance(allowed_instances, list) or not allowed_instances:
        raise PromotionCheckRefusal(
            "candidate packet predeclares no allowed instance types")
    if receipt.get("instance_type") not in allowed_instances:
        raise PromotionCheckRefusal(
            f"instance_type {receipt.get('instance_type')!r} is not on the "
            f"packet's predeclared allowlist {allowed_instances}")
    samples = receipt.get("latency_samples_ms")
    if (not isinstance(samples, list) or len(samples) < 20
            or receipt.get("sample_count") != len(samples)):
        raise PromotionCheckRefusal(
            "operational receipt must carry >=20 raw latency samples and "
            "a sample_count equal to their number")
    values = sorted(_finite_nonneg(s, "latency sample") for s in samples)
    recomputed_p95 = values[min(len(values) - 1,
                                  int(0.95 * (len(values) - 1)))]
    latency = _finite_nonneg(receipt["latency_p95_ms"], "latency_p95_ms")
    if abs(latency - recomputed_p95) > 1e-9:
        raise PromotionCheckRefusal(
            f"latency_p95_ms={latency} does not recompute from the raw "
            f"samples (p95={recomputed_p95})")
    vram = _finite_nonneg(receipt["vram_gb"], "vram_gb")
    thresholds = candidate_packet.get("operational_thresholds", {})
    max_latency = _finite_nonneg(
        thresholds.get("max_latency_p95_ms"), "max_latency_p95_ms")
    max_vram = _finite_nonneg(thresholds.get("max_vram_gb"), "max_vram_gb")
    if latency > max_latency or vram > max_vram:
        raise PromotionCheckRefusal(
            f"operational measurements (p95 {latency}ms, {vram}GB) exceed "
            f"the PREDECLARED thresholds ({max_latency}ms, {max_vram}GB)")


def require_holdout_grades(report: dict, *,
                           grade_authority: Mapping[str, Mapping[str, Any]],
                           mandatory: list[str]) -> None:
    """Round 7 (Codex): the protocol grades sealed sets. Round 10
    (Codex, SYNTHETIC_ALL_PROMOTION_BUNDLE_ACCEPTED): grades come from
    the PINNED AUTHORITY document — never from bundle-supplied bindings
    — and a conditional acknowledgement must equal the authority's
    ACTUAL disclosed caveat, not any nonempty string."""
    for language in mandatory:
        entry = report.get("languages", {}).get(language) or {}
        holdout = str(entry.get("holdout_manifest_sha256", ""))
        authority_entry = grade_authority.get(holdout)
        if authority_entry is None:
            raise PromotionCheckRefusal(
                f"{language}: sealed set carries no recorded grade in the "
                "pinned authority")
        grade = str(authority_entry.get("grade", ""))
        if str(authority_entry.get("language", "")) != language:
            raise PromotionCheckRefusal(
                f"{language}: the pinned authority records this sealed set "
                f"for {authority_entry.get('language')!r} — cross-language "
                "substitution refused")
        if grade == "development_grade_only":
            raise PromotionCheckRefusal(
                f"{language}: a development-grade pool cannot be sole "
                "promotion evidence (placeholder speakers)")
        if grade == "conditional":
            caveat = str(authority_entry.get("caveat", ""))
            if entry.get("conditional_caveat_ack") != caveat:
                raise PromotionCheckRefusal(
                    f"{language}: the conditional acknowledgement must "
                    f"equal the authority's disclosed caveat {caveat!r}")
        elif grade != "promotion_grade":
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
    set. Round 8 (Codex): the REAL sealed manifests are JSONL row files
    with NO utterance_id field — the round-7 verifier invented a wrapper
    object with a different hash and could never consume them. This now
    parses the AUTHORITATIVE JSONL manifest whose raw bytes hash to the
    bound holdout identity, derives the deterministic row identity from
    the immutable audio_checksum_sha256 (unique per utterance), and
    additionally binds each result row's cluster to the manifest row's
    speaker — invented rows, dropped utterances, duplicates and cluster
    manipulation all refuse."""
    for language in mandatory:
        entry = report["languages"][language]
        _verify_rows_cover_manifest(
            language,
            rows_raw=rows_bytes(language),
            manifest_raw=manifest_bytes(language),
            bound_sha=str(entry.get("holdout_manifest_sha256", "")))


def _verify_rows_cover_manifest(label: str, *, rows_raw: bytes | None,
                                manifest_raw: bytes | None,
                                bound_sha: str) -> None:
    """The shared manifest-coverage core (round 9: code-switch uses it
    too). The AUTHORITATIVE JSONL manifest must hash to the bound
    identity; result rows must cover its audio checksums exactly once,
    match each utterance's speaker cluster, and (round 9, reference
    binding) hash the reference text the manifest declares."""
    if manifest_raw is None:
        raise PromotionCheckRefusal(
            f"{label}: sealed holdout manifest absent — row "
            "identity cannot be verified")
    if hashlib.sha256(manifest_raw).hexdigest() != bound_sha:
        raise PromotionCheckRefusal(
            f"{label}: holdout manifest bytes do not hash to the "
            "report's bound sealed-set identity")
    speaker_by_checksum: dict[str, str] = {}
    reference_sha_by_checksum: dict[str, str | None] = {}
    for line in manifest_raw.decode().splitlines():
        if not line.strip():
            continue
        try:
            manifest_row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PromotionCheckRefusal(
                f"{label}: sealed manifest is not valid JSONL"
            ) from exc
        checksum = str(manifest_row.get("audio_checksum_sha256", ""))
        if not _hex(checksum, 64):
            raise PromotionCheckRefusal(
                f"{label}: a sealed manifest row lacks its "
                "audio_checksum_sha256 identity")
        if checksum in speaker_by_checksum:
            raise PromotionCheckRefusal(
                f"{label}: duplicate audio checksum in the sealed "
                "manifest — the set identity is ambiguous")
        speaker_by_checksum[checksum] = str(
            manifest_row.get("speaker_id", ""))
        reference = manifest_row.get("text_normalized")
        reference_sha_by_checksum[checksum] = (
            hashlib.sha256(str(reference).encode()).hexdigest()
            if isinstance(reference, str) and reference else None)
    if not speaker_by_checksum:
        raise PromotionCheckRefusal(
            f"{label}: sealed manifest contains no rows")
    if rows_raw is None:
        raise PromotionCheckRefusal(
            f"{label}: rows absent for sealed-identity check")
    seen: set[str] = set()
    for line in rows_raw.decode().splitlines():
        if not line.strip():
            continue
        result_row = json.loads(line)
        checksum = str(result_row.get("audio_checksum_sha256", ""))
        if not _hex(checksum, 64):
            raise PromotionCheckRefusal(
                f"{label}: every result row must carry the "
                "audio_checksum_sha256 of the sealed utterance it scored")
        if checksum in seen:
            raise PromotionCheckRefusal(
                f"{label}: duplicate utterance rows — each sealed "
                "utterance is scored exactly once")
        seen.add(checksum)
        speaker = speaker_by_checksum.get(checksum)
        if speaker is None:
            raise PromotionCheckRefusal(
                f"{label}: a result row scores an utterance the "
                "sealed manifest never contained")
        if speaker and str(result_row.get("cluster_id")) != speaker:
            raise PromotionCheckRefusal(
                f"{label}: result row cluster does not match the "
                "sealed manifest's speaker for that utterance")
        expected_reference = reference_sha_by_checksum.get(checksum)
        if expected_reference is not None and (
            result_row.get("reference_text_sha256") != expected_reference
        ):
            raise PromotionCheckRefusal(
                f"{label}: result row reference hash does not match the "
                "sealed manifest's reference text — errors must be "
                "scored against the SEALED references")
        # round 10 (Codex): every scored row must BIND both hypotheses —
        # error counts without the texts they were computed from are
        # unauditable (full scorer recomputation remains the declared
        # next gap; the binding makes it possible)
        for side in ("baseline_hypothesis", "candidate_hypothesis"):
            hypothesis = result_row.get(side)
            declared = str(result_row.get(f"{side}_sha256", ""))
            if not isinstance(hypothesis, str):
                raise PromotionCheckRefusal(
                    f"{label}: result row lacks the {side} TEXT — a bare "
                    "hash backs nothing (Codex round 11)")
            if hashlib.sha256(
                hypothesis.encode()).hexdigest() != declared:
                raise PromotionCheckRefusal(
                    f"{label}: {side}_sha256 does not recompute from the "
                    "supplied hypothesis text")
    if seen != set(speaker_by_checksum):
        raise PromotionCheckRefusal(
            f"{label}: result rows do not cover EXACTLY the sealed "
            "utterance set (missing or invented utterances)")


def recompute_code_switch(report: dict, *,
                          rows_bytes: Callable[[str], bytes | None],
                          manifest_bytes: Callable[[str], bytes | None],
                          ) -> None:
    """Round 7 (Codex, FABRICATED_CODE_SWITCH_..._ACCEPTED): the
    code-switch gate is evidence too — its statistics recompute from its
    own hash-bound rows exactly like every language gate. Round 9: its
    declared set is a REAL bundled JSONL manifest verified by the shared
    coverage helper."""
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
    _verify_rows_cover_manifest(
        "code_switch",
        rows_raw=body,
        manifest_raw=manifest_bytes("code_switch"),
        bound_sha=str(evidence.get("manifest_sha256", "")))
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


def verify_packet_chronology(report: dict, *,
                             anchor_envelope: Mapping[str, Any],
                             packet_bytes: bytes,
                             candidate_packet: Mapping[str, Any],
                             anchor_fetch: Callable[
                                 [Mapping[str, Any]], tuple[bytes, str]],
                             sealed_start_fetch: Callable[
                                 [Mapping[str, Any]], Mapping[str, Any]],
                             ) -> dict[str, Any]:
    """The LIVE chronology verification the ADMISSION pipeline runs
    (rounds 8-10, Codex): a separate envelope names the packet identity
    and storage; the storage-set timestamp must precede the AWS-set
    creation time of the ONE sealed job the packet PREDECLARED, and that
    job must have Completed. Returns the material for the attested
    admission receipt the runtime later verifies offline."""
    declared_sha = str(anchor_envelope.get("packet_sha256") or "")
    if hashlib.sha256(packet_bytes).hexdigest() != declared_sha:
        raise PromotionCheckRefusal(
            "the bundled candidate packet does not hash to the anchor "
            "envelope's declared identity")
    storage = anchor_envelope.get("storage")
    if not isinstance(storage, Mapping) or not storage:
        raise PromotionCheckRefusal(
            "anchor envelope names no immutable storage coordinates")
    anchored_bytes, anchored_utc = anchor_fetch(storage)
    if hashlib.sha256(anchored_bytes).hexdigest() != declared_sha:
        raise PromotionCheckRefusal(
            "the anchored candidate packet bytes differ from the bundled "
            "packet — the predeclaration was edited after anchoring")
    if not _utc_ok(anchored_utc):
        raise PromotionCheckRefusal(
            "the anchor authority returned no valid timestamp")
    contract = candidate_packet.get("sealed_run")
    CONTRACT_FIELDS = ("job_name", "image_digest", "instance_type",
                        "input_s3_uris", "output_s3_prefix",
                        "output_kms_key_arn", "account_id", "region")
    if not isinstance(contract, Mapping) or any(
        not contract.get(field) for field in CONTRACT_FIELDS
    ):
        raise PromotionCheckRefusal(
            "candidate packet must PREDECLARE the COMPLETE sealed-run "
            f"contract ({', '.join(CONTRACT_FIELDS)}) — a job name alone "
            "binds nothing (Codex round 11)")
    job = report.get("sealed_run_job")
    if not isinstance(job, Mapping) or str(job.get("name")) != str(
        contract["job_name"]
    ):
        raise PromotionCheckRefusal(
            "the sealed job must be the ONE the packet predeclared — an "
            "unrelated job cannot provide the chronology clock")
    described = sealed_start_fetch(job)
    creation_utc = str(described.get("creation_utc", ""))
    if not _utc_ok(creation_utc):
        raise PromotionCheckRefusal(
            "the sealed-run authority returned no valid start timestamp")
    if described.get("status") != "Completed":
        raise PromotionCheckRefusal(
            f"the sealed run is {described.get('status')!r}, not "
            "Completed — an unfinished or failed run cannot promote")
    # round 11 (Codex, UNBOUND_COMPLETED_JOB_ACCEPTED): the DESCRIBED
    # job must match the predeclared contract on every dimension — an
    # unrelated completed job proves nothing
    live_checks = {
        "image_digest": described.get("image"),
        "instance_type": described.get("instance_type"),
        "output_s3_prefix": described.get("output_path"),
        "output_kms_key_arn": described.get("output_kms"),
    }
    for field, actual in live_checks.items():
        if str(actual) != str(contract[field]):
            raise PromotionCheckRefusal(
                f"sealed job {field}={actual!r} does not match the "
                f"predeclared {contract[field]!r}")
    described_inputs = set(map(str, described.get("input_uris") or []))
    if not set(map(str, contract["input_s3_uris"])) <= described_inputs:
        raise PromotionCheckRefusal(
            "the sealed job did not read the predeclared holdout inputs")
    arn = str(described.get("arn", ""))
    if (f":{contract['account_id']}:" not in arn
            or f":{contract['region']}:" not in arn):
        raise PromotionCheckRefusal(
            "the sealed job does not belong to the predeclared "
            "account/region")
    if anchored_utc >= creation_utc:
        raise PromotionCheckRefusal(
            f"the packet was anchored at {anchored_utc}, NOT before the "
            f"sealed run started at {creation_utc} — post-hoc "
            "predeclaration refused")
    return {"packet_sha256": declared_sha, "anchored_utc": anchored_utc,
            "sealed_job": {"name": str(contract["job_name"]),
                            "creation_utc": creation_utc,
                            "status": "Completed",
                            **{field: contract[field]
                               for field in CONTRACT_FIELDS
                               if field != "job_name"}}}


def verify_admission_receipt(report: dict, *,
                             anchor_envelope: Mapping[str, Any],
                             packet_bytes: bytes,
                             candidate_packet: Mapping[str, Any],
                             admission_receipt: Mapping[str, Any]) -> None:
    """Round 10 (Codex): the RUNTIME does not call AWS — its loader role
    has (correctly) no sagemaker:Describe* or s3:GetObjectVersion. The
    ADMISSION pipeline performs the live chronology verification with
    proper credentials and writes this attested receipt into the bundle;
    the runtime verifies the receipt's internal consistency against the
    packet, envelope and report it also holds."""
    declared_sha = str(anchor_envelope.get("packet_sha256") or "")
    if hashlib.sha256(packet_bytes).hexdigest() != declared_sha:
        raise PromotionCheckRefusal(
            "the bundled candidate packet does not hash to the anchor "
            "envelope's declared identity")
    if admission_receipt.get("packet_sha256") != declared_sha:
        raise PromotionCheckRefusal(
            "the admission receipt attests a DIFFERENT candidate packet")
    anchored_utc = str(admission_receipt.get("anchored_utc", ""))
    job = admission_receipt.get("sealed_job")
    if not isinstance(job, Mapping):
        raise PromotionCheckRefusal(
            "the admission receipt binds no sealed job")
    creation_utc = str(job.get("creation_utc", ""))
    if not _utc_ok(anchored_utc) or not _utc_ok(creation_utc):
        raise PromotionCheckRefusal(
            "the admission receipt carries invalid timestamps")
    if anchored_utc >= creation_utc:
        raise PromotionCheckRefusal(
            f"the packet was anchored at {anchored_utc}, NOT before the "
            f"sealed run started at {creation_utc} — post-hoc "
            "predeclaration refused")
    if job.get("status") != "Completed":
        raise PromotionCheckRefusal(
            f"the sealed run is {job.get('status')!r}, not Completed — "
            "an unfinished or failed run cannot promote")
    contract = candidate_packet.get("sealed_run")
    if not isinstance(contract, Mapping) or not contract.get("job_name"):
        raise PromotionCheckRefusal(
            "candidate packet must PREDECLARE the sealed-run contract")
    report_job = report.get("sealed_run_job") or {}
    if not (str(contract["job_name"]) == str(job.get("name"))
            == str(report_job.get("name"))):
        raise PromotionCheckRefusal(
            "the sealed job must be the ONE the packet predeclared — an "
            "unrelated job cannot provide the chronology clock")
    # round 11: the receipt's verified contract must EQUAL the packet's
    for field in ("image_digest", "instance_type", "output_s3_prefix",
                   "output_kms_key_arn", "account_id", "region"):
        if str(job.get(field)) != str(contract.get(field)):
            raise PromotionCheckRefusal(
                f"admission receipt {field} does not match the "
                "predeclared sealed-run contract")
    if (sorted(map(str, job.get("input_s3_uris") or []))
            != sorted(map(str, contract.get("input_s3_uris") or []))):
        raise PromotionCheckRefusal(
            "admission receipt inputs do not match the predeclared "
            "holdout inputs")


def require_licensed_code_switch(report: dict, *,
                                 candidate_packet: Mapping[str, Any],
                                 licensed_sets: Mapping[str, Mapping[str, Any]],
                                 ) -> None:
    """Round 10 (Codex): the code-switch set must resolve to a LICENSED,
    reserved, speaker-disjoint holdout registered in the pinned
    authority — a format-valid synthetic manifest is not a licensed
    dataset. The registry is EMPTY until the owner's code-switch
    acquisition lands, which makes production promotion structurally
    impossible today — exactly what the protocol requires."""
    declared = candidate_packet.get("code_switch") or {}
    name = str(declared.get("set", ""))
    registered = licensed_sets.get(name)
    if registered is None:
        raise PromotionCheckRefusal(
            f"code-switch set {name!r} is not in the pinned licensed-set "
            "registry — the first production promotion requires the "
            "owner's licensed code-switch acquisition")
    # round 11 (Codex): a registered set must be FULLY specified — a
    # manifest hash alone says nothing about licensing or reservation
    for field in ("manifest_sha256", "license_record",
                   "reservation_ledger_entry", "speaker_disjoint"):
        if not registered.get(field):
            raise PromotionCheckRefusal(
                f"licensed code-switch registry entry lacks {field} — "
                "registration requires license, reservation and "
                "speaker-disjointness evidence")
    if registered.get("speaker_disjoint") is not True:
        raise PromotionCheckRefusal(
            "licensed code-switch set must be speaker-disjoint")
    if registered.get("manifest_sha256") != declared.get("manifest_sha256"):
        raise PromotionCheckRefusal(
            "code-switch manifest does not match the licensed registry")


def verify_complete_promotion(report: dict, *,
                              protocol: Mapping[str, Any],
                              holdouts_by_language: Mapping[str, set[str]],
                              grade_authority: Mapping[str, Mapping[str, Any]],
                              licensed_code_switch_sets: Mapping[
                                  str, Mapping[str, Any]],
                              candidate_packet: Mapping[str, Any],
                              packet_bytes: bytes,
                              anchor_envelope: Mapping[str, Any],
                              artifact_tree_sha256: str,
                              rows_bytes: Callable[[str], bytes | None],
                              manifest_bytes: Callable[[str], bytes | None],
                              verify_chronology: Callable[[], None],
                              ) -> dict[str, str]:
    """Round 8 (Codex): the ONE complete promotion gate. The repo-side
    checker and the runtime bundle verifier both call THIS with their
    own authority adapters — a split-brain gate is how every prior
    bypass survived. Order matters only for error quality; every check
    must pass."""
    mandatory = list(protocol.get("mandatory_languages", []))
    if not mandatory:
        raise PromotionCheckRefusal("protocol declares no mandatory set")
    if str(report.get("candidate_digest") or "") != (
        f"sha256:{artifact_tree_sha256}"
    ):
        raise PromotionCheckRefusal(
            "gate report candidate_digest does not name this artifact tree")
    validate_report_structure(report)
    states = promotable_languages(report, mandatory)
    require_candidate_packet(
        report, candidate_packet=candidate_packet, mandatory=mandatory)
    verify_chronology()
    require_licensed_code_switch(
        report, candidate_packet=candidate_packet,
        licensed_sets=licensed_code_switch_sets)
    require_protocol_evidence(
        report, mandatory, protocol=protocol,
        holdouts_by_language=holdouts_by_language)
    require_holdout_grades(
        report, grade_authority=grade_authority, mandatory=mandatory)
    recompute_statistics(
        report, mandatory, mandatory=mandatory, rows_bytes=rows_bytes)
    require_sealed_row_identity(
        report, mandatory, rows_bytes=rows_bytes,
        manifest_bytes=manifest_bytes)
    recompute_code_switch(report, rows_bytes=rows_bytes,
                          manifest_bytes=manifest_bytes)
    require_operational_receipt(
        report, candidate_packet=candidate_packet,
        artifact_tree_sha256=artifact_tree_sha256)
    return states
