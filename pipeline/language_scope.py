"""Fail-closed B4 language scope derived from the approved decision record.

The source datasets remain intact.  This module governs only which languages
the current non-promotable B4 campaign may train on and which frozen sets may
participate in checkpoint selection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DECISION = (
    ROOT / "platform/decisions/B4-SCOPE-2026-001-language-deferral.json"
)


def _ordered_strings(value, name: str) -> tuple[str, ...]:
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)):
        raise SystemExit(
            f"REFUSING: language-scope {name} must be a non-empty unique "
            "string list")
    return tuple(value)


def load() -> tuple[dict, str]:
    raw = DECISION.read_bytes()
    doc = json.loads(raw)
    problems: list[str] = []
    if doc.get("record") != "B4-LANGUAGE-SCOPE-DECISION":
        problems.append("record type is not B4-LANGUAGE-SCOPE-DECISION")
    if doc.get("id") != "B4-SCOPE-2026-001":
        problems.append("decision id is not B4-SCOPE-2026-001")
    if doc.get("status") != "approved":
        problems.append(f"status is {doc.get('status')!r}, not 'approved'")
    if doc.get("scope", {}).get("promotable") is not False:
        problems.append("scope does not explicitly remain non-promotable")

    languages = doc.get("languages") or {}
    original_training = _ordered_strings(
        languages.get("original_training"), "original_training")
    active_training = _ordered_strings(
        languages.get("active_training"), "active_training")
    original_validation = _ordered_strings(
        languages.get("original_validation"), "original_validation")
    active_validation = _ordered_strings(
        languages.get("active_validation"), "active_validation")
    deferred = _ordered_strings(languages.get("deferred"), "deferred")

    if set(active_training) & set(deferred):
        problems.append("active training and deferred languages overlap")
    if set(active_validation) & set(deferred):
        problems.append("active validation and deferred languages overlap")
    if set(active_training) | set(deferred) != set(original_training):
        problems.append(
            "active training plus deferred languages do not exactly cover "
            "the original training scope")
    if set(active_validation) | set(deferred) != set(original_validation):
        problems.append(
            "active validation plus deferred languages do not exactly cover "
            "the original validation scope")
    if not set(active_validation) <= set(active_training):
        problems.append("an active validation language is absent from training")
    if deferred != ("amharic", "ewe"):
        problems.append(
            f"deferred languages are {deferred}, expected ('amharic', 'ewe')")

    binding = doc.get("training_mix_binding") or {}
    fingerprint = str(binding.get("dataset_fingerprint", ""))
    if (len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)):
        problems.append("dataset fingerprint is not 64 lowercase hex")
    for field in (
            "eligible_rows_after_policy_exclusions", "sampled_rows",
            "deferral_policy_rows_total", "deferral_policy_rows_applicable",
            "deferral_policy_rows_out_of_scope"):
        value = binding.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            problems.append(f"{field} is not a non-negative integer")
    if (binding.get("deferral_policy_rows_applicable", 0)
            + binding.get("deferral_policy_rows_out_of_scope", 0)
            != binding.get("deferral_policy_rows_total")):
        problems.append(
            "applicable and out-of-scope policy rows do not sum to total")

    controls = doc.get("hard_controls") or {}
    for field in (
            "gate_weakening_permitted", "reuse_old_adapter_permitted",
            "model_registration_permitted", "promotion_permitted"):
        if controls.get(field) is not False:
            problems.append(f"{field} must be false")
    for field in (
            "deferred_languages_must_be_absent_from_training",
            "deferred_languages_must_be_absent_from_validation_and_selection"):
        if controls.get(field) is not True:
            problems.append(f"{field} must be true")
    if problems:
        raise SystemExit(
            "REFUSING: B4 language-scope decision is unusable —\n  "
            + "\n  ".join(problems))
    return doc, hashlib.sha256(raw).hexdigest()


SCOPE, LANGUAGE_SCOPE_SHA256 = load()
ALL_TRAINING_LANGUAGES = tuple(SCOPE["languages"]["original_training"])
TRAINING_LANGUAGES = tuple(SCOPE["languages"]["active_training"])
ALL_VALIDATION_LANGUAGES = tuple(SCOPE["languages"]["original_validation"])
VALIDATION_LANGUAGES = tuple(SCOPE["languages"]["active_validation"])
DEFERRED_LANGUAGES = tuple(SCOPE["languages"]["deferred"])
EXPECTED_DATASET_FINGERPRINT = SCOPE["training_mix_binding"][
    "dataset_fingerprint"]
EXPECTED_ELIGIBLE_ROWS = SCOPE["training_mix_binding"][
    "eligible_rows_after_policy_exclusions"]
EXPECTED_SAMPLED_ROWS = SCOPE["training_mix_binding"]["sampled_rows"]
EXPECTED_POLICY_ROWS_TOTAL = SCOPE["training_mix_binding"][
    "deferral_policy_rows_total"]
EXPECTED_POLICY_ROWS_APPLICABLE = SCOPE["training_mix_binding"][
    "deferral_policy_rows_applicable"]
