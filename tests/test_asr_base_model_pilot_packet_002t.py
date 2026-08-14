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
    ATTEMPT_21_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002T.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002T-attempt-21.md"
AUDIT = ROOT / "platform/evidence/ASR-BASE-MODEL-ASYNC-OBSERVATION-AUDIT-2026-001.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002S-ATTEMPT-20-NETWORK-PROBE-RECEIPT-RACE-REFUSAL.json"
RECONCILIATION = ROOT / "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-006.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-016.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt21_is_one_fresh_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "attempts_1_through_20_reuse_permitted": False,
        "authorized_numbers": [21],
        "cost_ceiling_usd": 10,
        "maximum": 1,
        "maximum_gpu_nodes": 1,
        "non_transferable": True,
        "seconds_each": 10800,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002T only" in text


def test_all_attempt21_modules_are_bound_to_exact_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_21_EXECUTOR_MODULE_PATHS)
    result = validate_executor_module_bindings(ROOT, value["executor_modules"])
    assert result["module_count"] == len(ATTEMPT_21_EXECUTOR_MODULE_PATHS) == 30
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_async_policy_audit_and_attempt20_are_hash_bound() -> None:
    value = bound()
    assert value["async_observation_audit"]["sha256"] == sha(AUDIT)
    assert value["write_once_history"]["attempt_20_refusal"]["sha256"] == sha(
        REFUSAL
    )
    assert value["write_once_history"]["attempt_20_cost_reconciliation"][
        "sha256"
    ] == sha(RECONCILIATION)
    assert value["async_observation_policy"] == {
        "absence_is_only_retryable_receipt_state": True,
        "listener_absence_retryable_only_while_pod_non_terminal": True,
        "malformed_drift_terminal_or_regression_retryable": False,
        "network_receipt_timeout_seconds": 300,
        "poll_interval_seconds": 10,
        "required_stable_observations": 2,
        "safe_refusal_diagnostics_before_cleanup": True,
        "shared_module": "scripts/asr_base_model_async_observations.py",
    }


def test_attempt21_plan_and_prohibitions_are_unchanged() -> None:
    value = bound()
    result = validate_plan(exact_plan(value, 21), value, 21)
    assert result == {
        "status": "PASS_EXACT_EXECUTION_PLAN",
        "attempt": 21,
        "permanent_create_only": 0,
        "permanent_bounded_update": 0,
        "temporary_create_then_delete": 18,
        "bounded_capacity_change": 1,
    }
    text = PACKET.read_text()
    for boundary in ("IAM", "internet", "training", "serving", "approved/asr"):
        assert boundary in text


def test_cost_registry_016_is_conservative_and_request_fits() -> None:
    value = bound()
    assert value["cost_registry"]["sha256"] == sha(COST)
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
    assert ceiling - committed - Decimal("10") == Decimal("115.5713935784")
