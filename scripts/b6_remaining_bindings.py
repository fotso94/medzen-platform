#!/usr/bin/env python3
"""Fail-closed source and authorization bindings for packet 2026-032A."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-032A"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-032A"
COLD_PATH = "platform/evidence/receipts/B6-2026-032A-COLD/cold_rehearsal.json"
PACKET_PATH = (
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-032A-websocket-local-qualified.md"
)
SCAN_RESULT_PATH = "platform/evidence/B6-PACKET-2026-031-SCAN-RESULT.json"
FILE_RECEIPT_PATH = "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"
ATTEMPT_1_RESULT_PATH = (
    "platform/evidence/"
    "B6-PACKET-2026-032-ATTEMPT-1-TERMINAL-WEBSOCKET-FRAME-REFUSAL.json"
)
LOCAL_CONVERSATION_PATH = (
    "platform/evidence/b6-websocket-runtime/"
    "medzen-orchestrator.full-conversation.json"
)
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
    ATTEMPT_1_RESULT_PATH,
    LOCAL_CONVERSATION_PATH,
    "infra/b6_integration_window.tf",
    "pipeline/b6_integration_receipts.py",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/finance/COST-REGISTRY-2026-005.json",
    "platform/decisions/B6-WEBSOCKET-CONVERSATION-QUALIFICATION-2026-001.json",
    "platform/k8s/b6-6/remaining-proofs-window.yaml",
    "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml",
    "platform/runtime-receipt-policy-v3.yaml",
    "platform/standards/runtime-image-hardening-v2.md",
    "platform/testdata/b6a-003c-b-synthetic.wav",
    "platform/testdata/orchestrator/b6-window-asr-fixture.json",
    "platform/testdata/registry-ssm/b6-window-websocket-v1.json",
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
    "scripts/check_b6_service_image.py",
    "scripts/check_b6_6_window_plan.py",
    "scripts/generate_b6_websocket_qualification_fixtures.py",
    "scripts/terraform_medzen.sh",
    "services/speech-orchestrator/medzen_speech_orchestrator/streaming_app.py",
    "tests/test_b6_6_proof_diagnostics.py",
    "tests/test_b6_6_websocket_full_local_qualification.py",
    "tests/test_b6_orchestrator_websocket_image_qualification.py",
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
        "packet_2026_032_attempt_1_refusal": {
            "path": ATTEMPT_1_RESULT_PATH,
            "sha256": sha256_file(root / ATTEMPT_1_RESULT_PATH),
            "status": (
                "REFUSED_ATTEMPT_2_LOCKED_PENDING_LOCAL_CONVERSATION_QUALIFICATION"
            ),
            "failure_stage": "websocket_proof",
            "cleanup": "PASS",
        },
        "local_full_websocket_conversation": {
            "path": LOCAL_CONVERSATION_PATH,
            "sha256": sha256_file(root / LOCAL_CONVERSATION_PATH),
            "status": "PASS",
            "probe_app_pair_sha256": (
                "e68098b4d3b1722bb37c0851be770bcf51bf656a24476c264f141a5361866a9b"
            ),
        },
    }
    if evidence != expected:
        raise BindingRefusal("packet-2026-032A immutable evidence binding differs")
    scan = json.loads((root / SCAN_RESULT_PATH).read_bytes())
    file_receipt = json.loads((root / FILE_RECEIPT_PATH).read_bytes())
    attempt_1 = json.loads((root / ATTEMPT_1_RESULT_PATH).read_bytes())
    local_conversation = json.loads((root / LOCAL_CONVERSATION_PATH).read_bytes())
    conversation = local_conversation.get("websocket_conversation", {})
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
        or attempt_1.get("execution", {}).get("failure_stage")
        != "websocket_proof"
        or attempt_1.get("execution", {}).get("cleanup") != "PASS"
        or attempt_1.get("allowance", {}).get("packet_attempts_remaining") != 1
        or local_conversation.get("status") != "PASS"
        or conversation.get("status") != "PASS"
        or conversation.get("final_result_preserved") is not True
        or conversation.get("probe_app_binding", {}).get("pair_sha256")
        != "e68098b4d3b1722bb37c0851be770bcf51bf656a24476c264f141a5361866a9b"
    ):
        raise BindingRefusal("packet-2026-032A predecessor evidence refuses")


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact packet-2026-032A SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("packet-2026-032A authorization is absent") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("packet 2026-032A is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("packet-2026-032A binding differs")
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
        raise BindingRefusal("independent packet-2026-032A review is absent")
    if value.get("allowance") != {
        "aggregate_project_ceiling_usd": 300.0,
        "recognized_committed_guardrail_usd": 64.4286064216,
        "existing_reservation_usd": 10.0,
        "new_reservation_usd": 0.0,
        "original_packet_attempts_authorized": 2,
        "original_packet_attempts_consumed": 1,
        "continuity_attempt_number": 2,
        "requested_attempts": 1,
        "maximum_seconds_per_attempt": 4500,
        "maximum_requested_worker_seconds": 4500,
        "estimated_compute_usd": 1.6,
        "attempts_non_transferable": True,
        "pass_terminates_packet": True,
        "cold_rehearsal_required_before_each_attempt": True,
    }:
        raise BindingRefusal("packet-2026-032A allowance binding differs")
    if value.get("proof_scope") != {
        "preserved_not_rerun": ["file_proof"],
        "remaining_live_proofs": list(REMAINING_PROOFS),
        "production_traffic": False,
        "synthetic_only": True,
    }:
        raise BindingRefusal("packet-2026-032A proof scope differs")
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
        raise BindingRefusal("packet-2026-032A Stage A reuse binding differs")
    _validate_immutable_evidence(value, root)
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("packet-2026-032A source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("packet-2026-032A source path is unsafe")
        target = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not target.is_file()
            or sha256_file(target) != expected
        ):
            raise BindingRefusal(f"packet-2026-032A source hash differs: {relative}")
    return value
