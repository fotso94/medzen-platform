from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6A-PACKET-2026-003C-D-BLOCKED-SSM-SAMPLER.json"


def _record() -> dict:
    return json.loads(RESULT.read_text())


def test_003c_d_receipt_sequence_stops_before_model_and_transcription() -> None:
    record = _record()
    assert record["outcome"] == "BLOCKED_SSM_SAMPLER_SELF_TEST"
    observed = [(item["stage"], item["status"]) for item in record["stage_receipts"]]
    assert observed == [
        ("local_bindings", "PASS"),
        ("deadline", "PASS"),
        ("dra_stable_readiness", "PASS"),
        ("sampler_self_test", "REFUSED"),
        ("cleanup", "PASS"),
    ]
    assert record["absent_stage_receipts"] == {
        "transcription": "NOT_CREATED_MODEL_NEVER_DEPLOYED",
        "gpu_memory_measurement": "NOT_CREATED_TRANSCRIPTION_STAGE_NOT_REACHED",
        "proof_summary": "NOT_CREATED_PROOF_STAGE_NOT_REACHED",
    }


def test_003c_d_published_receipts_are_exact_content_addressed_files() -> None:
    record = _record()
    for receipt in record["stage_receipts"]:
        path = ROOT / receipt["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]
        data = json.loads(path.read_text())
        assert data["stage"] == receipt["stage"]
        assert data["status"] == receipt["status"]
        assert data["contains_audio_transcript_logs_credentials_or_phi"] is False


def test_003c_d_transcription_memory_ordering_is_not_applicable() -> None:
    audit = _record()["proof_ordering_audit"]
    assert audit["transcription_receipt_persisted"] is False
    assert audit["gpu_memory_sampler_started"] is False
    assert audit["outcome"] == "NOT_APPLICABLE_STAGES_NOT_REACHED"


def test_003c_d_ssm_failure_is_bounded_without_invented_cause() -> None:
    failure = _record()["ssm_self_test"]
    assert failure["command_id"] == "9b0295f8-8bc3-428f-87a6-a6d08e2834b3"
    assert failure["document_version"] == "1"
    assert failure["response_code"] == 1
    assert failure["model_deployed_before_self_test"] is False
    assert failure["raw_stdout_or_stderr_preserved"] is False
    assert failure["exact_underlying_shell_refusal"] == "NOT_PRESERVED_BY_003C_D"


def test_003c_d_cleanup_budget_and_production_boundaries_hold() -> None:
    record = _record()
    cleanup = record["cleanup_verification"]
    for key in (
        "eks_gpu_desired", "asg_desired", "asg_instances", "gpu_nodes",
        "medzen_pods", "dra_pods_at_gpu_zero", "scheduled_actions",
        "approved_asr_objects_or_versions", "production_registry_parameters",
    ):
        assert cleanup[key] == 0
    assert cleanup["deadline_disarmed_after_zero"] is True
    assert cleanup["post_run_terraform_plan"] == "NO_CHANGES"
    budget = record["budget_control"]
    assert budget["cumulative_conservative_gpu_seconds"] == 1191
    assert budget["remaining_original_two_hour_allowance_seconds"] == 6009
    retained = record["retained_state"]
    assert retained["approved_artifact_written"] is False
    assert retained["model_registered"] is False
    assert retained["production_ssm_changed"] is False


def test_003c_d_governance_bindings_and_network_effect_are_explicit() -> None:
    record = _record()
    for binding in record["governance_bindings"].values():
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == binding["sha256"]
    network = record["local_session_and_network"]
    assert network["active_gpu_window_session_interrupted"] is False
    assert network["cleanup_session_interrupted"] is False
    assert network["post_run_read_only_audit_delay_observed"] is True
