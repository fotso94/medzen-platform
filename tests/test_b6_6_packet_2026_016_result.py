from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = (
    ROOT
    / "platform/evidence/B6-PACKET-2026-016-REFUSED-ECR-ENDPOINT-POLICY.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load() -> dict:
    return json.loads(RESULT.read_bytes())


def test_result_binds_exact_reviewed_packet_and_owner_authorization():
    value = load()
    governance = value["governance_bindings"]
    for name in ("packet", "authorization"):
        binding = governance[name]
        assert sha256(ROOT / binding["path"]) == binding["sha256"]
    assert governance["packet"]["sha256"] == (
        "1560c5b6a775377cff43bf46a236bdd5da0c645cf3f846b33bc63ed50c670f6d"
    )
    assert governance["authorization"]["status"] == "owner-approved"


def test_only_completed_stage_receipts_are_persisted_and_hash_bound():
    receipts = load()["stage_receipts"]
    assert [item["stage"] for item in receipts] == [
        "local_bindings",
        "deadline",
        "workers_ready",
        "cleanup",
    ]
    for binding in receipts:
        path = ROOT / binding["path"]
        receipt = json.loads(path.read_bytes())
        assert sha256(path) == binding["sha256"]
        assert receipt["stage"] == binding["stage"]
        assert receipt["status"] == binding["status"] == "PASS"
        assert receipt["recorded_utc"] == binding["recorded_utc"]
        assert receipt["contains_audio_transcript_reply_citations_credentials_or_phi"] is False


def test_refusal_is_exactly_the_two_ecr_interface_endpoint_policies():
    value = load()
    refusal = value["endpoint_policy_refusal"]
    assert value["status"] == (
        "VERIFIED_REFUSED_AT_ECR_INTERFACE_ENDPOINT_POLICY_CLEANUP_PASS"
    )
    assert value["failure_stage"] == "TERRAFORM_WINDOW_ECR_INTERFACE_ENDPOINT_CREATE"
    assert refusal["terraform_apply_attempted"] is True
    assert refusal["terraform_window_receipt_persisted"] is False
    assert refusal["service_deployment_attempted"] is False
    assert refusal["fargate_task_started"] is False
    assert refusal["internal_alb_created"] is False
    assert refusal["ecr_api"]["error_code"] == "Client.InvalidPolicyDocument"
    assert refusal["ecr_dkr"]["error_code"] == "Client.InvalidPolicyDocument"
    assert refusal["ecr_api"]["policy"]["actions"] == ["ecr:GetAuthorizationToken"]
    assert refusal["ecr_dkr"]["policy"]["resource"].endswith(
        ":repository/medzen-rag-index"
    )
    assert refusal["s3_control"]["result"] == "CREATED_THEN_REMOVED_BY_CLEANUP"


def test_diagnosis_is_strong_inference_and_does_not_overclaim_root_cause():
    diagnosis = load()["read_only_diagnosis"]
    assert diagnosis["classification"] == (
        "STRONG_INFERENCE_IAM_PRINCIPAL_PROPAGATION_NOT_YET_PROVEN"
    )
    assert diagnosis["temporary_role_create"]["result"] == "SUCCESS"
    assert diagnosis["seconds_from_role_create_to_ecr_dkr_request"] == 3
    assert diagnosis["seconds_from_role_create_to_ecr_api_request"] == 4
    assert diagnosis["not_proven"]
    assert diagnosis["no_correction_or_retry_performed"] is True
    assert all(url.startswith("https://docs.aws.amazon.com/") for url in diagnosis["official_references"])


def test_cleanup_is_zero_and_budget_continuity_remains_inside_allowance():
    value = load()
    cleanup = value["cleanup"]
    zero = value["zero_state_verification"]
    budget = value["budget_control"]
    assert cleanup["automatic_cleanup"] == "PASS"
    assert cleanup["terraform_cleanup_plan"]["destroys"] == 13
    assert cleanup["terraform_cleanup_plan"]["partial_creation_subset_guard"] == (
        "PASS_B6_6_CLEANUP"
    )
    assert cleanup["deadlines_removed_after_zero"] is True
    assert cleanup["local_token_removed"] is True
    assert cleanup["window_vpc_endpoints"] == 0
    assert cleanup["window_endpoint_security_groups"] == 0
    assert zero["cpu_nodegroup"]["desired"] == 0
    assert zero["gpu_nodegroup"]["desired"] == 0
    assert zero["cpu_nodegroup"]["asg_instances"] == 0
    assert zero["gpu_nodegroup"]["asg_instances"] == 0
    assert zero["kubernetes_worker_nodes"] == 0
    assert zero["production_serving_pointer_count"] == 0
    assert zero["approved_asr_objects"] == 0
    assert budget["cumulative_window_seconds_after_packet_2026_016"] == (
        budget["cumulative_window_seconds_before_packet_2026_016"]
        + budget["packet_2026_016_conservative_wall_clock_seconds"]
    )
    assert budget["remaining_window_seconds"] == (
        budget["maximum_cumulative_window_seconds"]
        - budget["cumulative_window_seconds_after_packet_2026_016"]
    )
    assert budget["remaining_window_seconds"] == 8415
    assert budget["new_reservation_usd"] == 0.0
    assert value["project_state"]["b6_6_complete"] is False


def test_prior_refusal_records_remain_immutable():
    expected = {
        "platform/evidence/B6-PACKET-2026-008-REFUSED-WORKER-REGISTRATION.json": (
            "f2b8acbabafb2642e5b70ddbae966930f2ba62201c7a2fb26f6e32bc3246d432"
        ),
        "platform/evidence/B6-PACKET-2026-009-REFUSED-TOKEN-ENCODING.json": (
            "3295768ed6d326125f4c5098908a0b6e090c800a93b35c199ecadf0a574d8a49"
        ),
        "platform/evidence/B6-PACKET-2026-010-REFUSED-ALB-LISTENER-IAM.json": (
            "4ea2234f6803049d6d4afd4a24a2f03f118c1c45c090b173f61cfef8506fdabf"
        ),
        "platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json": (
            "daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a"
        ),
    }
    for relative, digest in expected.items():
        assert sha256(ROOT / relative) == digest
