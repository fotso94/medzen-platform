#!/usr/bin/env python3
"""Fail closed unless B6.6 authorization binds every reviewed execution input."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-016"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-016"
CREDENTIAL_EVIDENCE_PATH = (
    "platform/evidence/B6-CLIENT-SECRET-RESTORATION-AWS-EXECUTION-2026-002.json"
)
CREDENTIAL_EVIDENCE_SHA256 = (
    "3d221399287dc55c3ae2d72d1a5e381680dc5263d21451c8434ccc21f95becb3"
)
EXPECTED_CREDENTIAL_BINDING = {
    "secret_arn": (
        "arn:aws:secretsmanager:eu-central-1:558069890522:"
        "secret:medzen/client-api-keys-NxZGxE"
    ),
    "new_version_id": "daacb67e-fcd1-41e1-bf62-47a3f18c8d0b",
    "new_version_stage": "AWSCURRENT",
    "prior_current_version_id": "d09d567e-9bde-482a-b95a-3cab990a1006",
    "prior_current_version_stages": [],
    "older_version_id": "f78c8aa8-2765-4788-9928-dd1ba7c406bf",
    "older_version_stages": [],
    "bearer_token_sha256": (
        "77f2979e024c42e91db938fecdb6214359637b316ad5edf6bbf1008fe59a89ea"
    ),
    "secret_value_sha256": (
        "39bd665f417671bc57066271ecf012df81179326a7f07e3a1c8220953d78a41a"
    ),
    "plaintext_recorded": False,
}
REQUIRED_SOURCES = {
    "infra/alb_controller.tf",
    "infra/b6_integration_window.tf",
    "infra/b6_client_secret.tf",
    "infra/b6_6_window_override.tf",
    "infra/eks.tf",
    "infra/b6_planning_override.tf",
    "infra/variables.tf",
    "pipeline/b6_integration_receipts.py",
    "platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml",
    "platform/decisions/B6-AWS-AUTH-2026-015-synthetic-credential-restoration.json",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-014-b6-6-private-probe-successor.md",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-015-synthetic-credential-restoration.md",
    "platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json",
    "platform/evidence/B6-CLIENT-API-KEYS-2026-001.json",
    "platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json",
    "platform/evidence/B6-LBC-QUALIFICATION-AWS-EXECUTION-2026-001.json",
    "platform/evidence/B6-6-LOCAL-CORRECTION-2026-001.json",
    "platform/evidence/B6-6-LOCAL-CORRECTION-2026-002.json",
    "platform/evidence/B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json",
    CREDENTIAL_EVIDENCE_PATH,
    "platform/evidence/B6-PACKET-2026-008-REFUSED-WORKER-REGISTRATION.json",
    "platform/evidence/B6-PACKET-2026-009-REFUSED-TOKEN-ENCODING.json",
    "platform/evidence/B6-PACKET-2026-010-REFUSED-ALB-LISTENER-IAM.json",
    "platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json",
    "platform/evidence/B6-LBC-IAM-LIFECYCLE-AWS-EXECUTION-2026-001.json",
    "platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json",
    "platform/evidence/receipts/B6-2026-015-LIVE/preflight.json",
    "platform/evidence/receipts/B6-2026-015-LIVE/restore.json",
    "platform/evidence/receipts/B6-2026-015-LIVE/rotation.json",
    "platform/evidence/receipts/B6-2026-015-LIVE/terraform_import.json",
    "platform/evidence/receipts/B6-2026-015-LIVE/terraform_normalization.json",
    "platform/evidence/receipts/B6-2026-015-LIVE/terraform_reconciliation.json",
    "platform/evidence/receipts/B6-2026-015-LIVE/verification.json",
    "platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-001.json",
    "platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json",
    "platform/finance/COST-REGISTRY-2026-004.json",
    "platform/k8s/b6-6/integration-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
    "platform/testdata/orchestrator/synthetic-file-request.wav",
    "scripts/b6_6_cleanup.sh",
    "scripts/b6_6_bindings.py",
    "scripts/b6_6_deadline.py",
    "scripts/b6_6_fargate_probe.py",
    "scripts/b6_6_lbc_runtime.py",
    "scripts/b6_6_lbc_tag_warning.py",
    "scripts/b6_6_probe.py",
    "scripts/b6_6_probe_endpoints.py",
    "scripts/b6_6_receipt.py",
    "scripts/b6_6_secret_preflight.py",
    "scripts/b6_6_token_binding.py",
    "scripts/b6_6_wait_workers.py",
    "scripts/check_b6_6_window_plan.py",
    "scripts/pin_aws_lbc_digest.py",
    "scripts/run_b6_6_integration_window.sh",
    "scripts/run_b6_client_secret_restoration.py",
    "scripts/terraform_medzen.sh",
    "tests/test_b6_6_attempt_4_runtime.py",
    "tests/test_b6_6_executable_assets.py",
    "tests/test_b6_6_final_window_successor.py",
    "tests/test_b6_6_private_probe_successor.py",
    "tests/test_b6_lbc_tag_warning.py",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact B6.6 packet SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("B6.6 authorization is absent or unreadable") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("B6.6 is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("B6.6 packet binding differs")
    review = value.get("independent_review", {})
    if (
        review.get("status") != "PASS"
        or not isinstance(review.get("reviewer"), str)
        or review.get("reviewed_packet_sha256") != packet_sha256
    ):
        raise BindingRefusal("independent B6.6 review is absent")
    if value.get("credential_binding") != EXPECTED_CREDENTIAL_BINDING:
        raise BindingRefusal("B6.6 fresh credential binding differs")
    if value.get("credential_restoration_evidence") != {
        "path": CREDENTIAL_EVIDENCE_PATH,
        "sha256": CREDENTIAL_EVIDENCE_SHA256,
        "status": "VERIFIED_COMPLETE",
    }:
        raise BindingRefusal("B6.6 credential-restoration evidence differs")
    if sha256_file(root / CREDENTIAL_EVIDENCE_PATH) != CREDENTIAL_EVIDENCE_SHA256:
        raise BindingRefusal("B6.6 credential-restoration evidence hash differs")
    if value.get("cost", {}) != {
        "registry_id": "COST-REGISTRY-2026-004",
        "allocation_id": "B6-INTEGRATION-WINDOW-2026-001",
        "maximum_usd": 10.0,
    }:
        raise BindingRefusal("B6.6 cost binding differs")
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("B6.6 source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("B6.6 source path is unsafe")
        target = root / relative
        if not target.is_file() or sha256_file(target) != expected:
            raise BindingRefusal(f"B6.6 source hash differs: {relative}")
    return value
