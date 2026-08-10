#!/usr/bin/env python3
"""Fail closed unless packet 2026-025 binds the consolidated window exactly."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-025"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-025"
COLD_PATH = "platform/evidence/receipts/B6-2026-025-COLD/cold_rehearsal.json"
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
    "platform/evidence/B6-R5-VERIFIER-AUDIT-2026-001.json",
    "platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json",
    "platform/finance/COST-REGISTRY-2026-004.json",
    "platform/k8s/b6-6/integration-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
    "platform/runtime-receipt-policy-v2.yaml",
    "platform/testdata/orchestrator/synthetic-file-request.wav",
    COLD_PATH,
    "scripts/b6_6_bindings.py",
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
    "tests/test_b6_6_fargate_boundary.py",
    "tests/test_b6_6_r5_verifier_audit.py",
    "tests/test_b6_6_stage_a.py",
    "tests/fixtures/aws/ec2-describe-security-group-rules-sg-070fc00321934eacb.json",
    "tests/fixtures/aws/ec2-describe-security-groups-sg-070fc00321934eacb.json",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact packet-2026-025 SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("packet-2026-025 authorization is absent") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("packet 2026-025 is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("packet-2026-025 binding differs")
    review = value.get("independent_review", {})
    reviewed_commit = review.get("reviewed_repository_commit")
    if (
        review.get("status") != "PASS"
        or not isinstance(review.get("reviewer"), str)
        or review.get("reviewed_packet_sha256") != packet_sha256
        or re.fullmatch(r"[0-9a-f]{40}", str(reviewed_commit)) is None
        or value.get("prepared_repository_commit") != reviewed_commit
    ):
        raise BindingRefusal("independent packet-2026-025 review is absent")
    if value.get("allowance") != {
        "aggregate_project_ceiling_usd": 300.0,
        "existing_reservation_usd": 10.0,
        "stage_a_requested_runs": 1,
        "stage_a_maximum_seconds": 1800,
        "stage_a_maximum_cost_usd": 0.5,
        "stage_a_consecutive_probe_tasks": 3,
        "stage_a_eks_worker_mutations": 0,
        "stage_a_pass_required_before_window": True,
        "requested_attempts": 2,
        "maximum_seconds_per_attempt": 4500,
        "maximum_requested_worker_seconds": 9000,
        "estimated_compute_usd": 3.2,
        "combined_stage_a_and_window_ceiling_usd": 3.7,
        "cold_rehearsal_required_before_each_attempt": True,
        "unused_seconds_not_transferable_between_attempts": True,
    }:
        raise BindingRefusal("packet-2026-025 allowance binding differs")
    if value.get("persistent_secret") != {
        "bridge_receipt_required_before_attempt_1": True,
        "create_or_delete_during_window": False,
        "rotate_in_place_at_stage0": True,
        "operator_plaintext_read": "EXPLICIT_DENY_REQUIRED",
    }:
        raise BindingRefusal("persistent-secret lifecycle binding differs")
    cold = value.get("cold_rehearsal", {})
    if cold != {
        "path": COLD_PATH,
        "sha256": sha256_file(root / COLD_PATH),
        "status": "PASS_COLD_REHEARSAL",
        "full_pass_runs": 1,
        "injected_failure_runs": 23,
        "stage_a_full_pass_runs": 1,
        "stage_a_injected_failure_runs": 7,
        "task_eni_sg_egress_lint": {
            "status": "PASS",
            "task_eni_security_groups": 2,
            "egress_rules": 3,
            "missing_egress_security_groups": 0,
            "plan_managed_task_eni_security_groups": 1,
            "external_attested_task_eni_security_groups": 1,
            "packet_managed_egress_rules": 2,
            "external_attested_egress_rules": 1,
            "missing_egress_refusal_cases": 2,
            "dns_security_group_filtering": "NOT_APPLICABLE_AMAZON_PROVIDED_VPC_RESOLVER",
        },
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
        "aws_read_fixture_fidelity": {
            "status": "PASS",
            "decision_path": "platform/decisions/B6-AWS-READ-FIXTURE-FIDELITY-2026-001.json",
            "decision_sha256": "4d048375b6b17d9e84ec29babbf5bb8b007b74d6736032a424293c541d8ee822",
            "evidence_path": "platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-001.json",
            "evidence_sha256": "6bd723750bcf006ea760d78617b21781114c5a63d9943d91b5c3e2ce2cbe876d",
            "fixture_hashes": {
                "tests/fixtures/aws/ec2-describe-security-group-rules-sg-070fc00321934eacb.json": "96dd135dc918f7b7de260d8aa92df3bd7ffd184b8796ab1b90e43933927af469",
                "tests/fixtures/aws/ec2-describe-security-groups-sg-070fc00321934eacb.json": "2f9129d630cadc5f2915a5bf8c9b9885096fb39b43bfbe5125fab23d71c5a49a",
            },
            "merged_egress_permission_objects": 1,
            "individual_egress_rules": 2,
            "protocol_minus_one_port_quirk": "PASS",
            "real_aws_calls": 0,
        },
    }:
        raise BindingRefusal("cold-rehearsal binding differs")
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("packet-2026-025 source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("packet-2026-025 source path is unsafe")
        target = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise BindingRefusal(f"packet-2026-025 source hash differs: {relative}")
    return value
