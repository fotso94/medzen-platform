#!/usr/bin/env python3
"""Fail-closed source and authorization bindings for packet 2026-034."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTH_ID = "B6-AWS-AUTH-2026-034"
PACKET_ID = "B6-AWS-CHANGE-PACKET-2026-034"
COLD_PATH = "platform/evidence/receipts/B6-2026-034-COLD/cold_rehearsal.json"
PACKET_PATH = (
    "platform/decisions/B6-AWS-CHANGE-PACKET-2026-034-remaining-proofs.md"
)
SCAN_RESULT_PATH = "platform/evidence/B6-PACKET-2026-033-SCAN-RESULT.json"
FILE_RECEIPT_PATH = "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"
PRIOR_REFUSAL_PATH = (
    "platform/evidence/"
    "B6-PACKET-2026-032A-ATTEMPT-2-TERMINAL-DEPENDENCY-REFUSAL.json"
)
LOCAL_QUALIFICATION_PATH = (
    "platform/evidence/"
    "B6-WEBSOCKET-PARTIAL-SOURCE-LOCAL-QUALIFICATION-2026-001.json"
)
LOCAL_RUNTIME_PATH = (
    "platform/evidence/b6-websocket-runtime/"
    "medzen-orchestrator.partial-source-successor.runtime.json"
)
NEW_ORCHESTRATOR_DIGEST = (
    "sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3"
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
    PRIOR_REFUSAL_PATH,
    LOCAL_QUALIFICATION_PATH,
    LOCAL_RUNTIME_PATH,
    "infra/b6_integration_window.tf",
    "pipeline/b6_integration_receipts.py",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a.json",
    "platform/evidence/receipts/B6-2026-026-STAGE-A-LIVE/stage_a_cleanup.json",
    "platform/finance/COST-REGISTRY-2026-005.json",
    "platform/decisions/B6-WEBSOCKET-PARTIAL-SOURCE-REMEDIATION-2026-001.json",
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
    "tests/test_b6_packet_2026_034.py",
    "tests/test_b6_remaining_proofs_window.py",
}


class BindingRefusal(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_immutable_evidence(value: dict[str, Any], root: Path) -> None:
    evidence = value.get("immutable_evidence")
    expected = {
        "packet_2026_033_scan_result": {
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
        "packet_2026_032a_dependency_refusal": {
            "path": PRIOR_REFUSAL_PATH,
            "sha256": sha256_file(root / PRIOR_REFUSAL_PATH),
            "status": "REFUSED_NO_ATTEMPTS_REMAINING_PENDING_DEPENDENCY_DIAGNOSIS",
            "failure_stage": "websocket_proof",
            "close_code": 4503,
            "cleanup": "PASS",
        },
        "local_partial_source_qualification": {
            "path": LOCAL_QUALIFICATION_PATH,
            "sha256": sha256_file(root / LOCAL_QUALIFICATION_PATH),
            "status": "PASS_LOCAL_ECR_SCAN_NOT_AUTHORIZED",
            "dependency_unavailable_reason": (
                "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
            ),
            "stable_full_conversation_passes": 3,
            "probe_app_pair_sha256": (
                "f6c8eb872cbd80c5542350e0c4ac5c0b1cff82d820d94ab452ef12cba816a9d6"
            ),
        },
    }
    if evidence != expected:
        raise BindingRefusal("packet-2026-034 immutable evidence binding differs")
    scan = json.loads((root / SCAN_RESULT_PATH).read_bytes())
    file_receipt = json.loads((root / FILE_RECEIPT_PATH).read_bytes())
    prior_refusal = json.loads((root / PRIOR_REFUSAL_PATH).read_bytes())
    qualification = json.loads((root / LOCAL_QUALIFICATION_PATH).read_bytes())
    runtime = json.loads((root / LOCAL_RUNTIME_PATH).read_bytes())
    unavailable = qualification.get("runtime_qualification", {}).get(
        "deliberately_missing_source", {}
    )
    aligned = qualification.get("runtime_qualification", {}).get(
        "aligned_source", {}
    )
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
        or prior_refusal.get("execution", {}).get("failure_stage")
        != "websocket_proof"
        or prior_refusal.get("execution", {}).get("cleanup") != "PASS"
        or prior_refusal.get("allowance", {}).get("packet_attempts_remaining") != 0
        or qualification.get("status") != "PASS_LOCAL_ECR_SCAN_NOT_AUTHORIZED"
        or unavailable.get("status") != "PASS_FAIL_CLOSED"
        or unavailable.get("readyz_status") != 503
        or unavailable.get("reason_code") != "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
        or aligned.get("status") != "PASS"
        or aligned.get("stable_full_conversation_passes") != 3
        or aligned.get("final_result_preserved") is not True
        or aligned.get("probe_app_pair_sha256")
        != "f6c8eb872cbd80c5542350e0c4ac5c0b1cff82d820d94ab452ef12cba816a9d6"
        or runtime.get("websocket_dependency_gate", {}).get("reason_code")
        != "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
        or runtime.get("websocket_conversation", {}).get(
            "stable_conversation_passes"
        )
        != 3
    ):
        raise BindingRefusal("packet-2026-034 predecessor evidence refuses")


def validate(path: Path, packet_sha256: str, root: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise BindingRefusal("exact packet-2026-034 SHA-256 is required")
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise BindingRefusal("packet-2026-034 authorization is absent") from exc
    if value.get("id") != AUTH_ID or value.get("status") != "owner-approved":
        raise BindingRefusal("packet 2026-034 is not owner-approved")
    if value.get("packet") != {"id": PACKET_ID, "sha256": packet_sha256}:
        raise BindingRefusal("packet-2026-034 binding differs")
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
        raise BindingRefusal("independent packet-2026-034 review is absent")
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
        raise BindingRefusal("packet-2026-034 allowance binding differs")
    if value.get("proof_scope") != {
        "preserved_not_rerun": ["file_proof"],
        "remaining_live_proofs": list(REMAINING_PROOFS),
        "production_traffic": False,
        "synthetic_only": True,
    }:
        raise BindingRefusal("packet-2026-034 proof scope differs")
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
        raise BindingRefusal("packet-2026-034 Stage A reuse binding differs")
    _validate_immutable_evidence(value, root)
    sources = value.get("source_bindings")
    if not isinstance(sources, dict) or set(sources) != REQUIRED_SOURCES:
        raise BindingRefusal("packet-2026-034 source binding set differs")
    for relative, expected in sorted(sources.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise BindingRefusal("packet-2026-034 source path is unsafe")
        target = root / relative
        if re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None:
            raise BindingRefusal(f"packet-2026-034 source hash differs: {relative}")
        if target.is_file() and sha256_file(target) == expected:
            continue
        # B6v2 round 4 (Codex serving review): this CLOSED window's record
        # attests what was authorized AT prepared_repository_commit — it is
        # not a freeze on future reviewed work. A working-tree difference
        # is fine exactly when the recorded hash matches the bytes at that
        # commit (same at-commit discipline as loader_v2's promotion gate).
        import hashlib
        import subprocess
        shown = subprocess.run(
            ["git", "-C", str(root), "show",
             f"{value['prepared_repository_commit']}:{relative}"],
            capture_output=True,
        )
        if (shown.returncode != 0
                or hashlib.sha256(shown.stdout).hexdigest() != expected):
            raise BindingRefusal(f"packet-2026-034 source hash differs: {relative}")
    return value
