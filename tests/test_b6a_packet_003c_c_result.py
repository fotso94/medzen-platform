from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6A-PACKET-2026-003C-C-BLOCKED-GPU-MEMORY.json"


def _record() -> dict:
    return json.loads(RESULT.read_text())


def test_003c_c_result_is_blocked_not_complete_and_cleanup_is_zero() -> None:
    record = _record()
    assert record["outcome"] == "BLOCKED_PLATFORM_PROOF"
    assert record["proof"]["peak_l4_gpu_memory"] == "NOT_MEASURED"
    assert record["proof"]["transcription_evidence"]["accepted_as_b6a_transcription_proof"] is False
    assert record["project_state"]["b6a_complete"] is False
    assert record["project_state"]["b6_status"] == "BLOCKED"
    cleanup = record["cleanup_verification"]
    for key in (
        "eks_gpu_desired", "asg_desired", "asg_instances", "gpu_ec2_instances",
        "gpu_nodes", "b6a_pods", "asr_deployments", "dra_pods_at_gpu_zero",
        "scheduled_actions", "approved_asr_objects", "production_registry_parameters",
    ):
        assert cleanup[key] == 0


def test_003c_c_result_binds_unmodified_sources_and_receipts() -> None:
    record = _record()
    for path, expected in record["source_bindings"].items():
        if not path.startswith("scripts/"):
            continue
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
    assert record["authorization"]["sha256"] == hashlib.sha256(
        (ROOT / record["authorization"]["path"]).read_bytes()
    ).hexdigest()
    assert record["packet"]["sha256"] == hashlib.sha256(
        (ROOT / record["packet"]["path"]).read_bytes()
    ).hexdigest()


def test_003c_c_result_preserves_budget_and_production_boundaries() -> None:
    record = _record()
    budget = record["budget_control"]
    assert budget["cumulative_conservative_gpu_seconds"] == 680
    assert budget["remaining_original_two_hour_allowance_seconds"] == 6520
    assert budget["new_reservation_created"] is False
    retained = record["retained_nonserving_state"]
    assert retained["production_serving_state_changed"] is False
    assert retained["green_bucket_branch_or_data_changed"] is False


def test_003c_c_result_records_the_exact_control_defect_without_inventing_cause() -> None:
    cause = _record()["cause_assessment"]
    assert cause["control_defect"] == (
        "RAW_NONEMPTY_BASELINE_ACCEPTED_BEFORE_PARSED_SAMPLE_VALIDATION"
    )
    assert cause["exact_underlying_sampler_failure"] == "NOT_RECOVERABLE_FROM_003C_C"
