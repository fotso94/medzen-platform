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

from scripts.asr_base_model_pilot_integrity import (
    ATTEMPT_24_EXECUTOR_MODULE_PATHS,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002W.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002W-attempt-24.md"
RISK = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-019.json"
RECONCILIATION = ROOT / "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-009.json"
AUDIT = ROOT / "platform/evidence/ASR-BASE-MODEL-WAITER-FINALIZER-AUDIT-2026-001.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002V-ATTEMPT-23-DNS-POLL-CONTROL-FLOW-REFUSAL.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002W-COLD/cold-rehearsal.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt24_is_one_fresh_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "attempts_1_through_23_reuse_permitted": False,
        "authorized_numbers": [24],
        "cost_ceiling_usd": 10,
        "maximum": 1,
        "maximum_gpu_nodes": 1,
        "non_transferable": True,
        "seconds_each": 10800,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002W only" in text
    assert sha(RISK) in text


def test_all_attempt24_modules_are_bound_to_the_exact_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_24_EXECUTOR_MODULE_PATHS)
    assert len(value["executor_modules"]) == len(ATTEMPT_24_EXECUTOR_MODULE_PATHS) == 33
    assert "scripts/asr_base_model_pod_lifecycle.py" in value["executor_modules"]
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_lifecycle_policy_is_shared_bounded_and_nonmasking() -> None:
    policy = bound()["pod_lifecycle_policy"]
    assert policy["shared_module"] == "scripts/asr_base_model_pod_lifecycle.py"
    assert policy["stage_local_delete_mode"] == "NONBLOCKING_THEN_STABLE_ABSENCE"
    assert policy["stable_absence_observations"] == 2
    assert policy["primary_exception_authoritative"] is True
    assert policy["secondary_cleanup_failure_persisted_separately"] is True
    assert policy["undefined_sleep_member_permitted"] is False
    prepull = policy["image_prepull"]
    assert prepull["required_before_dns_control"] is True
    assert prepull["exact_linux_amd64_digest"] == bound()["image"]["linux_amd64_digest"]
    assert prepull["terminal_timeout_seconds"] == 1200
    assert prepull["progress_stall_timeout_seconds"] == 600
    assert prepull["stable_node_inventory_observations"] == 2


def test_systemic_audit_is_hash_bound_and_complete() -> None:
    binding = bound()["waiter_finalizer_audit"]
    assert binding["sha256"] == sha(AUDIT)
    audit = json.loads(AUDIT.read_bytes())
    assert audit["status"] == "PASS_SYSTEMIC_WAITER_FINALIZER_AUDIT"
    assert audit["waiter_site_count"] == 15
    assert audit["controls"]["stage_local_blocking_pod_deletes"] == 0
    assert audit["controls"]["undefined_sleep_calls"] == 0
    assert audit["controls"]["rehearsal_waiters_with_instant_terminal_only"] == 0


def test_attempt24_machine_plan_has_no_permanent_or_image_mutation() -> None:
    value = bound()
    result = validate_plan(exact_plan(value, 24), value, 24)
    assert result == {
        "status": "PASS_EXACT_EXECUTION_PLAN",
        "attempt": 24,
        "permanent_create_only": 0,
        "permanent_bounded_update": 0,
        "temporary_create_then_delete": 18,
        "bounded_capacity_change": 1,
    }
    assert value["image"]["publication_required"] is False
    assert value["security_gate"]["registry_scanning_mutation_permitted"] is False


def test_cost_registry_019_is_conservative_and_request_fits() -> None:
    value = bound()
    assert value["cost_registry"]["sha256"] == sha(COST)
    registry = json.loads(COST.read_bytes())
    assert registry["reconciliation"]["sha256"] == sha(RECONCILIATION)
    summary = registry["guardrail_summary"]
    committed = Decimal(str(summary["recognized_committed_guardrail_usd"]))
    reserved = Decimal(str(summary["active_reservations_usd"]))
    ceiling = Decimal(str(summary["aggregate_ceiling_usd"]))
    assert ceiling - committed - reserved == Decimal("95.5713935784")
    assert ceiling - committed - reserved - Decimal("10") == Decimal(
        "85.5713935784"
    )


def test_write_once_attempt23_history_is_bound() -> None:
    history = bound()["write_once_history"]
    expected = {
        "attempt_23_packet": "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002V-attempt-23.md",
        "attempt_23_authorization": "platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-002V.json",
        "attempt_23_bindings": "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002V.json",
        "attempt_23_dry_validation": "platform/evidence/ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-2026-002V.json",
        "attempt_23_refusal": "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002V-ATTEMPT-23-DNS-POLL-CONTROL-FLOW-REFUSAL.json",
        "attempt_23_cost_reconciliation": "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-009.json",
        "attempt_23_cost_registry": "platform/finance/COST-REGISTRY-2026-019.json",
    }
    for key, relative in expected.items():
        assert history[key]["sha256"] == sha(ROOT / relative)
    assert history["attempt_23_live_receipts"]["commit"] == (
        "2efdf6bf9ad9ff10b6ee76c64b9c060fdfc8ad42"
    )
    assert history["attempt_23_refusal"]["sha256"] == sha(REFUSAL)


def test_receipt_last_rehearsal_covers_lifecycle_fidelity() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["executor_module_integrity"]["module_count"] == 33
    fidelity = value["bounded_waiter_rehearsal_fidelity"]
    assert fidelity["status"] == (
        "PASS_ALL_BOUNDED_WAITER_FAKES_EXERCISE_NONTERMINAL_STATE"
    )
    assert fidelity["site_count"] == 13
    assert fidelity["primary_exception_preservation"]["reason_code"] == (
        "DNS_RESOLVED_IP_OUTSIDE_ENDPOINT_ALLOWLIST"
    )
    assert fidelity["primary_exception_preservation"][
        "secondary_cleanup_diagnostic"
    ]["status"] == (
        "PRIMARY_EXCEPTION_RETAINED_WITH_SECONDARY_CLEANUP_DIAGNOSTIC"
    )
    clean = value["scenarios"]["clean_pass"]["stage_pod_lifecycle"]
    assert clean["terminal_observation_sequences"]["asr-eval-image-prepull"][:2] == [
        "Pending",
        "Succeeded",
    ]
    assert clean["node_image_inventory_sequence"][:3] == [
        "ABSENT",
        "PRESENT",
        "PRESENT",
    ]
    assert value["scenarios"]["image_prepull_stall"]["failure_reason_code"] == (
        "IMAGE_PREPULL_PROGRESS_STALLED"
    )
    assert all(item["zero_state"] is True for item in value["scenarios"].values())
