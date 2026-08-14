from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_integrity import (
    ATTEMPT_18_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002Q.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002Q-attempt-18.md"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002Q-COLD/cold-rehearsal.json"
QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-ENDPOINT-POLICY-QUALIFICATION-2026-001.json"
COST_RECONCILIATION = ROOT / "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-003.json"
COST_REGISTRY = ROOT / "platform/finance/COST-REGISTRY-2026-013.json"
ATTEMPT_17 = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002P-ATTEMPT-17-NODE-STAGING-REFUSAL.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt18_is_one_fresh_nontransferable_request() -> None:
    value = bound()
    assert value["attempts"] == {
        "authorized_numbers": [18],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10,
        "attempts_1_through_17_reuse_permitted": False,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002Q only" in text


def test_all_attempt18_executor_modules_are_bound_to_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_18_EXECUTOR_MODULE_PATHS)
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected
    assert len(value["executor_modules"]) == len(ATTEMPT_18_EXECUTOR_MODULE_PATHS) == 27


def test_attempt18_plan_has_no_permanent_mutation() -> None:
    value = bound()
    plan = exact_plan(value, 18)
    result = validate_plan(plan, value, 18)
    assert plan["permanent_create_only"] == []
    assert plan["permanent_bounded_update"] == []
    assert result["temporary_create_then_delete"] == 18
    assert result["bounded_capacity_change"] == 1


def test_packet_binds_policy_qualification_cost_and_history() -> None:
    text = PACKET.read_text()
    for path in (BINDINGS, QUALIFICATION, COST_RECONCILIATION, COST_REGISTRY, ATTEMPT_17):
        if path == BINDINGS:
            continue  # Sealed after the receipt-last rehearsal.
        assert sha(path) in text
    value = bound()
    assert value["endpoint_policy_derivation"]["qualification_sha256"] == sha(
        QUALIFICATION
    )
    assert value["cost_registry"]["sha256"] == sha(COST_REGISTRY)
    assert value["write_once_history"]["attempt_17_cost_reconciliation"]["sha256"] == sha(
        COST_RECONCILIATION
    )
    assert value["write_once_history"]["attempt_17_refusal"]["sha256"] == sha(
        ATTEMPT_17
    )


def test_policy_derivation_is_exact_and_has_no_other_version_variant() -> None:
    policy = bound()["endpoint_policy_derivation"]
    assert policy == {
        "qualification_path": "platform/evidence/ASR-BASE-MODEL-ENDPOINT-POLICY-QUALIFICATION-2026-001.json",
        "qualification_sha256": sha(QUALIFICATION),
        "inventory_total_calls": 35,
        "s3_calls": 13,
        "s3_versioned_calls": 8,
        "s3_unversioned_calls": 5,
        "ecr_calls": 22,
        "required_s3_actions": ["s3:GetObject", "s3:GetObjectVersion"],
        "other_s3_version_variant_actions_required": [],
        "exact_prefix_scope_only": True,
        "hand_written_action_lists_permitted": False,
        "observed_live_call_cross_check_required": True,
    }


def test_final_cold_rehearsal_uses_committed_bindings_and_policy_refusals() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["executor_module_integrity"]["module_count"] == 27
    policy = value["endpoint_policy_inventory"]
    assert policy["hand_written_action_lists_permitted"] is False
    assert policy["static_rehearsal"]["inventory_call_count"] == 35
    assert policy["static_rehearsal"]["required_actions"] == [
        "s3:GetObject",
        "s3:GetObjectVersion",
    ]
    assert policy["static_rehearsal"]["refusals"] == {
        "missing_get_object_version": "ENDPOINT_POLICY_CALL_UNCOVERED",
        "observed_request_inventory_drift": "ENDPOINT_OBSERVED_S3_CALLS_DIFFER",
        "version_flag_action_drift": "ENDPOINT_RECORDED_ACTION_DIFFERS",
    }


def test_historical_attempt17_record_is_unchanged() -> None:
    assert sha(ATTEMPT_17) == (
        "7c13b45fa917ed0db79114ef15518959e7e8a58cb11b7ac614a3bf2d2ddbe102"
    )
