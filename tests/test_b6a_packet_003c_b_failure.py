from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = (
    ROOT / "platform/evidence/B6A-PACKET-2026-003C-B-FAILED-DRA-READINESS.json"
)


def _result():
    return json.loads(RESULT.read_text())


def test_003c_b_failure_binds_authorization_and_packet():
    result = _result()
    assert result["status"] == "VERIFIED_FAILED_CLOSED"
    assert result["outcome"] == "FAILED_CLOSED_EXECUTION"
    for key in ("authorization", "packet"):
        binding = result[key]
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == (
            binding["sha256"]
        )


def test_003c_b_stopped_before_workload_or_transcription():
    refusal = _result()["observed_refusal"]
    assert refusal["asr_workload_apply_attempted"] is False
    assert refusal["medzen_namespace_created"] is False
    assert refusal["transcription_attempted"] is False
    assert refusal["peak_l4_memory"] == "NOT_MEASURED"
    assert refusal["security_waiver_used"] is False


def test_003c_b_cleanup_proved_every_gpu_state_zero():
    cleanup = _result()["cleanup_verification"]
    for key in (
        "eks_gpu_minimum",
        "eks_gpu_desired",
        "asg_desired",
        "asg_instances",
        "gpu_nodes",
        "b6a_pods",
        "asr_deployments",
        "scheduled_actions",
        "approved_asr_objects",
        "production_registry_parameters",
    ):
        assert cleanup[key] == 0
    assert cleanup["deadline_disarmed_after_zero_proof"] is True


def test_003c_b_does_not_overclaim_the_inferred_root_cause():
    cause = _result()["cause_assessment"]
    assert cause["most_likely_cause"] == "DRA_POD_OR_RESOURCE_SLICE_READINESS_RACE"
    assert cause["confidence"] == "HIGH_BUT_NOT_CONCLUSIVE"
    assert "not recoverable" in cause["not_proven"]


def test_003c_b_cannot_resume_without_new_packet():
    result = _result()
    assert result["project_state"]["b6a_complete"] is False
    assert result["project_state"]["b6_status"] == "BLOCKED"
    assert "No retry" in result["next_boundary"]
