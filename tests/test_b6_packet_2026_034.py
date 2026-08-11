from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-034-remaining-proofs.md"
SCAN = ROOT / "platform/evidence/B6-PACKET-2026-033-SCAN-RESULT.json"
FILE_PROOF = ROOT / "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"
COLD = ROOT / "platform/evidence/receipts/B6-2026-034-COLD/cold_rehearsal.json"
DIGEST = "sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_binds_exact_scan_passed_child_and_preserved_file_proof():
    packet = PACKET.read_text()
    scan = json.loads(SCAN.read_bytes())
    assert scan["outcome"] == "PASS_SCAN_ONLY"
    assert scan["subject"]["child_manifest_digest"] == DIGEST
    assert scan["authoritative_scan"]["finding_count"] == 0
    assert _sha(SCAN) in packet
    assert _sha(FILE_PROOF) in packet
    assert DIGEST in packet
    assert "The file proof is not rerun" in packet


def test_packet_requests_only_two_bounded_remaining_proof_attempts():
    packet = PACKET.read_text()
    normalized = " ".join(packet.split())
    assert "Approve B6 AWS change packet 2026-034 only" in packet
    assert "two non-transferable 4,500-second attempts" in normalized
    assert "Existing active B6 reservation | `$10.00`" in packet
    assert "New reservation | `$0.00`" in packet
    assert "Maximum requested worker seconds | `9,000`" in packet
    assert "A PASS on attempt 1 terminates the packet" in normalized
    assert "file_proof` is deliberately absent" in packet
    for proof in (
        "websocket_proof",
        "cancellation_proof",
        "failure_drills",
        "isolation_proof",
    ):
        assert proof in packet


def test_cold_receipt_has_full_pass_all_refusals_and_dependency_injection():
    receipt = json.loads(COLD.read_bytes())
    payload = receipt["payload"]
    assert receipt["status"] == "PASS"
    assert payload["status"] == "PASS_COLD_REHEARSAL"
    assert payload["full_pass_runs"] == 1
    assert payload["injected_failure_runs"] == 23
    assert payload["dependency_unavailable_injections"] == 1
    assert payload["file_proof_receipts_created"] == 0
    assert payload["real_aws_calls"] == 0
    assert payload["real_kubectl_calls"] == 0
    assert payload["immutable_reuse_and_digest_audit"][
        "orchestrator_child_manifest_digest"
    ] == DIGEST
