from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_gpu_storage import validate_gpu_storage_prerequisite
from scripts.asr_base_model_pilot_integrity import (
    ATTEMPT_20_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002S.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002S-attempt-20.md"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002S-COLD/cold-rehearsal.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-015.json"
RECONCILIATION = ROOT / "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-005.json"
ATTEMPT_19 = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002R-ATTEMPT-19-EPHEMERAL-STORAGE-REFUSAL.json"
STORAGE_APPLY = ROOT / "platform/evidence/ASR-BASE-MODEL-GPU-STORAGE-APPLY-2026-001.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt20_is_one_fresh_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "attempts_1_through_19_reuse_permitted": False,
        "authorized_numbers": [20],
        "cost_ceiling_usd": 10,
        "maximum": 1,
        "maximum_gpu_nodes": 1,
        "non_transferable": True,
        "seconds_each": 10800,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002S only" in text


def test_all_attempt20_modules_are_bound_to_exact_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_20_EXECUTOR_MODULE_PATHS)
    result = validate_executor_module_bindings(ROOT, value["executor_modules"])
    assert result["module_count"] == len(ATTEMPT_20_EXECUTOR_MODULE_PATHS) == 29
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_current_asg_and_40_gib_are_exactly_bound_in_plan() -> None:
    value = bound()
    assert value["aws"]["gpu_root_volume_gib"] == 40
    assert value["aws"]["gpu_asg_name"] == (
        "eks-gpu-14cfff59-42c6-46ad-8d59-37cd02daefa8"
    )
    plan = exact_plan(value, 20)
    result = validate_plan(plan, value, 20)
    assert result == {
        "status": "PASS_EXACT_EXECUTION_PLAN",
        "attempt": 20,
        "permanent_create_only": 0,
        "permanent_bounded_update": 0,
        "temporary_create_then_delete": 18,
        "bounded_capacity_change": 1,
    }
    assert value["aws"]["gpu_asg_name"] in plan["bounded_capacity_change"][0]


def test_bound_capacity_gate_passes_exact_recorded_live_state() -> None:
    value = bound()
    fixture = json.loads(
        (ROOT / value["gpu_storage_policy"]["live_fixture"]["path"]).read_bytes()
    )
    result = validate_gpu_storage_prerequisite(
        ROOT,
        value["gpu_storage_policy"],
        fixture["nodegroup"],
        expected_image=value["image"],
    )
    assert result["status"] == "PASS_PRE_ENVELOPE_GPU_STORAGE"
    assert result["calculated_minimum_gib"] == 29
    assert result["operational_floor_gib"] == 40
    assert result["attempt_number_consumed"] is False


def test_write_once_storage_and_attempt19_evidence_is_hash_bound() -> None:
    value = bound()
    history = value["write_once_history"]
    assert history["attempt_19_refusal"]["sha256"] == sha(ATTEMPT_19)
    assert history["gpu_storage_apply_evidence"]["sha256"] == sha(STORAGE_APPLY)
    assert value["cost_registry"]["sha256"] == sha(COST)
    assert history["attempt_19_cost_reconciliation"]["sha256"] == sha(
        RECONCILIATION
    )


def test_cost_registry_015_arithmetic_and_attempt20_headroom() -> None:
    summary = json.loads(COST.read_bytes())["guardrail_summary"]
    committed = Decimal(str(summary["recognized_committed_guardrail_usd"]))
    reserved = Decimal(str(summary["active_reservations_usd"]))
    ceiling = Decimal(str(summary["aggregate_ceiling_usd"]))
    assert committed + reserved == Decimal(
        str(summary["committed_plus_reserved_usd"])
    )
    assert ceiling - committed - reserved == Decimal(
        str(summary["guardrail_headroom_after_reservations_usd"])
    )
    assert ceiling - committed - Decimal("10") == Decimal("125.5713935784")


def test_receipt_last_rehearsal_proves_both_gpu_storage_outcomes() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["bindings_source"]["sha256"] == sha(BINDINGS)
    gate = receipt["pre_envelope_gpu_storage_gate"]
    assert gate["sufficient_capacity"]["status"] == (
        "PASS_PRE_ENVELOPE_GPU_STORAGE"
    )
    assert gate["sufficient_capacity"]["live_root_volume_gib"] == 40
    assert gate["insufficient_capacity"]["reason_code"] == (
        "GPU_ROOT_VOLUME_BELOW_OPERATIONAL_FLOOR"
    )
    assert gate["insufficient_capacity"]["attempt_envelope_created"] is False
    assert gate["insufficient_capacity"]["attempt_number_consumed"] is False
    assert gate["insufficient_capacity"]["aws_mutations"] == 0
    assert all(item["zero_state"] is True for item in receipt["scenarios"].values())
