"""Fail-closed current language scope derived from the owner decision.

Historical B4 data, evidence and factory bindings remain intact.  The current
schedule has no active training or validation language, so training-stage
descriptor construction must refuse rather than treating an empty list as an
unrestricted wildcard.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DECISION = (
    ROOT / "platform/decisions/B4-B5-SCOPE-2026-003-language-deferral.json"
)
HISTORICAL_DECISION = (
    ROOT / "platform/decisions/B4-SCOPE-2026-001-language-deferral.json"
)
EXPECTED_HISTORICAL_DECISION_SHA256 = (
    "5484fab6c73be03d7cd33b55417eb03621dcd342ad4a086fd1f90ef5d62b0657"
)
EXPECTED_POLICY_RELATIVE = (
    "platform/decisions/DQ-2026-005-policy-with-lingala-holdout.json"
)
EXPECTED_ADOPTION_KEY = (
    "curated/_versions/v2/ADOPTION-B4-SIMPLIFIED-8LANG-R2.json"
)


def _ordered_strings(value, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(item, str) or not item for item in value)
            or len(set(value)) != len(value)):
        raise SystemExit(
            f"REFUSING: language-scope {name} must be a "
            f"{'possibly-empty' if allow_empty else 'non-empty'} unique "
            "string list")
    return tuple(value)


def load() -> tuple[dict, str]:
    raw = DECISION.read_bytes()
    doc = json.loads(raw)
    problems: list[str] = []
    if doc.get("record") != "B4-B5-LANGUAGE-SCOPE-DEFERRAL":
        problems.append("record type is not B4-B5-LANGUAGE-SCOPE-DEFERRAL")
    if doc.get("id") != "B4-B5-SCOPE-2026-003":
        problems.append("decision id is not B4-B5-SCOPE-2026-003")
    if doc.get("revision") != 1:
        problems.append("decision revision is not 1")
    if doc.get("status") != "owner_approved":
        problems.append(
            f"status is {doc.get('status')!r}, not 'owner_approved'")

    languages = doc.get("language_scope") or {}
    original_training = _ordered_strings(
        languages.get("original_training"), "original_training")
    active_training = _ordered_strings(
        languages.get("active_training"), "active_training", allow_empty=True)
    original_validation = _ordered_strings(
        languages.get("original_validation"), "original_validation")
    active_validation = _ordered_strings(
        languages.get("active_validation"), "active_validation", allow_empty=True)
    deferred = _ordered_strings(languages.get("deferred"), "deferred")
    inactive_not_evaluated = _ordered_strings(
        languages.get("inactive_not_evaluated"),
        "inactive_not_evaluated")

    if set(active_training) & set(deferred):
        problems.append("active training and deferred languages overlap")
    if set(active_training) & set(inactive_not_evaluated):
        problems.append(
            "active training and inactive-not-evaluated languages overlap")
    if set(deferred) & set(inactive_not_evaluated):
        problems.append(
            "deferred and inactive-not-evaluated languages overlap")
    if set(active_validation) & set(deferred):
        problems.append("active validation and deferred languages overlap")
    if (set(active_training) | set(deferred) | set(inactive_not_evaluated)
            != set(original_training)):
        problems.append(
            "active, deferred and inactive-not-evaluated languages do not "
            "exactly cover the original training scope")
    if set(active_validation) | set(deferred) != set(original_validation):
        problems.append(
            "active validation plus deferred languages do not exactly cover "
            "the original validation scope")
    if not set(active_validation) <= set(active_training):
        problems.append("an active validation language is absent from training")
    expected_deferred = (
        "acholi", "akan", "amharic", "ewe", "fula", "shona",
        "lingala", "luganda", "oromo")
    if deferred != expected_deferred:
        problems.append(
            "deferred languages are "
            f"{deferred}, expected {expected_deferred}")
    expected_inactive = ("hausa", "igbo", "pidgin", "swahili", "yoruba")
    if inactive_not_evaluated != expected_inactive:
        problems.append(
            "inactive-not-evaluated languages are "
            f"{inactive_not_evaluated}, expected {expected_inactive}")
    if active_training:
        problems.append("active training scope is not explicitly empty")
    if active_validation:
        problems.append("active validation scope is not explicitly empty")

    per_language = doc.get("per_language_deferral") or {}
    if tuple(per_language) != expected_deferred:
        problems.append("per-language deferral entries do not match scope order")
    for language in expected_deferred:
        entry = per_language.get(language) or {}
        if not entry.get("reason") or not entry.get("reactivation_condition"):
            problems.append(
                f"{language} lacks a deferral reason or reactivation condition")

    lock = doc.get("historical_hash_lock") or {}
    for relative, expected_sha in lock.items():
        try:
            actual_sha = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        except OSError as exc:
            problems.append(f"historical hash-lock file is unreadable: {exc}")
        else:
            if actual_sha != expected_sha:
                problems.append(
                    f"historical hash lock differs for {relative}")

    binding_rule = doc.get("executable_binding") or {}
    if binding_rule.get("source") != DECISION.relative_to(ROOT).as_posix():
        problems.append("executable binding does not name this scope record")
    if tuple(binding_rule.get("active_training_must_equal") or ()) != ():
        problems.append("executable binding does not require empty training")
    if tuple(binding_rule.get("active_validation_must_equal") or ()) != ():
        problems.append("executable binding does not require empty validation")

    constraints = doc.get("constraints") or {}
    for field in (
            "training_runs_authorized", "aws_resource_creation_authorized",
            "aws_packet_2026_001b_approved",
            "historical_gate_report_regeneration_authorized",
            "model_registration_authorized",
            "approved_artifact_publication_authorized",
            "production_ssm_change_authorized", "deployment_authorized"):
        if constraints.get(field) is not False:
            problems.append(f"{field} must be false")

    historical_raw = HISTORICAL_DECISION.read_bytes()
    historical_sha = hashlib.sha256(historical_raw).hexdigest()
    if historical_sha != EXPECTED_HISTORICAL_DECISION_SHA256:
        problems.append("historical B4 language-scope bytes changed")
    historical = json.loads(historical_raw)

    binding = historical.get("training_mix_binding") or {}
    if binding.get("adoption_key") != EXPECTED_ADOPTION_KEY:
        problems.append("training mix does not bind the scoped adoption key")
    if binding.get("deferral_policy") != EXPECTED_POLICY_RELATIVE:
        problems.append("training mix does not bind the scoped deferral policy")
    policy_path = ROOT / EXPECTED_POLICY_RELATIVE
    try:
        policy_sha = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    except OSError as exc:
        problems.append(f"scoped deferral policy is unreadable: {exc}")
    else:
        if binding.get("deferral_policy_sha256") != policy_sha:
            problems.append("scoped deferral policy bytes differ from the decision")
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

    controls = historical.get("hard_controls") or {}
    for field in (
            "gate_weakening_permitted", "reuse_old_adapter_permitted",
            "model_registration_permitted", "promotion_permitted"):
        if controls.get(field) is not False:
            problems.append(f"{field} must be false")
    for field in (
            "deferred_languages_must_be_absent_from_training",
            "deferred_languages_must_be_absent_from_validation_and_selection",
            "post_selection_holdout_must_not_select_checkpoint"):
        if controls.get(field) is not True:
            problems.append(f"{field} must be true")
    if problems:
        raise SystemExit(
            "REFUSING: B4 language-scope decision is unusable —\n  "
            + "\n  ".join(problems))
    return doc, hashlib.sha256(raw).hexdigest()


SCOPE, LANGUAGE_SCOPE_SHA256 = load()
HISTORICAL_SCOPE = json.loads(HISTORICAL_DECISION.read_bytes())
HISTORICAL_TRAINING_MIX = HISTORICAL_SCOPE["training_mix_binding"]
ALL_TRAINING_LANGUAGES = tuple(SCOPE["language_scope"]["original_training"])
TRAINING_LANGUAGES = tuple(SCOPE["language_scope"]["active_training"])
ALL_VALIDATION_LANGUAGES = tuple(SCOPE["language_scope"]["original_validation"])
VALIDATION_LANGUAGES = tuple(SCOPE["language_scope"]["active_validation"])
DEFERRED_LANGUAGES = tuple(SCOPE["language_scope"]["deferred"])
INACTIVE_NOT_EVALUATED_LANGUAGES = tuple(
    SCOPE["language_scope"]["inactive_not_evaluated"])
EXPECTED_DATASET_FINGERPRINT = HISTORICAL_TRAINING_MIX[
    "dataset_fingerprint"]
EXPECTED_ELIGIBLE_ROWS = HISTORICAL_TRAINING_MIX[
    "eligible_rows_after_policy_exclusions"]
EXPECTED_SAMPLED_ROWS = HISTORICAL_TRAINING_MIX["sampled_rows"]
EXPECTED_POLICY_ROWS_TOTAL = HISTORICAL_TRAINING_MIX[
    "deferral_policy_rows_total"]
EXPECTED_POLICY_ROWS_APPLICABLE = HISTORICAL_TRAINING_MIX[
    "deferral_policy_rows_applicable"]
HOLDOUT = HISTORICAL_TRAINING_MIX["post_selection_holdout"]
POLICY_PATH = ROOT / HISTORICAL_TRAINING_MIX["deferral_policy"]
POLICY_SHA256 = HISTORICAL_TRAINING_MIX["deferral_policy_sha256"]
ADOPTION_KEY = HISTORICAL_TRAINING_MIX["adoption_key"]
