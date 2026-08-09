#!/usr/bin/env python3
"""Fail closed unless B6.6 authorization binds every reviewed execution input."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-008"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-008"
REQUIRED_SOURCES = {
    "infra/alb_controller.tf",
    "infra/b6_integration_window.tf",
    "infra/b6_client_secret.tf",
    "infra/variables.tf",
    "pipeline/b6_integration_receipts.py",
    "platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml",
    "platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json",
    "platform/evidence/B6-CLIENT-API-KEYS-2026-001.json",
    "platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json",
    "platform/evidence/B6-LBC-QUALIFICATION-AWS-EXECUTION-2026-001.json",
    "platform/finance/COST-REGISTRY-2026-004.json",
    "platform/k8s/b6-6/integration-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
    "platform/testdata/orchestrator/synthetic-file-request.wav",
    "scripts/b6_6_cleanup.sh",
    "scripts/b6_6_bindings.py",
    "scripts/b6_6_deadline.py",
    "scripts/b6_6_probe.py",
    "scripts/b6_6_receipt.py",
    "scripts/check_b6_6_window_plan.py",
    "scripts/pin_aws_lbc_digest.py",
    "scripts/run_b6_6_integration_window.sh",
    "scripts/terraform_medzen.sh",
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
    if review.get("status") != "PASS" or not isinstance(review.get("reviewer"), str):
        raise BindingRefusal("independent B6.6 review is absent")
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
