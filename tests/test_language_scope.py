"""The approved B4 scope is enforced without deleting deferred data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pipeline import budget, language_scope, orchestrate, scope_deviation
from pipeline.validation_runner import FROZEN, frozen_validation


def test_scope_record_hash_and_language_partition_are_exact():
    doc, digest = language_scope.load()
    assert digest == hashlib.sha256(
        language_scope.DECISION.read_bytes()).hexdigest()
    assert digest == language_scope.LANGUAGE_SCOPE_SHA256
    assert doc["revision"] == 6
    assert language_scope.ADOPTION_KEY == (
        "curated/_versions/v2/ADOPTION-B4-SIMPLIFIED-8LANG.json")
    assert language_scope.POLICY_PATH.name == (
        "DQ-2026-005-policy-with-lingala-holdout.json")
    assert hashlib.sha256(language_scope.POLICY_PATH.read_bytes()).hexdigest() == (
        language_scope.POLICY_SHA256)
    assert language_scope.DEFERRED_LANGUAGES == (
        "acholi", "akan", "amharic", "ewe", "fula", "shona")
    assert not (set(language_scope.TRAINING_LANGUAGES)
                & set(language_scope.DEFERRED_LANGUAGES))
    assert not (set(language_scope.VALIDATION_LANGUAGES)
                & set(language_scope.DEFERRED_LANGUAGES))
    assert (set(language_scope.TRAINING_LANGUAGES)
            | set(language_scope.DEFERRED_LANGUAGES)
            == set(language_scope.ALL_TRAINING_LANGUAGES))
    assert doc["hard_controls"]["reuse_old_adapter_permitted"] is False


def test_frozen_evidence_is_preserved_but_campaign_uses_only_active_sets():
    frozen, _ = frozen_validation()
    assert tuple(frozen["sets"]) == language_scope.ALL_VALIDATION_LANGUAGES
    assert tuple(json.loads(FROZEN.read_bytes())["sets"]) == (
        "acholi", "akan", "amharic", "ewe", "fula", "lingala",
        "luganda", "oromo", "shona")
    assert orchestrate.VALIDATION_LANGUAGES == (
        "lingala", "luganda", "oromo")


def test_scope_fingerprint_and_counts_are_pinned():
    assert language_scope.EXPECTED_DATASET_FINGERPRINT == (
        "eed56700ceadd37ac1513e49cd1798a6cddc20b46c90d2a9b2ed6b439685769e")
    assert language_scope.EXPECTED_ELIGIBLE_ROWS == 2223
    assert language_scope.EXPECTED_SAMPLED_ROWS == 2221
    assert language_scope.EXPECTED_POLICY_ROWS_TOTAL == 96
    assert language_scope.EXPECTED_POLICY_ROWS_APPLICABLE == 86
    assert budget.LEDGER_KEY == "candidates/budget/b4-aggregate/ledger.json"
    assert budget.CEILING_USD == 100.0
    assert budget.HISTORICAL_SPEND_USD == 16.8738


def test_owner_scope_deviation_is_executable_and_keeps_lingala():
    decision, decision_sha, gates, gates_sha = scope_deviation.load()
    assert decision_sha == scope_deviation.DECISION_SHA256
    assert gates_sha == scope_deviation.A5_GATES_SHA256
    assert tuple(decision["language_scope"]["active_training"]) == (
        "hausa", "igbo", "lingala", "luganda", "oromo",
        "pidgin", "swahili", "yoruba")
    assert decision["data_scope_deviations"]["code_switch"]["decision"] == (
        "NOT_EVALUATED")
    assert decision["data_scope_deviations"]["english_french_replay"][
        "decision"] == "NOT_EVALUATED"
    assert len(gates["gates"]) == 25
    assert all(row["status"] != "PASSED" for row in gates["gates"])


def test_scoped_policy_is_unreviewed_and_adds_only_the_holdout():
    scoped = json.loads(language_scope.POLICY_PATH.read_bytes())
    prior = json.loads((Path(__file__).resolve().parent.parent
                        / "platform/decisions/"
                        / "DQ-2026-004-policy-deferral-scoped.json").read_bytes())
    assert scoped["status"] == "approved"
    assert scoped["decision_type"] == "policy_deferral"
    assert scoped["human_review_performed"] is False
    assert scoped["counts"]["total"] == prior["counts"]["total"] + 77
    assert scoped["inherits_policy"]["sha256"] == hashlib.sha256(
        (Path(__file__).resolve().parent.parent / scoped["inherits_policy"]["path"])
        .read_bytes()).hexdigest()
    assert scoped["holdout_exclusion"]["selection_use_forbidden"] is True


def test_language_decisions_keep_oromo_and_luganda_and_defer_blockers():
    doc = json.loads(language_scope.DECISION.read_bytes())
    findings = doc["smaller_language_findings"]
    assert {language: findings[language]["decision"] for language in findings} == {
        "acholi": "defer", "oromo": "retain", "ewe": "defer",
        "luganda": "retain", "fula": "defer", "akan": "defer",
        "shona": "defer"}
    retained = doc["retained_scope_evidence"]
    assert retained["compatible_learning_rate"] == 1e-4
    assert retained["candidate_min_eos_rate"] == 1.0
    assert retained["candidate_max_cap_hit_rate"] == 0.0
