"""The approved B4 scope is enforced without deleting deferred data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pipeline import (budget, language_scope, orchestrate, scope_deviation,
                      stage_descriptor)
from pipeline.validation_runner import FROZEN, frozen_validation


def test_scope_record_hash_and_language_partition_are_exact():
    doc, digest = language_scope.load()
    assert digest == hashlib.sha256(
        language_scope.DECISION.read_bytes()).hexdigest()
    assert digest == language_scope.LANGUAGE_SCOPE_SHA256
    assert doc["id"] == "B4-B5-SCOPE-2026-003"
    assert doc["revision"] == 1
    assert language_scope.ADOPTION_KEY == (
        "curated/_versions/v2/ADOPTION-B4-SIMPLIFIED-8LANG-R2.json")
    assert language_scope.POLICY_PATH.name == (
        "DQ-2026-005-policy-with-lingala-holdout.json")
    assert hashlib.sha256(language_scope.POLICY_PATH.read_bytes()).hexdigest() == (
        language_scope.POLICY_SHA256)
    assert language_scope.DEFERRED_LANGUAGES == (
        "acholi", "akan", "amharic", "ewe", "fula", "shona",
        "lingala", "luganda", "oromo")
    assert language_scope.INACTIVE_NOT_EVALUATED_LANGUAGES == (
        "hausa", "igbo", "pidgin", "swahili", "yoruba")
    assert language_scope.TRAINING_LANGUAGES == ()
    assert language_scope.VALIDATION_LANGUAGES == ()
    assert not (set(language_scope.TRAINING_LANGUAGES)
                & set(language_scope.DEFERRED_LANGUAGES))
    assert not (set(language_scope.VALIDATION_LANGUAGES)
                & set(language_scope.DEFERRED_LANGUAGES))
    assert (set(language_scope.TRAINING_LANGUAGES)
            | set(language_scope.DEFERRED_LANGUAGES)
            | set(language_scope.INACTIVE_NOT_EVALUATED_LANGUAGES)
            == set(language_scope.ALL_TRAINING_LANGUAGES))
    assert doc["constraints"]["training_runs_authorized"] is False
    assert doc["constraints"]["aws_resource_creation_authorized"] is False


def test_frozen_evidence_is_preserved_but_current_validation_is_empty():
    frozen, _ = frozen_validation()
    assert tuple(frozen["sets"]) == language_scope.ALL_VALIDATION_LANGUAGES
    assert tuple(json.loads(FROZEN.read_bytes())["sets"]) == (
        "acholi", "akan", "amharic", "ewe", "fula", "lingala",
        "luganda", "oromo", "shona")
    assert orchestrate.VALIDATION_LANGUAGES == ()


@pytest.mark.parametrize("stage", [
    "base_and_preflight", "sweep", "final", "artifactize",
    "spot_checkpoint", "spot_resume",
])
def test_empty_active_scope_refuses_every_training_descriptor(stage):
    with pytest.raises(SystemExit, match=(
            r"active language scope is empty.*active_training=\[\].*"
            r"active_validation=\[\]")):
        stage_descriptor.build(stage=stage)


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
    assert decision["revision"] == 3
    termination = decision["termination_gate_deviation"]
    assert termination["applies_symmetrically_to"] == [
        "lingala", "luganda", "oromo"]
    assert termination[
        "max_unique_failures_per_language_per_checkpoint"] == 1
    assert termination[
        "same_checksum_may_fail_at_multiple_final_checkpoints"] is False
    assert termination["holdout_max_unique_failures"] == 0
    diagnostic = decision["servable_artifact"]["conversion_diagnostic"]
    assert diagnostic["arms_in_fixed_order"] == [
        "merged_pytorch_float16",
        "ctranslate2_float16",
        "ctranslate2_int8_float16",
    ]
    assert diagnostic["selection_must_not_read_holdout"] is True
    assert diagnostic["holdout_base_must_use_same_ctranslate2_precision"] is True
    assert diagnostic["precision_preference"] == ["int8_float16", "float16"]
    assert diagnostic["minimum_relative_wer_gain"] == 0.15


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


def test_historical_language_findings_remain_immutable_and_readable():
    doc = json.loads(language_scope.HISTORICAL_DECISION.read_bytes())
    findings = doc["smaller_language_findings"]
    assert {language: findings[language]["decision"] for language in findings} == {
        "acholi": "defer", "oromo": "retain", "ewe": "defer",
        "luganda": "retain", "fula": "defer", "akan": "defer",
        "shona": "defer"}
    retained = doc["retained_scope_evidence"]
    assert retained["compatible_learning_rate"] == 1e-4
    assert retained["candidate_min_eos_rate"] == 1.0
    assert retained["candidate_max_cap_hit_rate"] == 0.0


def test_new_decision_preserves_yardsticks_factory_and_open_resource_gap():
    doc = json.loads(language_scope.DECISION.read_bytes())
    yardsticks = doc["preserved_quality_yardsticks"]
    assert yardsticks["lingala_untouched_holdout"] == {
        "rows": 77,
        "candidate_wer": 0.5996,
        "same_precision_base_wer": 0.7558,
        "relative_gain": 0.2067,
        "evidence": {
            "path": "platform/evidence/CAMPAIGNRUN-2026-013-passed.json",
            "sha256": "e376bba6e3944f9cea0a3c4a81d162ff4e308d4836438b316dfebc36837beae8",
        },
    }
    assert yardsticks["selection_set_ctranslate2_float16"] == {
        "lingala_wer": 0.7284,
        "luganda_wer": 0.7002,
        "oromo_wer": 0.6997,
        "evidence": {
            "path": "platform/evidence/CAMPAIGNRUN-2026-013-passed.json",
            "sha256": "e376bba6e3944f9cea0a3c4a81d162ff4e308d4836438b316dfebc36837beae8",
        },
    }
    assert yardsticks["zero_shot_base"] == {
        "lingala_wer": 0.9207,
        "luganda_wer": 1.0659,
        "oromo_wer": 1.1749,
        "evidence": {
            "path": "platform/evidence/CAMPAIGNRUN-2026-012-failed.json",
            "sha256": "cfd64c9c6c7138a42830d42f97cd3e6a48bd9945619e40a15bd75f7415854102",
        },
    }
    capabilities = set(doc["factory_capabilities"]["capabilities"])
    assert capabilities == {
        "LoRA training", "interleaved checkpoint gating",
        "best-passing-checkpoint selection", "exact Spot checkpoint resume",
        "adapter merge", "CTranslate2 conversion",
        "post-selection holdout discipline", "budget control",
        "immutable evidence",
    }
    serving = doc["serving_resource_deviation"]
    assert serving["float16_precision_deviation"] == "OPEN_NOT_WITHDRAWN"
    assert serving["serving_resource_record"] == "OPEN_NOT_WITHDRAWN"
    assert serving["peak_l4_gpu_memory"] == "NOT_MEASURED"


def test_scope_deferral_does_not_change_any_locked_historical_hash():
    doc = json.loads(language_scope.DECISION.read_bytes())
    for relative, expected in doc["historical_hash_lock"].items():
        assert hashlib.sha256(
            (Path(__file__).resolve().parent.parent / relative).read_bytes()
        ).hexdigest() == expected
    mapping = (Path(__file__).resolve().parent.parent
               / "platform/evidence/GIT-HISTORY-COMMIT-MAP-2026-001.txt")
    assert ("e03e7830d84ba422d8c418c61a0c0a0259d88339 "
            "68a09590611e6f768ee4dd0c1b185456132eacdc") in mapping.read_text()
