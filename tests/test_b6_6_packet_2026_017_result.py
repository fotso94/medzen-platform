from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = (
    ROOT
    / "platform/evidence/B6-PACKET-2026-017-REFUSED-DRA-ECR-ENDPOINT-DNS.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load() -> dict:
    return json.loads(RESULT.read_bytes())


def test_result_binds_exact_reviewed_packet_authorization_and_cost_registry():
    value = load()
    governance = value["governance_bindings"]
    for name in ("packet", "authorization", "cost_registry"):
        binding = governance[name]
        assert sha256(ROOT / binding["path"]) == binding["sha256"]
    assert governance["packet"]["sha256"] == (
        "8fa32f4013445fd18ad353119ddd10a1c5c199935059a63afedf951c61a045b6"
    )
    assert governance["authorization"]["status"] == "owner-approved"


def test_every_persisted_main_and_credential_receipt_is_hash_bound():
    value = load()
    main = value["stage_receipts"]
    assert [item["stage"] for item in main] == [
        "local_bindings",
        "deadline",
        "workers_ready",
        "terraform_window",
        "endpoints_ready",
        "controller_ready",
        "cleanup",
    ]
    for binding in main:
        path = ROOT / binding["path"]
        receipt = json.loads(path.read_bytes())
        assert sha256(path) == binding["sha256"]
        assert receipt["stage"] == binding["stage"]
        assert receipt["status"] == binding["status"] == "PASS"
        assert receipt["recorded_utc"] == binding["recorded_utc"]
        assert receipt["contains_audio_transcript_reply_citations_credentials_or_phi"] is False
    for binding in value["credential_stage_zero"]["receipts"]:
        path = ROOT / binding["path"]
        receipt = json.loads(path.read_bytes())
        assert sha256(path) == binding["sha256"]
        assert receipt["stage"] == binding["stage"]
        assert receipt["status"] == binding["status"]
        assert receipt["recorded_utc"] == binding["recorded_utc"]


def test_endpoint_policy_correction_passed_before_dra_network_refusal():
    value = load()
    endpoint = value["successful_controls_before_refusal"]["endpoint_correction"]
    refusal = value["dra_image_pull_refusal"]
    assert endpoint["status"] == "PASS_PROVEN"
    assert endpoint["interface_endpoints_available"] == 2
    assert endpoint["principal_mode"] == "REQUIRED_WILDCARD_NO_ROLE_REFERENCE"
    assert endpoint["network_ingress_mode"] == "PROBE_EXCLUSIVE_SELF_REFERENCE"
    assert endpoint["prior_same_apply_principal_propagation_race_removed"] is True
    assert value["failure_stage"] == "DRA_READY_IMAGE_PULL"
    assert refusal["pod_state"] == "Init:ErrImagePull"
    assert refusal["observed_error_class"] == "TCP_443_IO_TIMEOUT"
    assert refusal["resolved_private_endpoint_ip"] == "172.31.26.108"
    assert refusal["dra_ready_receipt_persisted"] is False
    assert refusal["service_deployment_attempted"] is False
    assert refusal["packet_scope_changed_during_execution"] is False


def test_diagnosis_is_bounded_and_does_not_overclaim_endpoint_policy_coverage():
    diagnosis = load()["read_only_diagnosis"]
    assert diagnosis["classification"] == (
        "PROVEN_ENDPOINT_PRIVATE_DNS_AND_SECURITY_GROUP_REACHABILITY_CONFLICT"
    )
    assert "private DNS" in diagnosis["root_cause"]
    assert "endpoint policy" in diagnosis["scope_limit"]
    assert diagnosis["no_mid_run_correction_or_retry_performed"] is True


def test_cleanup_is_zero_and_budget_continuity_remains_inside_allowance():
    value = load()
    cleanup = value["cleanup"]
    zero = value["zero_state_verification"]
    budget = value["budget_control"]
    assert cleanup["automatic_cleanup"] == "PASS"
    assert cleanup["terraform_cleanup_plan"]["destroys"] == 15
    assert cleanup["terraform_cleanup_plan"]["machine_guard"] == "PASS_B6_6_CLEANUP"
    assert cleanup["managed_cpu_termination_hook"]["operator_forced_completion"] is False
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
    assert budget["cumulative_window_seconds_after_packet_2026_017"] == (
        budget["cumulative_window_seconds_before_packet_2026_017"]
        + budget["packet_2026_017_conservative_wall_clock_seconds"]
    )
    assert budget["remaining_window_seconds"] == (
        budget["maximum_cumulative_window_seconds"]
        - budget["cumulative_window_seconds_after_packet_2026_017"]
    )
    assert budget["remaining_window_seconds"] == 6852
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
        "platform/evidence/B6-PACKET-2026-012-REFUSED-IMPORTED-STATE-DRIFT.json": (
            "19be424d2aee39f862c5e6de3b2335a87a8031b5e84e3fd6a50d03a465164c69"
        ),
        "platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json": (
            "daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a"
        ),
        "platform/evidence/B6-PACKET-2026-016-REFUSED-ECR-ENDPOINT-POLICY.json": (
            "7538b6a3f9d80201b8161f43aef0115d0d3424d7daff33caa58e460308b940f3"
        ),
    }
    for relative, digest in expected.items():
        assert sha256(ROOT / relative) == digest
