from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6A-PACKET-2026-003C-E-BLOCKED-SSM-INVOCATION.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record() -> dict:
    return json.loads(RESULT.read_bytes())


def test_003c_e_result_binds_every_stage_receipt():
    value = record()
    for receipt in value["stage_receipts"]:
        assert sha(ROOT / receipt["path"]) == receipt["sha256"]
    assert [item["status"] for item in value["stage_receipts"]] == [
        "PASS",
        "PASS",
        "PASS",
        "REFUSED",
        "PASS",
    ]


def test_003c_e_stopped_before_model_and_memory_and_did_not_close_b6a():
    value = record()
    assert value["outcome"] == "BLOCKED_SSM_SAMPLER_SELF_TEST"
    assert value["ssm_sampler"]["first_lookup_result"] == "InvocationDoesNotExist"
    assert value["ssm_sampler"]["command_executed"] is False
    assert value["absent_stage_receipts"]["transcription"].startswith("NOT_CREATED")
    assert value["post_run_review"]["peak_l4_gpu_memory"] == "NOT_MEASURED"
    assert value["post_run_review"]["b6a_closed"] is False


def test_003c_e_cleanup_and_budget_fail_closed():
    value = record()
    cleanup = value["cleanup_verification"]
    assert cleanup["receipt_status"] == "PASS"
    assert cleanup["eks_gpu_desired"] == 0
    assert cleanup["asg_instances"] == 0
    assert cleanup["gpu_nodes"] == 0
    assert cleanup["scheduled_actions"] == 0
    assert cleanup["approved_asr_objects_or_versions"] == 0
    assert cleanup["production_registry_parameters"] == 0
    assert cleanup["post_run_terraform_plan"] == "NO_CHANGES"
    assert value["budget_control"]["cumulative_conservative_gpu_seconds"] == 2590
    assert value["budget_control"]["remaining_original_two_hour_allowance_seconds"] == 4610


def test_003c_e_packet_is_closed_and_requires_a_new_reviewed_packet():
    value = record()
    assert value["status"] == "VERIFIED_BLOCKED_CLEANUP_COMPLETE"
    assert value["next_boundary"].startswith("Packet 003C-E is closed")
    assert len(value["required_remediation_before_retry"]) == 4
