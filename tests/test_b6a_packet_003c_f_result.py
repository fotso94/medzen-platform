from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6A-PACKET-2026-003C-F-COMPLETE.json"
CLOSURE = ROOT / "platform/evidence/B6A-CLOSURE-2026-001.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def test_003c_f_result_binds_all_live_receipts_and_preserves_prewindow_receipt():
    value = load(RESULT)
    assert len(value["stage_receipts"]) == 8
    assert all(receipt["status"] == "PASS" for receipt in value["stage_receipts"])
    for receipt in value["stage_receipts"]:
        assert sha(ROOT / receipt["path"]) == receipt["sha256"]

    prewindow = value["prewindow_local_transport_refusal"]
    assert sha(ROOT / prewindow["receipt"]["path"]) == prewindow["receipt"]["sha256"]
    assert prewindow["gpu_window_opened"] is False
    assert prewindow["aws_or_kubernetes_mutation"] is False


def test_003c_f_transcription_receipt_precedes_memory_sampling():
    review = load(RESULT)["post_run_review"]
    transcription = datetime.fromisoformat(review["transcription_receipt_recorded_utc"])
    sampler = datetime.fromisoformat(review["memory_sampler_first_sample_utc"])
    assert sampler > transcription
    assert int((sampler - transcription).total_seconds() * 1000) == 2132
    assert review["transcription_receipt_fsynced_before_memory_sampler"] is True
    assert review["receipt_ordering"] == "PASS"


def test_003c_f_records_numeric_l4_peak_and_complete_cleanup():
    value = load(RESULT)
    review = value["post_run_review"]
    assert review["baseline_used_mib"] == 3988
    assert review["peak_used_mib"] == 4180
    assert review["total_mib"] == 23034
    assert review["measurement_samples"] == 14

    cleanup = value["cleanup_verification"]
    for field in (
        "eks_gpu_desired",
        "asg_desired",
        "asg_instances",
        "gpu_nodes",
        "dra_pods",
        "medzen_workloads",
        "scheduled_actions",
        "approved_asr_objects_or_versions",
        "production_registry_parameters",
    ):
        assert cleanup[field] == 0
    assert cleanup["gpu_ec2_state"] == "terminated"
    assert cleanup["post_run_terraform_plan"] == "NO_CHANGES"


def test_003c_f_budget_is_inside_bound_and_b6a_only_is_complete():
    value = load(RESULT)
    budget = value["budget_control"]
    assert budget["this_run_conservative_gpu_seconds"] == 773
    assert budget["cumulative_conservative_gpu_seconds"] == 3363
    assert budget["remaining_original_two_hour_allowance_seconds"] == 3837
    assert budget["cumulative_conservative_gpu_seconds"] <= 7200

    state = value["project_state"]
    assert state["b5_outcome"] == "BLOCKED"
    assert state["b6a_complete"] is True
    assert state["b6_complete"] is False
    assert state["approved_artifact_written"] is False
    assert state["model_registered"] is False
    assert state["production_ssm_changed"] is False


def test_b6a_closure_binds_result_and_stops_at_full_b6_planning_review():
    closure = load(CLOSURE)
    basis = closure["closure_basis"]["execution_result"]
    assert sha(RESULT) == basis["sha256"]
    assert basis["outcome"] == "B6A_PLATFORM_PROOF_COMPLETE"
    assert closure["status"] == "CLOSED_COMPLETE"
    assert closure["governance_boundaries"]["b5_passed"] is False
    assert closure["governance_boundaries"]["v0_approved_for_production"] is False
    assert closure["governance_boundaries"]["full_b6_complete"] is False
    assert closure["governance_boundaries"]["b6a_complete"] is True
    assert closure["next_step"] == "FULL_B6_PLANNING_REVIEW"
