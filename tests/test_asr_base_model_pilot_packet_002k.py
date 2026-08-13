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
    RECORDED_AWS_REHEARSAL_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan


PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002K-attempt-12.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002K.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002K-COLD/cold-rehearsal.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002J-ATTEMPT-11-GPU-NODE-READINESS-REFUSAL.json"
DIAGNOSIS = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002J-ATTEMPT-11-GPU-NODE-READINESS-DIAGNOSIS.json"
FIXTURE = ROOT / "platform/evidence/ASR-BASE-MODEL-GPU-NODE-READINESS-FIXTURE-CAPTURE-2026-001.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-007.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_packet_is_non_executable_attempt_twelve_request() -> None:
    value = bound()
    text = PACKET.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002K only" in text
    assert value["attempts"] == {
        "authorized_numbers": [12],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10.0,
        "attempts_1_through_11_reuse_permitted": False,
    }


def test_attempt_eleven_history_refusal_and_diagnosis_are_write_once() -> None:
    history = bound()["write_once_history"]
    assert history["attempt_11_refusal"]["sha256"] == sha(REFUSAL)
    assert history["attempt_11_diagnosis"]["sha256"] == sha(DIAGNOSIS)
    for item in history.values():
        assert sha(ROOT / item["path"]) == item["sha256"]


def test_gpu_node_transition_fixture_is_exact_and_non_mutating() -> None:
    value = bound()["gpu_node_readiness_fixtures"]
    fixture = json.loads(FIXTURE.read_bytes())
    assert value["sha256"] == sha(FIXTURE)
    assert value["invented_kubernetes_fields_permitted"] is False
    assert fixture["capture_method"]["aws_mutations"] == 0
    assert fixture["capture_method"]["kubernetes_mutations"] == 0
    assert fixture["replay_rule"]["delayed_success_sequence"] == [
        "empty",
        "not_ready",
        "ready",
        "ready",
    ]
    assert fixture["causal_timeline"]["classification"] == "KUBELET_REGISTRATION_AND_READINESS_RACE_CONFIRMED"


def test_all_seventeen_executor_modules_match_the_bound_reviewed_commit() -> None:
    value = bound()
    assert tuple(value["executor_modules"]) == RECORDED_AWS_REHEARSAL_EXECUTOR_MODULE_PATHS
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_attempt_twelve_plan_is_existing_artifact_and_image_read_only() -> None:
    value = bound()
    plan = exact_plan(value, 12)
    assert plan["permanent_create_only"] == []
    assert plan["temporary_create_then_delete"]
    assert "ecr:repository/medzen-asr-eval-runtime" in plan["read_only_existing"]
    assert "s3:" + value["pilot_bundle"]["s3_prefix"].removeprefix("s3://") + "**" in plan["read_only_existing"]


def test_cold_rehearsal_proves_delayed_ready_and_timeout_paths() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["bindings_source"]["sha256"] == sha(BINDINGS)
    assert receipt["full_pass_runs"] == 2
    assert receipt["injected_failure_runs"] == 9
    delayed = receipt["scenarios"]["gpu_node_delayed_ready"]
    assert delayed["outcome"] == "PASS_PILOT"
    assert delayed["gpu_node_readiness"]["observation_sequence"] == [
        "CAPTURED_ATTEMPT_11_EMPTY",
        "CAPTURED_ATTEMPT_11_NOT_READY",
        "CAPTURED_ATTEMPT_11_READY",
        "CAPTURED_ATTEMPT_11_READY",
    ]
    timeout = receipt["scenarios"]["gpu_node_never_ready"]
    assert timeout["outcome"] == "FAILED_CLOSED_EXECUTION"
    assert timeout["failure_reason_code"] == "GPU_NODE_READY_TIMEOUT"
    assert timeout["gpu_node_readiness"]["reads"] == 60
    assert all(item["zero_state"] is True for item in receipt["scenarios"].values())


def test_cost_registry_separates_pending_actual_from_guardrail() -> None:
    value = json.loads(COST.read_bytes())
    assert bound()["cost_registry"]["sha256"] == sha(COST)
    assert value["attempt_11_cost_observation"]["actual_billing_state"] == "AWS_COST_EXPLORER_INGESTION_PENDING"
    summary = value["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == 84.4286064216
    assert summary["active_reservations_usd"] == 0.0
    assert summary["guardrail_headroom_after_reservations_usd"] == 215.5713935784
    assert summary["attempt_11_actual_direct_compute_gross_usd"] is None
    assert value["controls"]["attempt_11_actual_billing_must_be_reconciled_when_landed"] is True


def test_packet_binds_all_successor_evidence_hashes() -> None:
    text = PACKET.read_text(encoding="utf-8")
    for path in (BINDINGS, COLD, REFUSAL, DIAGNOSIS, FIXTURE, COST):
        assert sha(path) in text
