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
    PROVEN_COMMAND_GATED_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan
from scripts.asr_base_model_proven_commands import validate_proven_command_bindings


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002O.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002O-attempt-16.md"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002O-COLD/cold-rehearsal.json"
AUDIT = ROOT / "platform/evidence/ASR-BASE-MODEL-LIVE-NODE-COMMAND-AUDIT-2026-001.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002N-ATTEMPT-15-GPU-SAMPLER-REFUSAL.json"
KEEP = ROOT / "platform/evidence/ASR-EVAL-HOST-TEMP-CLEANUP-KEEP-LIST-2026-002.json"
CLEANUP = ROOT / "platform/evidence/ASR-EVAL-HOST-TEMP-CLEANUP-RESULT-2026-002.json"
QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-LOCAL-RESOURCE-QUALIFICATION-2026-004.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-011.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt_sixteen_is_one_fresh_nontransferable_request() -> None:
    text = PACKET.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002O only" in text
    assert bound()["attempts"] == {
        "authorized_numbers": [16],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10,
        "attempts_1_through_15_reuse_permitted": False,
    }


def test_all_executor_modules_match_exact_reviewed_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(
        PROVEN_COMMAND_GATED_EXECUTOR_MODULE_PATHS
    )
    assert validate_executor_module_bindings(ROOT, value["executor_modules"])[
        "module_count"
    ] == 22
    for relative, expected in value["executor_modules"].items():
        completed = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        assert hashlib.sha256(completed.stdout).hexdigest() == expected


def test_proven_sampler_binding_and_unproven_commands_are_explicit() -> None:
    result = validate_proven_command_bindings(
        ROOT, bound()["proven_live_node_commands"]
    )
    assert result["sampler"]["status"] == "PASS_BYTE_IDENTICAL_HISTORICAL_ARGV"
    assert result["node_local_input_stage"] == "NOT_HISTORICALLY_PROVEN"
    assert result["pilot_workload"] == "NOT_HISTORICALLY_PROVEN"
    assert result["unproven_commands_reclassified_as_proven"] is False


def test_attempt_sixteen_plan_has_no_permanent_mutation() -> None:
    plan = exact_plan(bound(), 16)
    result = validate_plan(plan, bound(), 16)
    assert plan["permanent_create_only"] == []
    assert plan["permanent_bounded_update"] == []
    assert result["temporary_create_then_delete"] == 18
    assert result["bounded_capacity_change"] == 1


def test_packet_binds_attempt_15_and_host_evidence() -> None:
    text = PACKET.read_text(encoding="utf-8")
    for path in (AUDIT, REFUSAL, KEEP, CLEANUP, QUALIFICATION, COST, BINDINGS):
        assert sha(path) in text
    history = bound()["write_once_history"]
    assert history["attempt_15_refusal"]["sha256"] == sha(REFUSAL)
    assert history["live_node_command_audit"]["sha256"] == sha(AUDIT)
    assert history["attempt_16_cleanup_keep_list"]["sha256"] == sha(KEEP)
    assert history["attempt_16_cleanup_result"]["sha256"] == sha(CLEANUP)


def test_host_qualification_restores_standing_floor() -> None:
    value = json.loads(QUALIFICATION.read_bytes())
    assert value["validation"]["status"] == "PASS_PRE_ENVELOPE_LOCAL_RESOURCES"
    assert value["validation"]["measured_available_bytes"] >= 40 * 1024**3
    assert value["validation"]["attempt_number_consumed"] is False
    assert bound()["local_resource_qualification"]["sha256"] == sha(QUALIFICATION)


def test_receipt_last_rehearsal_proves_observed_failure_and_corrected_pass() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["bindings_source"]["sha256"] == sha(BINDINGS)
    assert receipt["executor_module_integrity"]["module_count"] == 22
    assert receipt["proven_live_node_command_bindings"]["sampler"]["status"] == (
        "PASS_BYTE_IDENTICAL_HISTORICAL_ARGV"
    )
    clean = receipt["scenarios"]["clean_pass"]["gpu_sampler_diagnostics"]
    assert clean["status"] == "PASS_120_NUMERIC_SAMPLES"
    assert clean["reason_code"] is None
    failure = receipt["scenarios"]["sampler_driver_library_missing"]
    assert failure["failure_stage"] == "gpu_and_sampler_gate"
    assert failure["failure_reason_code"] == "GPU_SAMPLER_DRIVER_LIBRARY_NOT_FOUND"
    assert failure["gpu_sampler_diagnostics"]["persisted_before_cleanup"] is True
    assert failure["zero_state"] is True
