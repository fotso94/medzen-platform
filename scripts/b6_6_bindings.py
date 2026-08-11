#!/usr/bin/env python3
"""Fail closed unless packet 2026-027 binds the stable-ALB window exactly."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-027"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-027"
COLD_PATH = "platform/evidence/receipts/B6-2026-027-COLD/cold_rehearsal.json"
DESCRIPTION_PROJECTION_PATH = (
    "platform/evidence/B6-RENDERED-TERRAFORM-DESCRIPTIONS-2026-001.json"
)
REVIEW_PATH = "platform/designs/B6-WINDOW-DESIGN-REVIEW-2026-001.md"
REQUIRED_SOURCES = {
    "infra/alb_controller.tf",
    "infra/b6_6_endpoint_policy_override.tf",
    "infra/b6_6_persistent_secret_override.tf",
    "infra/b6_6_window_override.tf",
    "infra/b6_client_secret.tf",
    "infra/b6_integration_window.tf",
    "infra/eks.tf",
    "infra/variables.tf",
    "pipeline/b6_integration_receipts.py",
    REVIEW_PATH,
    "platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml",
    "platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json",
    "platform/decisions/B6-WINDOW-VERIFIER-POLICY-2026-001.json",
    "platform/decisions/B6-AWS-READ-FIXTURE-FIDELITY-2026-001.json",
    "platform/decisions/B6-ENDPOINT-VERIFIER-2026-002-empirical.json",
    "platform/decisions/B6-ALB-PROBE-STABILITY-2026-001.json",
    "platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json",
    "platform/evidence/B6-BACKEND-TASK-ENI-SG-EGRESS-READBACK-2026-001.json",
    DESCRIPTION_PROJECTION_PATH,
    "platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json",
    "platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json",
    "platform/evidence/B6-PACKET-2026-019-REFUSED-BRIDGE-PRINCIPAL.json",
    "platform/evidence/B6-PACKET-2026-020-NOT-EXECUTED-PRINCIPAL-PREFLIGHT-CONDITION.json",
    "platform/evidence/B6-PACKET-2026-020A-ATTEMPT-1-REFUSED-ENDPOINT-PLAN-GUARD.json",
    "platform/evidence/B6-PACKET-2026-021-ATTEMPT-2-REFUSED-FARGATE-BOUNDARY.json",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-022-fargate-boundary.md",
    "platform/decisions/B6-AWS-AUTH-2026-022-stage-a-and-window.json",
    "platform/evidence/B6-PACKET-2026-022-STAGE-A-REFUSED-ECR-EGRESS.json",
    "platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_endpoints.json",
    "platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_preflight.json",
    "platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_probe_1.json",
    "platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_terraform.json",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-023-probe-egress.md",
    "platform/decisions/B6-AWS-AUTH-2026-023-stage-a-and-window.json",
    "platform/evidence/B6-PACKET-2026-023-STAGE-A-REFUSED-SG-DESCRIPTION.json",
    "platform/evidence/receipts/B6-2026-023-COLD/cold_rehearsal.json",
    "platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a_preflight.json",
    "platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a_terraform.json",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-024-description-charset.md",
    "platform/decisions/B6-AWS-AUTH-2026-024-stage-a-and-window.json",
    "platform/evidence/B6-PACKET-2026-024-STAGE-A-REFUSED-ENDPOINT-VERIFIER-SHAPE.json",
    "platform/evidence/receipts/B6-2026-024-COLD/cold_rehearsal.json",
    "platform/evidence/receipts/B6-2026-024-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-024-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/evidence/receipts/B6-2026-024-STAGE-A-LIVE/stage_a_endpoints.json",
    "platform/evidence/receipts/B6-2026-024-STAGE-A-LIVE/stage_a_preflight.json",
    "platform/evidence/receipts/B6-2026-024-STAGE-A-LIVE/stage_a_terraform.json",
    "platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-001.json",
    "platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-002.json",
    "platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-003.json",
    "platform/evidence/B6-COST-RECONCILIATION-2026-005.json",
    "platform/evidence/B6-PACKET-2026-026-TERMINAL-FARGATE-TARGET-READINESS-RACE.json",
    "platform/evidence/B6-R5-VERIFIER-AUDIT-2026-001.json",
    "platform/evidence/B6-R5-VERIFIER-AUDIT-2026-002.json",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-025-per-rule-verifier.md",
    "platform/decisions/B6-AWS-AUTH-2026-025-stage-a-and-window.json",
    "platform/evidence/B6-PACKET-2026-025-STAGE-A-REFUSED-S3-ENDPOINT-API-SHAPE.json",
    "platform/evidence/receipts/B6-2026-025-COLD/cold_rehearsal.json",
    "platform/evidence/receipts/B6-2026-025-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-025-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/evidence/receipts/B6-2026-025-STAGE-A-LIVE/stage_a_endpoints.json",
    "platform/evidence/receipts/B6-2026-025-STAGE-A-LIVE/stage_a_preflight.json",
    "platform/evidence/receipts/B6-2026-025-STAGE-A-LIVE/stage_a_terraform.json",
    "platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-026-empirical-probe-verifier.md",
    "platform/decisions/B6-AWS-AUTH-2026-026-stage-a-and-window.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_endpoints.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_preflight.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_probe_1.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_probe_2.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_probe_3.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_terraform.json",
    "platform/finance/COST-REGISTRY-2026-004.json",
    "platform/finance/COST-REGISTRY-2026-005.json",
    "platform/k8s/b6-6/integration-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
    "platform/runtime-receipt-policy-v2.yaml",
    "platform/testdata/orchestrator/synthetic-file-request.wav",
    COLD_PATH,
    "scripts/b6_6_bindings.py",
    "scripts/b6_6_aws_read_fixtures.py",
    "scripts/b6_6_cleanup.sh",
    "scripts/b6_6_cold_rehearsal.py",
    "scripts/b6_6_credential.py",
    "scripts/b6_6_deadline.py",
    "scripts/b6_6_fargate_probe.py",
    "scripts/b6_6_lbc_runtime.py",
    "scripts/b6_6_lbc_tag_warning.py",
    "scripts/b6_6_manifest_slice.py",
    "scripts/b6_6_operations.sh",
    "scripts/b6_6_persistent_secret_bridge.py",
    "scripts/b6_6_pre_endpoint_images.py",
    "scripts/b6_6_probe.py",
    "scripts/b6_6_probe_endpoints.py",
    "scripts/b6_6_runner.py",
    "scripts/b6_6_stage_a.py",
    "scripts/b6_6_wait_workers.py",
    "scripts/check_b6_6_persistent_secret_plan.py",
    "scripts/check_b6_6_window_plan.py",
    "scripts/terraform_medzen.sh",
    "tests/test_b6_6_consolidated_window.py",
    "tests/test_b6_6_alb_target_readiness.py",
    "tests/test_b6_6_fargate_boundary.py",
    "tests/test_b6_6_r5_verifier_audit.py",
    "tests/test_b6_6_stage_a.py",
    "tests/test_cost_registry_2026_005.py",
    "tests/fixtures/aws/ec2-describe-security-group-rules-sg-070fc00321934eacb.json",
    "tests/fixtures/aws/ec2-describe-security-groups-sg-070fc00321934eacb.json",
    "tests/fixtures/aws/autoscaling-describe-auto-scaling-groups-medzen-cpu.json",
    "tests/fixtures/aws/autoscaling-describe-auto-scaling-groups-medzen-gpu.json",
    "tests/fixtures/aws/autoscaling-describe-scheduled-actions-medzen-cpu.json",
    "tests/fixtures/aws/autoscaling-describe-scheduled-actions-medzen-gpu.json",
    "tests/fixtures/aws/ec2-describe-prefix-lists-s3-eu-central-1.json",
    "tests/fixtures/aws/ec2-describe-vpc-endpoints-interface-vpce-0c807782b5e1c9577.json",
    "tests/fixtures/aws/ec2-describe-vpc-endpoints-s3-gateway-vpce-09b2f7b21a4f625f3.json",
    "tests/fixtures/aws/ecs-describe-clusters-medzen-b6-window-probe-inactive.json",
    "tests/fixtures/aws/ecs-describe-task-definition-medzen-b6-window-probe-9-inactive.json",
    "tests/fixtures/aws/ecs-describe-tasks-medzen-b6-window-probe-missing.json",
    "tests/fixtures/aws/ecs-list-tasks-medzen-b6-window-probe-empty.json",
    "tests/fixtures/aws/eks-describe-nodegroup-medzen-speech-cpu.json",
    "tests/fixtures/aws/eks-describe-nodegroup-medzen-speech-gpu.json",
    "tests/fixtures/aws/elbv2-describe-listeners-cache-proxy-test.json",
    "tests/fixtures/aws/elbv2-describe-load-balancers-cache-proxy-test.json",
    "tests/fixtures/aws/elbv2-describe-rules-cache-proxy-test.json",
    "tests/fixtures/aws/elbv2-describe-tags-cache-proxy-test.json",
    "tests/fixtures/aws/elbv2-describe-target-groups-cache-proxy-test.json",
    "tests/fixtures/aws/elbv2-describe-target-health-cache-proxy-test.json",
    "tests/fixtures/aws/elbv2-describe-target-health-medzen-ehrbase-healthy.json",
    "tests/fixtures/aws/iam-get-role-medzen-b6-window-probe-execution-absent.json",
    "tests/fixtures/aws/iam-get-role-medzen-orch-role.json",
    "tests/fixtures/aws/iam-get-user-s-fotso.json",
    "tests/fixtures/aws/secretsmanager-describe-secret-medzen-client-api-keys.json",
    "tests/fixtures/aws/secretsmanager-get-secret-value-medzen-client-api-keys-denied.json",
    "tests/fixtures/aws/secretsmanager-list-secret-version-ids-medzen-client-api-keys.json",
    "tests/fixtures/aws/ssm-get-parameters-by-path-b6-test-registry.json",
    "tests/fixtures/aws/ssm-get-parameters-by-path-serving-empty.json",
    "tests/fixtures/aws/sts-get-caller-identity-medzen.json",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact packet-2026-027 SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("packet-2026-027 authorization is absent") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("packet 2026-027 is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("packet-2026-027 binding differs")
    review = value.get("independent_review", {})
    reviewed_commit = review.get("reviewed_repository_commit")
    if (
        review.get("status") != "PASS"
        or not isinstance(review.get("reviewer"), str)
        or review.get("reviewed_packet_sha256") != packet_sha256
        or re.fullmatch(r"[0-9a-f]{40}", str(reviewed_commit)) is None
        or value.get("prepared_repository_commit") != reviewed_commit
    ):
        raise BindingRefusal("independent packet-2026-027 review is absent")
    if value.get("allowance") != {
        "aggregate_project_ceiling_usd": 300.0,
        "recognized_committed_guardrail_usd": 64.4286064216,
        "existing_reservation_usd": 10.0,
        "new_reservation_usd": 0.0,
        "requested_attempts": 2,
        "maximum_seconds_per_attempt": 4500,
        "maximum_requested_worker_seconds": 9000,
        "estimated_compute_usd": 3.2,
        "cold_rehearsal_required_before_each_attempt": True,
        "unused_seconds_not_transferable_between_attempts": True,
        "attempt_2_requires_attempt_1_refusal_and_zero_state": True,
        "pass_terminates_packet": True,
    }:
        raise BindingRefusal("packet-2026-027 allowance binding differs")
    if value.get("stage_a_reuse") != {
        "source_packet": "B6-AWS-CHANGE-PACKET-2026-026",
        "source_packet_sha256": "c39130c456b36b128f3c52fab22a533243c9d8e235128c574c3c56f892634702",
        "aggregate_receipt_path": "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json",
        "aggregate_receipt_sha256": sha256_file(
            root / "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json"
        ),
        "cleanup_receipt_path": "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json",
        "cleanup_receipt_sha256": sha256_file(
            root
            / "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json"
        ),
        "stable_probe_passes": 3,
        "cleanup_complete": True,
        "rerun_permitted": False,
    }:
        raise BindingRefusal("packet-2026-027 Stage A reuse binding differs")
    if value.get("persistent_secret") != {
        "bridge_receipt_required_before_attempt_1": True,
        "create_or_delete_during_window": False,
        "rotate_in_place_at_stage0": True,
        "operator_plaintext_read": "EXPLICIT_DENY_REQUIRED",
    }:
        raise BindingRefusal("persistent-secret lifecycle binding differs")
    from scripts.b6_6_aws_read_fixtures import audit as audit_aws_read_fixtures

    aws_read_fixture_fidelity = audit_aws_read_fixtures(root)
    bound_cold = json.loads((root / COLD_PATH).read_bytes()).get("payload", {})
    cold = value.get("cold_rehearsal", {})
    if cold != {
        "path": COLD_PATH,
        "sha256": sha256_file(root / COLD_PATH),
        "status": "PASS_COLD_REHEARSAL",
        "full_pass_runs": 1,
        "injected_failure_runs": 27,
        "stage_injected_failure_runs": 23,
        "new_gate_injected_failure_runs": 4,
        "stage_a_full_pass_runs": 1,
        "stage_a_injected_failure_runs": 7,
        "new_gate_rehearsal": bound_cold.get("new_gate_rehearsal"),
        "empirical_connectivity_gate": aws_read_fixture_fidelity[
            "network_reduction"
        ],
        "terraform_description_charset_lint": {
            "status": "PASS",
            "description_fields": 50,
            "string_descriptions": 48,
            "null_descriptions": 2,
            "invalid_descriptions": 0,
            "allowed_character_class": "A-Za-z0-9. _-:/()#,@[]+=&;{}!$*",
            "projection_path": DESCRIPTION_PROJECTION_PATH,
            "projection_sha256": sha256_file(root / DESCRIPTION_PROJECTION_PATH),
            "projection_inventory_sha256": "07ad67c8409d7b5f547bca51c6926cdd2e1fd0ea83a2918347a2d2ca7026b880",
            "invalid_description_refusal_cases": 1,
            "real_aws_calls": 0,
        },
        "aws_read_fixture_fidelity": aws_read_fixture_fidelity,
    }:
        raise BindingRefusal("cold-rehearsal binding differs")
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("packet-2026-027 source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("packet-2026-027 source path is unsafe")
        target = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise BindingRefusal(f"packet-2026-027 source hash differs: {relative}")
    return value
