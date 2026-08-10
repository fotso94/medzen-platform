from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / (
    "platform/evidence/"
    "B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json"
)


def sha256(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_012a_execution_evidence_is_complete_and_receipts_are_immutable() -> None:
    value = json.loads(EVIDENCE.read_bytes())
    assert value["status"] == "VERIFIED_COMPLETE"
    assert value["outcome"] == "PASS_SECRET_ROTATION_ONLY"
    assert value["project_state"]["attempt_4_authorized"] is False
    assert value["terraform_execution"]["stage_a"]["residual_outcome"] == "NO_CHANGES"
    assert value["terraform_execution"]["stage_b"]["residual_outcome"] == "NO_CHANGES"
    for receipt in value["stage_receipts"]:
        assert sha256(receipt["path"]) == receipt["sha256"]


def test_012a_rotation_binds_only_non_secret_identifiers() -> None:
    value = json.loads(EVIDENCE.read_bytes())
    rotation = value["rotation_and_verification"]
    assert rotation["new_secret_version_id"] == "d09d567e-9bde-482a-b95a-3cab990a1006"
    assert rotation["new_secret_version_stages"] == ["AWSCURRENT"]
    assert rotation["historical_secret_version_stages"] == []
    assert rotation["bearer_token_sha256"] == (
        "3a30b00fc96111490c2b471eec5eebe1c9d26bf991508428cf2f5511e306b84a"
    )
    assert rotation["plaintext_recorded"] is False
    assert rotation["operator_get_secret_value"] == "EXPLICITLY_DENIED_AS_REQUIRED"
    raw = EVIDENCE.read_text()
    for prohibited in ("SecretString", "Bearer "):
        assert prohibited not in raw


def test_012a_did_not_open_the_integration_window() -> None:
    value = json.loads(EVIDENCE.read_bytes())
    assert value["post_execution_zero_state"]["cpu_desired"] == 0
    assert value["post_execution_zero_state"]["gpu_desired"] == 0
    assert value["explicit_non_events"]["workers_started"] == 0
    assert value["explicit_non_events"]["attempt_4_started"] == 0
    assert value["cost"]["compute_cost_usd"] == 0.0
