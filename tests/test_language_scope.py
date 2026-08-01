"""The approved B4 scope is enforced without deleting deferred data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pipeline import budget, language_scope, orchestrate
from pipeline.validation_runner import FROZEN, frozen_validation


def test_scope_record_hash_and_language_partition_are_exact():
    doc, digest = language_scope.load()
    assert digest == hashlib.sha256(
        language_scope.DECISION.read_bytes()).hexdigest()
    assert digest == language_scope.LANGUAGE_SCOPE_SHA256
    assert doc["revision"] == 3
    assert language_scope.ADOPTION_KEY == (
        "curated/_versions/v2/ADOPTION-B4-SCOPED-NO-ACHOLI.json")
    assert language_scope.POLICY_PATH.name == (
        "DQ-2026-004-policy-deferral-scoped.json")
    assert hashlib.sha256(language_scope.POLICY_PATH.read_bytes()).hexdigest() == (
        language_scope.POLICY_SHA256)
    assert language_scope.DEFERRED_LANGUAGES == (
        "acholi", "amharic", "ewe")
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
        "akan", "fula", "lingala", "luganda", "oromo", "shona")


def test_scope_fingerprint_and_counts_are_pinned():
    assert language_scope.EXPECTED_DATASET_FINGERPRINT == (
        "a58b1d300980c467ff2aed3c21ada1d067d3ab4fc8d854b22ad9a43704afeb7d")
    assert language_scope.EXPECTED_ELIGIBLE_ROWS == 3321
    assert language_scope.EXPECTED_SAMPLED_ROWS == 3320
    assert language_scope.EXPECTED_POLICY_ROWS_TOTAL == 19
    assert language_scope.EXPECTED_POLICY_ROWS_APPLICABLE == 15
    assert budget.LEDGER_KEY == "candidates/budget/b4-scoped/ledger.json"


def test_scoped_policy_is_unreviewed_and_preserves_the_same_exclusion_set():
    scoped = json.loads(language_scope.POLICY_PATH.read_bytes())
    prior = json.loads((Path(__file__).resolve().parent.parent
                        / "platform/decisions/"
                        / "DQ-2026-003-policy-deferral-corrected.json").read_bytes())
    assert scoped["status"] == "approved"
    assert scoped["decision_type"] == "policy_deferral"
    assert scoped["human_review_performed"] is False
    assert scoped["counts"] == prior["counts"]
    projection = lambda doc: sorted(
        (row["audio_checksum_sha256"], row["trigger"], row["action"])
        for row in doc["exclusions"])
    assert projection(scoped) == projection(prior)


def test_smaller_language_decisions_keep_oromo_and_luganda_and_defer_rest():
    doc = json.loads(language_scope.DECISION.read_bytes())
    findings = doc["smaller_language_findings"]
    assert {language: findings[language]["decision"] for language in findings} == {
        "acholi": "defer", "oromo": "retain", "ewe": "defer",
        "luganda": "retain"}
    retained = doc["retained_scope_evidence"]
    assert retained["compatible_learning_rate"] == 1e-4
    assert retained["candidate_min_eos_rate"] == 1.0
    assert retained["candidate_max_cap_hit_rate"] == 0.0
