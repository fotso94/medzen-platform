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
import re
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
    # round 13 (Codex): the scorer is named by id and must be the BAKED
    # one — resolve_scorer() enforces the hash; here the id must exist
    if not candidate_packet.get("scorer_id"):
        raise PromotionCheckRefusal(
            "candidate packet must predeclare the scorer_id of a scorer "
            "baked into the reviewed loader image")
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
                                scorer: Mapping[str, Any] | None = None,
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
            bound_sha=str(entry.get("holdout_manifest_sha256", "")),
            scorer=scorer)


SCORER_REGISTRY = {
    # round 13 (Codex, ARBITRARY CODE EXECUTION through bundled scorer.py):
    # the scorer is BAKED into the reviewed loader image and referenced by
    # a fixed id + the sha256 of the baked module's own source bytes.
    # Signed evidence is DATA; the bundle may carry no executable code.
    "scorer_v1": "scorer_v1.py",
}


def scorer_registry_sha256(scorer_id: str) -> str:
    """sha256 of the BAKED module source for a registered scorer id."""
    import hashlib
    from pathlib import Path
    filename = SCORER_REGISTRY.get(str(scorer_id))
    if filename is None:
        raise PromotionCheckRefusal(
            f"scorer id {scorer_id!r} is not in the baked scorer registry "
            f"({sorted(SCORER_REGISTRY)}) — evidence cannot name a scorer "
            "this image does not carry")
    return hashlib.sha256(
        (Path(__file__).resolve().parent / filename).read_bytes()).hexdigest()


def resolve_scorer(candidate_packet: Mapping[str, Any]):
    """Resolve the predeclared scorer to the BAKED implementation. The
    packet names scorer_id and scorer_sha256; the sha256 must equal the
    baked module's source bytes — a packet naming any other scoring code
    refuses. Nothing from the bundle is ever compiled or executed."""
    scorer_id = str(candidate_packet.get("scorer_id", ""))
    declared = str(candidate_packet.get("scorer_sha256", "")).lower()
    baked = scorer_registry_sha256(scorer_id)
    if declared != baked:
        raise PromotionCheckRefusal(
            f"predeclared scorer_sha256 {declared[:12]} is not the baked "
            f"{scorer_id} ({baked[:12]}) — the scoring code is part of the "
            "reviewed image, never of the evidence")
    import importlib
    module = importlib.import_module(
        f".{SCORER_REGISTRY[scorer_id][:-3]}", package=__package__)
    namespace = {"score_errors": module.score_errors,
                 "reference_words": module.reference_words}
    for required, value in namespace.items():
        if not callable(value):
            raise PromotionCheckRefusal(
                f"the baked scorer exposes no {required}()")
    return namespace


def _verify_rows_cover_manifest(label: str, *, rows_raw: bytes | None,
                                manifest_raw: bytes | None,
                                bound_sha: str,
                                scorer: Mapping[str, Any] | None = None,
                                ) -> None:
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
    reference_text_by_checksum: dict[str, str | None] = {}
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
        reference_text_by_checksum[checksum] = (
            reference if isinstance(reference, str) and reference else None)
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
        # round 12 (Codex): EXECUTE the pinned scorer — every error
        # count and word count must recompute from the bound texts
        if scorer is not None:
            reference_text = reference_text_by_checksum.get(checksum)
            if reference_text is None:
                raise PromotionCheckRefusal(
                    f"{label}: sealed manifest row carries no reference "
                    "text — scored evidence cannot be recomputed")
            expected = {
                "baseline_errors": scorer["score_errors"](
                    reference_text, result_row["baseline_hypothesis"]),
                "candidate_errors": scorer["score_errors"](
                    reference_text, result_row["candidate_hypothesis"]),
                "reference_words": scorer["reference_words"](
                    reference_text),
            }
            for field, recomputed in expected.items():
                if result_row.get(field) != recomputed:
                    raise PromotionCheckRefusal(
                        f"{label}: {field}={result_row.get(field)!r} does "
                        f"not recompute from the bound texts "
                        f"(scorer says {recomputed}) — supplied numbers "
                        "prove nothing")
    if seen != set(speaker_by_checksum):
        raise PromotionCheckRefusal(
            f"{label}: result rows do not cover EXACTLY the sealed "
            "utterance set (missing or invented utterances)")


def recompute_code_switch(report: dict, *,
                          rows_bytes: Callable[[str], bytes | None],
                          manifest_bytes: Callable[[str], bytes | None],
                          scorer: Mapping[str, Any] | None = None,
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
        bound_sha=str(evidence.get("manifest_sha256", "")),
        scorer=scorer)
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


CONTRACT_FIELDS = (
    # round 11-12: identity, image, compute, inputs, outputs, account
    "job_name", "image_digest", "instance_type", "channels",
    "output_s3_prefix", "output_kms_key_arn", "account_id", "region",
    "execution_role_arn", "network_isolation", "volume_kms_key_arn",
    "hyperparameters_sha256",
    # round 13 (Codex, PARTIAL CONTRACT): environment, VPC, runtime
    # limits, instance count, volume size, checkpoint configuration
    "instance_count", "volume_size_gb", "max_runtime_seconds",
    "vpc_config", "checkpoint_config", "environment_sha256",
)
CHANNEL_FIELDS = ("s3_uri", "s3_data_type", "s3_data_distribution_type",
                  "content_type", "compression_type", "input_mode")
_IMAGE_DIGEST_RE = re.compile(r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws"
                              r"\.com/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _normalize_channels(channels: Any) -> dict[str, dict[str, str | None]]:
    if not isinstance(channels, Mapping) or not channels:
        raise PromotionCheckRefusal(
            "sealed-run channels must be a non-empty mapping of channel "
            "name to the COMPLETE input description")
    out: dict[str, dict[str, str | None]] = {}
    for name, spec in channels.items():
        if not isinstance(spec, Mapping) or set(spec) != set(CHANNEL_FIELDS):
            raise PromotionCheckRefusal(
                f"channel {name!r} must describe exactly "
                f"{list(CHANNEL_FIELDS)} — a URI alone is not an input "
                "contract (Codex round 13)")
        out[str(name)] = {field: (None if spec[field] is None
                                  else str(spec[field]))
                          for field in CHANNEL_FIELDS}
    return out


def validate_sealed_run_contract(contract: Any) -> dict[str, Any]:
    """The PREDECLARED sealed-run contract must be complete and strict:
    immutable ECR digest (a well-formed reference, not merely a string
    containing '@sha256:'), network isolation REQUIRED, exact channel
    semantics, and every runtime/security dimension named."""
    if not isinstance(contract, Mapping) or any(
        contract.get(field) in (None, "", [], {})
        for field in CONTRACT_FIELDS
    ):
        raise PromotionCheckRefusal(
            "candidate packet must PREDECLARE the COMPLETE sealed-run "
            f"contract ({', '.join(CONTRACT_FIELDS)}) — a job name alone "
            "binds nothing (Codex rounds 11-13)")
    if not _IMAGE_DIGEST_RE.fullmatch(str(contract["image_digest"])):
        raise PromotionCheckRefusal(
            "sealed-run image must be a well-formed IMMUTABLE ECR "
            "reference <account>.dkr.ecr.<region>.amazonaws.com/<repo>"
            "@sha256:<64 hex> — malformed or tagged references refuse")
    if contract["network_isolation"] is not True:
        raise PromotionCheckRefusal(
            "sealed runs REQUIRE network isolation — a run that could "
            "reach the network could have read anything (Codex round 13)")
    for field in ("instance_count", "volume_size_gb",
                   "max_runtime_seconds"):
        value = contract[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PromotionCheckRefusal(
                f"sealed-run {field} must be a positive integer")
    vpc = contract["vpc_config"]
    if vpc != "none" and (not isinstance(vpc, Mapping) or set(vpc) != {
            "security_group_ids", "subnets"}):
        raise PromotionCheckRefusal(
            "sealed-run vpc_config must be 'none' or "
            "{security_group_ids, subnets}")
    if not _hex(str(contract["environment_sha256"]), 64) or not _hex(
            str(contract["hyperparameters_sha256"]), 64):
        raise PromotionCheckRefusal(
            "sealed-run environment_sha256 / hyperparameters_sha256 must "
            "be sha256 hex")
    normalized = dict(contract)
    normalized["channels"] = _normalize_channels(contract["channels"])
    if isinstance(vpc, Mapping):
        normalized["vpc_config"] = {
            "security_group_ids": sorted(str(x) for x in
                                         vpc["security_group_ids"]),
            "subnets": sorted(str(x) for x in vpc["subnets"])}
    return normalized


def _describe_contract_view(described: Mapping[str, Any],
                            contract: Mapping[str, Any]) -> dict[str, Any]:
    """Project the DESCRIBED job onto the contract's fields for an exact
    comparison; account/region come from the ARN."""
    view = {field: described.get(field) for field in CONTRACT_FIELDS
            if field not in ("job_name", "account_id", "region",
                             "channels", "vpc_config")}
    described_channels = described.get("channels") or {}
    view["channels"] = {
        str(name): {field: (None if spec.get(field) is None
                            else str(spec.get(field)))
                    for field in CHANNEL_FIELDS}
        for name, spec in described_channels.items()}
    vpc = described.get("vpc_config")
    view["vpc_config"] = ({"security_group_ids": sorted(
        str(x) for x in vpc.get("security_group_ids", [])),
        "subnets": sorted(str(x) for x in vpc.get("subnets", []))}
        if isinstance(vpc, Mapping) else "none")
    return view


def _compare_contract(described_view: Mapping[str, Any],
                      contract: Mapping[str, Any], *, label: str) -> None:
    for field in CONTRACT_FIELDS:
        if field in ("job_name", "account_id", "region"):
            continue
        actual, declared = described_view.get(field), contract.get(field)
        if _canonical(actual) != _canonical(declared) and not (
            field not in ("channels", "vpc_config", "network_isolation",
                          "instance_count", "volume_size_gb",
                          "max_runtime_seconds")
            and str(actual) == str(declared)
        ):
            raise PromotionCheckRefusal(
                f"{label} {field}={actual!r} does not match the "
                f"predeclared {declared!r}")


def verify_sealed_outputs(report: dict, *,
                          candidate_packet: Mapping[str, Any],
                          contract: Mapping[str, Any],
                          artifact_tree_sha256: str,
                          rows_bytes: Callable[[str], bytes | None],
                          job_window: tuple[str, str],
                          output_object_fetch: Callable[
                              [str, str], tuple[bytes, str, str]],
                          ) -> dict[str, Any]:
    """Round 13 (Codex, 'the evidence does not prove the model generated
    the hypotheses'): every bundled row file must be BYTE-IDENTICAL to an
    immutable, versioned object the sealed job itself wrote under its
    output prefix, inside the job's execution window; the job's own
    INFERENCE RECEIPT (also an output object) must name this artifact
    tree, the predeclared decoding configuration, the contract image
    and every row object's sha256. Returns the verified identities for
    the attested admission receipt."""
    outputs = report.get("sealed_outputs")
    if not isinstance(outputs, Mapping):
        raise PromotionCheckRefusal(
            "gate report binds no sealed_outputs — rows must be the "
            "sealed job's own immutable output objects")
    decoding = str(candidate_packet.get("decoding_config_sha256", ""))
    if not _hex(decoding, 64):
        raise PromotionCheckRefusal(
            "candidate packet must predeclare decoding_config_sha256 — "
            "the decoding configuration is part of the method")
    prefix = str(contract["output_s3_prefix"]).rstrip("/") + "/"
    start_utc, end_utc = job_window
    if not _utc_ok(start_utc) or not _utc_ok(end_utc):
        raise PromotionCheckRefusal(
            "the sealed-run authority returned no valid execution window")

    def fetch(label: str, ref: Any) -> bytes:
        if not isinstance(ref, Mapping) or not all(
                ref.get(k) for k in ("s3_uri", "version_id", "sha256")):
            raise PromotionCheckRefusal(
                f"{label}: output object must name s3_uri, version_id "
                "and sha256")
        uri = str(ref["s3_uri"])
        if not uri.startswith(prefix):
            raise PromotionCheckRefusal(
                f"{label}: {uri} is not under the sealed job's output "
                f"prefix {prefix} — an object the job did not write")
        body, modified_utc, _etag = output_object_fetch(
            uri, str(ref["version_id"]))
        if hashlib.sha256(body).hexdigest() != str(ref["sha256"]).lower():
            raise PromotionCheckRefusal(
                f"{label}: versioned object bytes do not hash to the "
                "declared sha256")
        if not (start_utc <= modified_utc <= end_utc):
            raise PromotionCheckRefusal(
                f"{label}: object written at {modified_utc}, outside the "
                f"sealed job's window {start_utc}..{end_utc}")
        return body

    receipt_ref = outputs.get("inference_receipt")
    receipt_body = fetch("inference receipt", receipt_ref)
    # Codex review #14 finding 2 (sealed-output provenance forgeable): the
    # object's prefix/version/window/hash prove WHAT was written, not WHO
    # wrote it. The evaluator SIGNS its inference receipt with a dedicated
    # KMS key only the sealed-evaluator role can use; admission verifies
    # that signature so a bare S3 writer cannot forge internally-consistent
    # results. The signature is itself a versioned output object.
    from .signing import SignatureRefusal, verify_evaluator_signature
    sig_body = fetch("evaluator signature", outputs.get("evaluator_signature"))
    try:
        verify_evaluator_signature(receipt_body, sig_body)
    except SignatureRefusal as exc:
        raise PromotionCheckRefusal(str(exc)) from exc
    try:
        inference = json.loads(receipt_body)
    except ValueError as exc:
        raise PromotionCheckRefusal(
            "inference receipt is not JSON") from exc
    if not isinstance(inference, Mapping):
        raise PromotionCheckRefusal("inference receipt is not an object")
    expectations = {
        "artifact_tree_sha256": artifact_tree_sha256,
        "decoding_config_sha256": decoding,
        "image_digest": str(contract["image_digest"]),
        "job_name": str(contract["job_name"]),
    }
    for field, expected in expectations.items():
        if str(inference.get(field)) != expected:
            raise PromotionCheckRefusal(
                f"inference receipt {field}={inference.get(field)!r} does "
                f"not bind the predeclared {expected!r} — the job did not "
                "attest to running THIS artifact with THIS method")
    declared_rows = outputs.get("rows")
    receipt_rows = inference.get("rows_sha256")
    if not isinstance(declared_rows, Mapping) or not declared_rows:
        raise PromotionCheckRefusal("sealed_outputs names no row objects")
    if not isinstance(receipt_rows, Mapping):
        raise PromotionCheckRefusal(
            "inference receipt names no rows_sha256 — the job must attest "
            "to its own outputs")
    verified_rows: dict[str, dict[str, str]] = {}
    for label, ref in declared_rows.items():
        body = fetch(f"rows[{label}]", ref)
        digest = hashlib.sha256(body).hexdigest()
        if str(receipt_rows.get(label, "")).lower() != digest:
            raise PromotionCheckRefusal(
                f"rows[{label}]: the inference receipt does not attest "
                "this row object")
        bundled = rows_bytes(label)
        if bundled is None or hashlib.sha256(bundled).hexdigest() != digest:
            raise PromotionCheckRefusal(
                f"rows[{label}]: bundled row bytes are not the sealed "
                "job's immutable output object")
        verified_rows[str(label)] = {
            "s3_uri": str(ref["s3_uri"]), "version_id": str(ref["version_id"]),
            "sha256": digest}
    if set(receipt_rows) != set(verified_rows):
        raise PromotionCheckRefusal(
            "the inference receipt attests row objects the report does "
            "not bind (or vice versa)")
    return {"inference_receipt": {
                "s3_uri": str(receipt_ref["s3_uri"]),
                "version_id": str(receipt_ref["version_id"]),
                "sha256": hashlib.sha256(receipt_body).hexdigest()},
            "evaluator_signature": {
                "s3_uri": str(outputs["evaluator_signature"]["s3_uri"]),
                "version_id": str(outputs["evaluator_signature"]["version_id"]),
                "sha256": hashlib.sha256(sig_body).hexdigest()},
            "rows": verified_rows,
            "decoding_config_sha256": decoding}


def verify_packet_chronology(report: dict, *,
                             anchor_envelope: Mapping[str, Any],
                             packet_bytes: bytes,
                             candidate_packet: Mapping[str, Any],
                             anchor_fetch: Callable[
                                 [Mapping[str, Any]], tuple[bytes, str]],
                             sealed_start_fetch: Callable[
                                 [Mapping[str, Any]], Mapping[str, Any]],
                             artifact_tree_sha256: str,
                             rows_bytes: Callable[[str], bytes | None],
                             output_object_fetch: Callable[
                                 [str, str], tuple[bytes, str, str]],
                             ) -> dict[str, Any]:
    """The LIVE chronology + contract + output verification the
    ADMISSION pipeline runs (rounds 8-13, Codex): a separate envelope
    names the packet identity and storage; the storage-set timestamp
    must precede the AWS-set creation time of the ONE sealed job the
    packet PREDECLARED; that job must have Completed and must EQUAL the
    predeclared contract on every dimension; and every bundled row file
    must be the job's own immutable output. Returns the material for
    the attested admission receipt the runtime later verifies offline."""
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
    contract = validate_sealed_run_contract(candidate_packet.get("sealed_run"))
    job = report.get("sealed_run_job")
    if not isinstance(job, Mapping) or str(job.get("name")) != str(
        contract["job_name"]
    ):
        raise PromotionCheckRefusal(
            "the sealed job must be the ONE the packet predeclared — an "
            "unrelated job cannot provide the chronology clock")
    described = sealed_start_fetch(job)
    creation_utc = str(described.get("creation_utc", ""))
    end_utc = str(described.get("end_utc", ""))
    if not _utc_ok(creation_utc):
        raise PromotionCheckRefusal(
            "the sealed-run authority returned no valid start timestamp")
    if described.get("status") != "Completed":
        raise PromotionCheckRefusal(
            f"the sealed run is {described.get('status')!r}, not "
            "Completed — an unfinished or failed run cannot promote")
    # rounds 11-13: the DESCRIBED job must EQUAL the predeclared contract
    # on EVERY dimension — an unrelated or differently-configured job
    # proves nothing
    view = _describe_contract_view(described, contract)
    _compare_contract(view, contract, label="sealed job")
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
    sealed_outputs = verify_sealed_outputs(
        report, candidate_packet=candidate_packet, contract=contract,
        artifact_tree_sha256=artifact_tree_sha256, rows_bytes=rows_bytes,
        job_window=(creation_utc, end_utc),
        output_object_fetch=output_object_fetch)
    sealed_job = dict(view)
    sealed_job.update({"name": str(contract["job_name"]),
                       "creation_utc": creation_utc, "end_utc": end_utc,
                       "status": "Completed",
                       "account_id": contract["account_id"],
                       "region": contract["region"],
                       "output_artifact_s3_uri": described.get(
                           "output_artifact_s3_uri")})
    return {"packet_sha256": declared_sha, "anchored_utc": anchored_utc,
            "sealed_job": sealed_job, "sealed_outputs": sealed_outputs}


def verify_admission_receipt(report: dict, *,
                             anchor_envelope: Mapping[str, Any],
                             packet_bytes: bytes,
                             candidate_packet: Mapping[str, Any],
                             admission_receipt: Mapping[str, Any],
                             artifact_tree_sha256: str,
                             rows_bytes: Callable[[str], bytes | None],
                             ) -> None:
    """Round 10 (Codex): the RUNTIME does not call AWS — its loader role
    has (correctly) no sagemaker:Describe* or s3:GetObjectVersion. The
    ADMISSION pipeline performs the live verification with proper
    credentials and writes this attested receipt into the bundle; the
    runtime verifies the receipt's internal consistency against the
    packet, envelope, report and row bytes it also holds."""
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
    contract = validate_sealed_run_contract(candidate_packet.get("sealed_run"))
    report_job = report.get("sealed_run_job") or {}
    if not (str(contract["job_name"]) == str(job.get("name"))
            == str(report_job.get("name"))):
        raise PromotionCheckRefusal(
            "the sealed job must be the ONE the packet predeclared — an "
            "unrelated job cannot provide the chronology clock")
    # rounds 11-13: the receipt's verified contract must EQUAL the
    # packet's on every dimension
    _compare_contract(_describe_contract_view(job, contract), contract,
                      label="admission receipt")
    for field in ("account_id", "region"):
        if str(job.get(field)) != str(contract[field]):
            raise PromotionCheckRefusal(
                f"admission receipt {field} does not match the "
                "predeclared sealed-run contract")
    # round 13: the receipt's verified output identities must be the
    # report's, and the bundled rows must hash to them
    attested = admission_receipt.get("sealed_outputs")
    declared = report.get("sealed_outputs")
    if not isinstance(attested, Mapping) or not isinstance(declared, Mapping):
        raise PromotionCheckRefusal(
            "admission receipt or gate report binds no sealed_outputs")
    if _canonical(attested.get("inference_receipt")) != _canonical(
            declared.get("inference_receipt")):
        raise PromotionCheckRefusal(
            "admission receipt attests a different inference receipt")
    # round 14 (Codex finding 2): the evaluator-signature identity the
    # admission verified must equal the report's declared one
    if _canonical(attested.get("evaluator_signature")) != _canonical(
            declared.get("evaluator_signature")):
        raise PromotionCheckRefusal(
            "admission receipt attests a different evaluator signature")
    if str(attested.get("decoding_config_sha256")) != str(
            candidate_packet.get("decoding_config_sha256")):
        raise PromotionCheckRefusal(
            "admission receipt decoding configuration is not the "
            "predeclared one")
    attested_rows = attested.get("rows") or {}
    declared_rows = declared.get("rows") or {}
    if _canonical(attested_rows) != _canonical(declared_rows) or not attested_rows:
        raise PromotionCheckRefusal(
            "admission receipt row objects do not equal the report's")
    for label, ref in attested_rows.items():
        bundled = rows_bytes(str(label))
        if bundled is None or hashlib.sha256(bundled).hexdigest() != str(
                ref.get("sha256", "")).lower():
            raise PromotionCheckRefusal(
                f"rows[{label}]: bundled row bytes are not the attested "
                "sealed-job output object")


def require_licensed_code_switch(report: dict, *,
                                 candidate_packet: Mapping[str, Any],
                                 licensed_sets: Mapping[str, Mapping[str, Any]],
                                 document_bytes: Callable[[str], bytes | None],
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
    for field in ("manifest_sha256", "license_record", "license_sha256",
                   "reservation_ledger_entry", "reservation_sha256",
                   "speaker_disjoint"):
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
    # round 13 (Codex, PRESENCE-ONLY): the licence record and the
    # reservation ledger entry are OPENED, hashed against the registry,
    # and must themselves name this manifest — a path naming nothing,
    # or a document about some other set, refuses
    for doc_field, sha_field, must_name in (
            ("license_record", "license_sha256", "manifest_sha256"),
            ("reservation_ledger_entry", "reservation_sha256",
             "manifest_sha256")):
        path = str(registered[doc_field])
        body = document_bytes(path)
        if body is None:
            raise PromotionCheckRefusal(
                f"licensed code-switch {doc_field} {path!r} does not "
                "resolve to a document")
        if hashlib.sha256(body).hexdigest() != str(
                registered[sha_field]).lower():
            raise PromotionCheckRefusal(
                f"licensed code-switch {doc_field} does not hash to the "
                f"registry's {sha_field}")
        try:
            document = json.loads(body)
        except ValueError as exc:
            raise PromotionCheckRefusal(
                f"licensed code-switch {doc_field} is not JSON") from exc
        if not isinstance(document, Mapping) or str(
                document.get(must_name)) != str(registered["manifest_sha256"]):
            raise PromotionCheckRefusal(
                f"licensed code-switch {doc_field} does not name this "
                "set's manifest_sha256 — a document about another set")


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
                              document_bytes: Callable[[str], bytes | None],
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
        licensed_sets=licensed_code_switch_sets,
        document_bytes=document_bytes)
    require_protocol_evidence(
        report, mandatory, protocol=protocol,
        holdouts_by_language=holdouts_by_language)
    require_holdout_grades(
        report, grade_authority=grade_authority, mandatory=mandatory)
    recompute_statistics(
        report, mandatory, mandatory=mandatory, rows_bytes=rows_bytes)
    scorer = resolve_scorer(candidate_packet)   # baked, never bundle code
    require_sealed_row_identity(
        report, mandatory, rows_bytes=rows_bytes,
        manifest_bytes=manifest_bytes, scorer=scorer)
    recompute_code_switch(report, rows_bytes=rows_bytes,
                          manifest_bytes=manifest_bytes, scorer=scorer)
    require_operational_receipt(
        report, candidate_packet=candidate_packet,
        artifact_tree_sha256=artifact_tree_sha256)
    return states
