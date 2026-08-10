from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load() -> dict:
    return json.loads(RESULT.read_bytes())


def test_result_binds_reviewed_packet_and_owner_authorization():
    value = load()
    governance = value["governance_bindings"]
    for name in ("packet", "authorization"):
        binding = governance[name]
        assert sha256(ROOT / binding["path"]) == binding["sha256"]
    assert governance["packet"]["sha256"] == (
        "8e286f062aea3148d22689763969995fceafe5134b3f5030396ce7807509e5af"
    )
    assert governance["authorization"]["status"] == "owner-approved"


def test_every_persisted_receipt_is_hash_bound_and_phi_safe():
    value = load()
    receipts = value["stage_receipts"]
    assert [item["stage"] for item in receipts] == [
        "local_bindings",
        "deadline",
        "workers_ready",
        "terraform_window",
        "controller_ready",
        "dra_ready",
        "rag_ready",
        "asr_ready",
        "tts_ready",
        "llm_ready",
        "orchestrator_ready",
        "cleanup",
    ]
    assert all(item["status"] == "PASS" for item in receipts)
    for binding in receipts:
        path = ROOT / binding["path"]
        receipt = json.loads(path.read_bytes())
        assert sha256(path) == binding["sha256"]
        assert receipt["stage"] == binding["stage"]
        assert receipt["status"] == binding["status"]
        assert receipt["recorded_utc"] == binding["recorded_utc"]
        assert receipt["contains_audio_transcript_reply_citations_credentials_or_phi"] is False


def test_actual_refusal_is_fargate_private_ecr_network_path():
    value = load()
    refusal = value["fargate_probe_refusal"]
    network = value["read_only_network_diagnosis"]
    assert value["status"] == "VERIFIED_REFUSED_AT_FARGATE_ECR_PULL_CLEANUP_PASS"
    assert value["failure_stage"] == "ALB_FARGATE_READYZ_PROBE_IMAGE_PULL"
    assert refusal["task_stop_code"] == "TaskFailedToStart"
    assert refusal["container_exit_code"] == "ABSENT_CONTAINER_NEVER_STARTED"
    assert refusal["probe_application_started"] is False
    assert refusal["readyz_request_attempted"] is False
    assert refusal["alb_ready_receipt_persisted"] is False
    assert network["probe_assign_public_ip"] == "DISABLED"
    assert network["security_group_egress"] == "ALLOW_ALL_IPV4"
    assert network["nat_gateway_route"] == "ABSENT"
    assert network["ecr_api_vpc_endpoint"] == "ABSENT"
    assert network["ecr_dkr_vpc_endpoint"] == "ABSENT"
    assert network["s3_vpc_endpoint"] == "ABSENT"
    assert network["classification"] == "NETWORK_PATH_GAP_NOT_IAM_OR_SECURITY_GROUP_EGRESS"


def test_three_rule_defect_is_separate_and_not_reclassified_as_current_cause():
    finding = load()["separate_latent_boundary_finding"]
    assert finding["caused_this_run_to_stop"] is False
    assert finding["observed_non_default_rule_count"] == 3
    assert finding["expected_routes"] == [
        "/v1/conversations/speech",
        "/v1/conversations/stream",
        "/readyz",
    ]
    assert len(finding["create_rule_cloudtrail_event_ids"]) == 3


def test_cleanup_is_zero_and_budget_continuity_is_inside_allowance():
    value = load()
    cleanup = value["cleanup"]
    zero = value["zero_state_verification"]
    budget = value["budget_control"]
    assert cleanup["automatic_cleanup"] == "PASS"
    assert cleanup["deadlines_removed_after_zero"] is True
    assert cleanup["window_alb"] == "ABSENT"
    assert cleanup["window_probe_iam_role"] == "ABSENT"
    assert zero["cpu_nodegroup"]["desired"] == 0
    assert zero["gpu_nodegroup"]["desired"] == 0
    assert zero["cpu_nodegroup"]["asg_instances"] == 0
    assert zero["gpu_nodegroup"]["asg_instances"] == 0
    assert zero["deadline_actions_remaining"] == 0
    assert zero["production_serving_pointer_count"] == 0
    assert zero["approved_asr_objects"] == 0
    assert budget["cumulative_window_seconds_after_packet_2026_013"] == (
        budget["cumulative_window_seconds_before_packet_2026_013"]
        + budget["packet_2026_013_conservative_wall_clock_seconds"]
    )
    assert budget["remaining_window_seconds"] == (
        budget["maximum_cumulative_window_seconds"]
        - budget["cumulative_window_seconds_after_packet_2026_013"]
    )
    assert budget["cumulative_window_seconds_after_packet_2026_013"] < 14400
    assert budget["new_reservation_usd"] == 0.0
    assert value["project_state"]["b6_6_complete"] is False
