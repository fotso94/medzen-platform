#!/usr/bin/env python3
"""Validate exact review, packet and source bindings for B6 packet 2026-015."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-015"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-015"
MANIFEST_PATH = "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-003.json"
REQUIRED_SOURCES = {
    "infra/b6_client_secret.tf",
    "infra/variables.tf",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-012A-import-state-normalization-continuation.md",
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-014-b6-6-private-probe-successor.md",
    "platform/evidence/B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json",
    "platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json",
    "platform/finance/COST-REGISTRY-2026-004.json",
    MANIFEST_PATH,
    "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-002.json",
    "scripts/b6_client_secret_restoration_2026_015_bindings.py",
    "scripts/check_b6_client_secret_restoration_2026_015_plan.py",
    "scripts/check_b6_client_secret_restoration_plan.py",
    "scripts/run_b6_client_secret_restoration_2026_015.py",
    "scripts/run_b6_client_secret_restoration_2026_015.sh",
    "scripts/terraform_medzen.sh",
    "tests/test_b6_client_secret_restoration_2026_015.py",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("owner authorization is absent or malformed") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("exact owner authorization is absent")
    packet = value.get("packet", {})
    if packet.get("id") != PACKET_ID:
        raise BindingRefusal("packet identity differs")
    packet_path = packet.get("path")
    packet_sha = packet.get("sha256")
    if (
        not isinstance(packet_path, str)
        or packet_path.startswith("/")
        or ".." in Path(packet_path).parts
        or re.fullmatch(r"[0-9a-f]{64}", str(packet_sha)) is None
        or not (root / packet_path).is_file()
        or sha256_file(root / packet_path) != packet_sha
    ):
        raise BindingRefusal("packet hash binding differs")
    manifest = value.get("request_manifest", {})
    if manifest != {
        "path": MANIFEST_PATH,
        "sha256": sha256_file(root / MANIFEST_PATH),
    }:
        raise BindingRefusal("request-manifest binding differs")
    review = value.get("independent_review", {})
    if (
        review.get("status") != "PASS"
        or not isinstance(review.get("reviewer"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(review.get("reviewed_packet_sha256")))
        is None
        or review.get("reviewed_packet_sha256") != packet_sha
    ):
        raise BindingRefusal("independent review binding differs")
    if value.get("cost") != {
        "registry_id": "COST-REGISTRY-2026-004",
        "allocation_id": "B6-INTEGRATION-WINDOW-2026-001",
        "maximum_incremental_usd": 0.1,
        "new_reservation_usd": 0.0,
    }:
        raise BindingRefusal("cost binding differs")
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("unsafe source path")
        target = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise BindingRefusal(f"source hash differs: {relative}")
    return value
