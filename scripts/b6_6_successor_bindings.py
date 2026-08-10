#!/usr/bin/env python3
"""Validate review, packet and source bindings for the B6 successor."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-017"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-017"
MANIFEST_PATH = "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-004.json"
REQUIRED_SOURCES = {
    "infra/alb_controller.tf",
    "infra/b6_6_endpoint_policy_override.tf",
    "infra/b6_6_window_override.tf",
    "infra/b6_integration_window.tf",
    "infra/b6_client_secret.tf",
    "infra/b6_planning_override.tf",
    "infra/eks.tf",
    "infra/variables.tf",
    "pipeline/b6_integration_receipts.py",
    MANIFEST_PATH,
    "platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-016-b6-6-final-window.md",
    "platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json",
    "platform/evidence/B6-PACKET-2026-016-REFUSED-ECR-ENDPOINT-POLICY.json",
    "platform/evidence/B6-6-ENDPOINT-POLICY-CORRECTION-EVALUATION-2026-001.json",
    "platform/evidence/B6-6-PRINCIPAL-INDEPENDENT-SUCCESSOR-LOCAL-PREPARATION-2026-001.json",
    "platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json",
    "platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json",
    "platform/finance/COST-REGISTRY-2026-004.json",
    "platform/iam/medzen-lbc-role.policy.template.json",
    "platform/k8s/b6-6/integration-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
    "platform/testdata/orchestrator/synthetic-file-request.wav",
    "scripts/b6_6_deadline.py",
    "scripts/b6_6_fargate_probe.py",
    "scripts/b6_6_lbc_runtime.py",
    "scripts/b6_6_lbc_tag_warning.py",
    "scripts/b6_6_probe.py",
    "scripts/b6_6_probe_endpoints.py",
    "scripts/b6_6_receipt.py",
    "scripts/b6_6_successor_bindings.py",
    "scripts/b6_6_successor_cleanup.sh",
    "scripts/b6_6_successor_credential_stage.py",
    "scripts/b6_6_successor_credential_stage.sh",
    "scripts/b6_6_successor_deadline.py",
    "scripts/b6_6_successor_fargate_probe.py",
    "scripts/b6_6_successor_probe_endpoints.py",
    "scripts/b6_6_successor_secret_preflight.py",
    "scripts/b6_6_successor_token_binding.py",
    "scripts/b6_6_successor_window.sh",
    "scripts/b6_6_token_binding.py",
    "scripts/b6_6_wait_workers.py",
    "scripts/b6_client_secret_restoration_2026_015_bindings.py",
    "scripts/check_b6_6_successor_window_plan.py",
    "scripts/check_b6_6_window_plan.py",
    "scripts/check_b6_client_secret_restoration_2026_015_plan.py",
    "scripts/check_b6_client_secret_restoration_plan.py",
    "scripts/pin_aws_lbc_digest.py",
    "scripts/run_b6_client_secret_restoration_2026_015.py",
    "scripts/terraform_medzen.sh",
    "tests/test_b6_6_successor_packet.py",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact successor packet SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("successor authorization is absent or unreadable") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("successor is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("successor packet binding differs")
    review = value.get("independent_review", {})
    reviewed_commit = review.get("reviewed_repository_commit")
    if (
        review.get("status") != "PASS"
        or not isinstance(review.get("reviewer"), str)
        or review.get("reviewed_packet_sha256") != packet_sha256
        or re.fullmatch(r"[0-9a-f]{40}", str(reviewed_commit)) is None
        or value.get("prepared_repository_commit") != reviewed_commit
    ):
        raise BindingRefusal("independent successor review is absent")
    if value.get("credential_stage") != {
        "mode": "IN_PACKET_STAGE_0_DYNAMIC_FRESH_BINDING",
        "manifest_path": MANIFEST_PATH,
        "manifest_sha256": sha256_file(root / MANIFEST_PATH),
        "receipt_subdirectory": "credential",
        "prior_current_version_id": "daacb67e-fcd1-41e1-bf62-47a3f18c8d0b",
        "plaintext_reuse_permitted": False,
    }:
        raise BindingRefusal("successor credential-stage binding differs")
    if value.get("cost") != {
        "registry_id": "COST-REGISTRY-2026-004",
        "allocation_id": "B6-INTEGRATION-WINDOW-2026-001",
        "maximum_usd": 10.0,
        "cumulative_seconds_before": 5985,
        "remaining_seconds_before": 8415,
        "maximum_worker_window_seconds": 4500,
        "remaining_seconds_after_full_cap": 3915,
        "new_reservation_usd": 0.0,
    }:
        raise BindingRefusal("successor cost binding differs")
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("successor source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("successor source path is unsafe")
        target = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise BindingRefusal(f"successor source hash differs: {relative}")
    return value
