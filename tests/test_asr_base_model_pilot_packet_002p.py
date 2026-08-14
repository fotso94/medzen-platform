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
    ATTEMPT_17_EXECUTOR_MODULE_PATHS,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002P.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002P-attempt-17.md"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002P-COLD/cold-rehearsal.json"
QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-NODE-STAGING-QUALIFICATION-2026-001.json"
AUDIT = ROOT / "platform/evidence/ASR-BASE-MODEL-PILOT-WORKLOAD-LESSONS-AUDIT-2026-001.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-012.json"
ATTEMPT_16 = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002O-ATTEMPT-16-NODE-STAGING-REFUSAL.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt17_is_one_fresh_nontransferable_request() -> None:
    value = bound()
    assert value["attempts"] == {
        "authorized_numbers": [17],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10,
        "attempts_1_through_16_reuse_permitted": False,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002P only" in text


def test_all_attempt17_executor_modules_are_bound() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_17_EXECUTOR_MODULE_PATHS)
    for relative, expected in value["executor_modules"].items():
        completed = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        assert hashlib.sha256(completed.stdout).hexdigest() == expected


def test_attempt17_plan_has_no_permanent_mutation() -> None:
    value = bound()
    plan = exact_plan(value, 17)
    result = validate_plan(plan, value, 17)
    assert plan["permanent_create_only"] == []
    assert plan["permanent_bounded_update"] == []
    assert result["temporary_create_then_delete"] == 18
    assert result["bounded_capacity_change"] == 1


def test_packet_binds_node_fix_workload_audit_cost_and_history() -> None:
    text = PACKET.read_text()
    for path in (BINDINGS, QUALIFICATION, AUDIT, COST, ATTEMPT_16):
        assert sha(path) in text
    history = bound()["write_once_history"]
    assert history["attempt_16_refusal"]["sha256"] == sha(ATTEMPT_16)
    assert bound()["node_staging_qualification"]["sha256"] == sha(QUALIFICATION)
    assert bound()["pilot_workload_lessons_audit"]["sha256"] == sha(AUDIT)
    assert bound()["cost_registry"]["sha256"] == sha(COST)


def test_final_cold_rehearsal_uses_committed_bindings_and_new_refusals() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["executor_module_integrity"]["module_count"] == len(
        ATTEMPT_17_EXECUTOR_MODULE_PATHS
    )
    node = value["scenarios"]["node_staging_unknown_user"]
    assert node["failure_stage"] == "node_local_input_stage"
    assert node["node_staging_diagnostics"]["persisted_before_cleanup"] is True
    assert "unknown user #10001" in node["node_staging_diagnostics"][
        "stderr_sanitized"
    ]
    workload = value["scenarios"]["pilot_job_refused"]
    assert workload["failure_stage"] == "pilot_rows"
    assert workload["pilot_workload_diagnostics"]["persisted_before_cleanup"] is True
    assert workload["zero_state"] is True


def test_historical_attempt16_record_is_unchanged() -> None:
    assert sha(ATTEMPT_16) == (
        "4f32505301c25d57510465db35edd213c498834a7f1d12a7a12b0a3cf7d6f025"
    )
