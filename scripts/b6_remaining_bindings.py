#!/usr/bin/env python3
"""Fail-closed source and authorization bindings for packet 2026-032."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-032"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-032"
COLD_PATH = "platform/evidence/receipts/B6-2026-032-COLD/cold_rehearsal.json"
PACKET_PATH = "platform/decisions/B6-AWS-CHANGE-PACKET-2026-032-remaining-proofs.md"
SCAN_RESULT_PATH = "platform/evidence/B6-PACKET-2026-031-SCAN-RESULT.json"
FILE_RECEIPT_PATH = "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"
NEW_ORCHESTRATOR_DIGEST = (
    "sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed"
)
REMAINING_PROOFS = (
    "websocket_proof",
    "cancellation_proof",
    "failure_drills",
    "isolation_proof",
)
REQUIRED_SOURCES = {
    PACKET_PATH,
    SCAN_RESULT_PATH,
    FILE_RECEIPT_PATH,
    COLD_PATH,
    "infra/b6_integration_window.tf",
    "pipeline/b6_integration_receipts.py",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/finance/COST-REGISTRY-2026-005.json",
    "platform/k8s/b6-6/remaining-proofs-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
    "platform/runtime-receipt-policy-v3.yaml",
    "platform/testdata/b6a-003c-b-synthetic.wav",
    "registry/deployment/b6-v0-synthetic.json",
    "platform/generated/registry-ssm/b6-v0-synthetic.json",
    "scripts/b6_6_aws_read_fixtures.py",
    "scripts/b6_6_credential.py",
    "scripts/b6_6_deadline.py",
    "scripts/b6_6_fargate_probe.py",
    "scripts/b6_6_k8s_stability.py",
    "scripts/b6_6_lbc_runtime.py",
    "scripts/b6_6_lbc_tag_warning.py",
    "scripts/b6_6_post_mutation_audit.py",
    "scripts/b6_6_probe.py",
    "scripts/b6_6_probe_endpoints.py",
    "scripts/b6_6_proof_audio_binding.py",
    "scripts/b6_6_registry_rag_alignment.py",
    "scripts/b6_6_registry_readback.py",
    "scripts/b6_6_wait_workers.py",
    "scripts/b6_remaining_bindings.py",
    "scripts/b6_remaining_cleanup.sh",
    "scripts/b6_remaining_cold_rehearsal.py",
    "scripts/b6_remaining_manifest_slice.py",
    "scripts/b6_remaining_operations.sh",
    "scripts/b6_remaining_pre_endpoint_images.py",
    "scripts/b6_remaining_runner.py",
    "scripts/check_b6_6_window_plan.py",
    "scripts/terraform_medzen.sh",
    "tests/test_b6_remaining_proofs_window.py",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_immutable_evidence(value: dict[str, Any], root: Path) -> None:
    evidence = value.get("immutable_evidence")
    expected = {
        "packet_2026_031_scan_result": {
            "path": SCAN_RESULT_PATH,
            "sha256": sha256_file(root / SCAN_RESULT_PATH),
            "outcome": "PASS_SCAN_ONLY",
            "orchestrator_child_manifest_digest": NEW_ORCHESTRATOR_DIGEST,
        },
        "preserved_file_proof": {
            "path": FILE_RECEIPT_PATH,
            "sha256": sha256_file(root / FILE_RECEIPT_PATH),
            "status": "PASS",
            "rerun_permitted": False,
        },
    }
    if evidence != expected:
        raise BindingRefusal("packet-2026-032 immutable evidence binding differs")
    scan = json.loads((root / SCAN_RESULT_PATH).read_bytes())
    file_receipt = json.loads((root / FILE_RECEIPT_PATH).read_bytes())
    if (
        scan.get("outcome") != "PASS_SCAN_ONLY"
        or scan.get("subject", {}).get("child_manifest_digest")
        != NEW_ORCHESTRATOR_DIGEST
        or scan.get("authoritative_scan", {}).get("critical") != 0
        or scan.get("authoritative_scan", {}).get("high") != 0
        or scan.get("deployment_authorized_by_this_record") is not False
        or file_receipt.get("stage") != "file_proof"
        or file_receipt.get("status") != "PASS"
        or file_receipt.get("payload", {}).get("http_status") != 200
    ):
        raise BindingRefusal("packet-2026-032 predecessor evidence refuses")


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact packet-2026-032 SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("packet-2026-032 authorization is absent") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("packet 2026-032 is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("packet-2026-032 binding differs")
    review = value.get("independent_review", {})
    reviewed_commit = review.get("reviewed_repository_commit")
    if (
        review.get("status") != "PASS"
        or review.get("reviewed_packet_sha256") != packet_sha256
        or review.get("reviewed_cold_rehearsal_sha256")
        != sha256_file(root / COLD_PATH)
        or re.fullmatch(r"[0-9a-f]{40}", str(reviewed_commit)) is None
        or value.get("prepared_repository_commit") != reviewed_commit
    ):
        raise BindingRefusal("independent packet-2026-032 review is absent")
    if value.get("allowance") != {
        "aggregate_project_ceiling_usd": 300.0,
        "recognized_committed_guardrail_usd": 64.4286064216,
        "existing_reservation_usd": 10.0,
        "new_reservation_usd": 0.0,
        "requested_attempts": 2,
        "maximum_seconds_per_attempt": 4500,
        "maximum_requested_worker_seconds": 9000,
        "estimated_compute_usd": 3.2,
        "attempts_non_transferable": True,
        "pass_terminates_packet": True,
        "cold_rehearsal_required_before_each_attempt": True,
    }:
        raise BindingRefusal("packet-2026-032 allowance binding differs")
    if value.get("proof_scope") != {
        "preserved_not_rerun": ["file_proof"],
        "remaining_live_proofs": list(REMAINING_PROOFS),
        "production_traffic": False,
        "synthetic_only": True,
    }:
        raise BindingRefusal("packet-2026-032 proof scope differs")
    if value.get("stage_a_reuse") != {
        "source_packet": "B6-AWS-CHANGE-PACKET-2026-026",
        "aggregate_receipt_path": (
            "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json"
        ),
        "aggregate_receipt_sha256": sha256_file(
            root
            / "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json"
        ),
        "cleanup_receipt_path": (
            "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json"
        ),
        "cleanup_receipt_sha256": sha256_file(
            root
            / "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json"
        ),
        "stable_probe_passes": 3,
        "cleanup_complete": True,
        "rerun_permitted": False,
    }:
        raise BindingRefusal("packet-2026-032 Stage A reuse binding differs")
    _validate_immutable_evidence(value, root)
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("packet-2026-032 source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("packet-2026-032 source path is unsafe")
        target = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise BindingRefusal(f"packet-2026-032 source hash differs: {relative}")
    return value
