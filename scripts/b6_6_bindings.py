#!/usr/bin/env python3
"""Fail closed unless packet 2026-021 binds the consolidated window exactly."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-021"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-021"
COLD_PATH = "platform/evidence/receipts/B6-2026-021-COLD/cold_rehearsal.json"
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
    "platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json",
    "platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json",
    "platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json",
    "platform/evidence/B6-PACKET-2026-019-REFUSED-BRIDGE-PRINCIPAL.json",
    "platform/evidence/B6-PACKET-2026-020-NOT-EXECUTED-PRINCIPAL-PREFLIGHT-CONDITION.json",
    "platform/evidence/B6-PACKET-2026-020A-ATTEMPT-1-REFUSED-ENDPOINT-PLAN-GUARD.json",
    "platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json",
    "platform/finance/COST-REGISTRY-2026-004.json",
    "platform/k8s/b6-6/integration-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
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
    "scripts/b6_6_wait_workers.py",
    "scripts/check_b6_6_persistent_secret_plan.py",
    "scripts/check_b6_6_window_plan.py",
    "scripts/terraform_medzen.sh",
    "tests/test_b6_6_consolidated_window.py",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact packet-2026-021 SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("packet-2026-021 authorization is absent") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("packet 2026-021 is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("packet-2026-021 binding differs")
    review = value.get("independent_review", {})
    reviewed_commit = review.get("reviewed_repository_commit")
    if (
        review.get("status") != "PASS"
        or not isinstance(review.get("reviewer"), str)
        or review.get("reviewed_packet_sha256") != packet_sha256
        or re.fullmatch(r"[0-9a-f]{40}", str(reviewed_commit)) is None
        or value.get("prepared_repository_commit") != reviewed_commit
    ):
        raise BindingRefusal("independent packet-2026-021 review is absent")
    if value.get("allowance") != {
        "aggregate_project_ceiling_usd": 300.0,
        "existing_reservation_usd": 10.0,
        "requested_attempts": 1,
        "maximum_seconds_per_attempt": 4500,
        "maximum_requested_worker_seconds": 4500,
        "estimated_compute_usd": 1.6,
        "cold_rehearsal_required_before_each_attempt": True,
        "unused_seconds_not_transferable_between_attempts": True,
    }:
        raise BindingRefusal("packet-2026-021 allowance binding differs")
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
    }:
        raise BindingRefusal("cold-rehearsal binding differs")
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("packet-2026-021 source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("packet-2026-021 source path is unsafe")
        target = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise BindingRefusal(f"packet-2026-021 source hash differs: {relative}")
    return value
