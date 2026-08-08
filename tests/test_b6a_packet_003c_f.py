from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-F-ssm-discovery-full-proof.md"
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-008.json"
RESULT_E = ROOT / "platform/evidence/B6A-PACKET-2026-003C-E-BLOCKED-SSM-INVOCATION.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_003c_f_packet_is_unapproved_and_orders_review_before_owner_approval():
    text = PACKET.read_text()
    assert "Status: **BLOCKED — INDEPENDENT REVIEW AND OWNER APPROVAL REQUIRED**" in text
    assert text.index("independent reviewer must first") < text.index(
        "Approve B6A AWS change packet 2026-003C-F only."
    )
    assert "This packet is not approved for execution" in text


def test_003c_f_packet_binds_result_local_evidence_and_every_executable_source():
    text = PACKET.read_text()
    assert sha(RESULT_E) in text
    assert sha(EVIDENCE) in text
    record = json.loads(EVIDENCE.read_bytes())
    for relative, expected in record["source_bindings"].items():
        assert sha(ROOT / relative) == expected
        assert expected in text


def test_003c_f_packet_binds_bounded_discovery_and_command_identity():
    text = PACKET.read_text()
    assert "at most 60 polls at\n  three-second intervals" in text
    assert "preserve the command ID in every terminal post-dispatch outcome" in text
    assert "fail closed on permanent discovery timeout" in text
    assert "repository root in `PYTHONPATH`" in text


def test_003c_f_packet_preserves_proof_order_cleanup_and_budget():
    text = PACKET.read_text()
    deadline = text.index("arm and read back")
    sampler = text.index("Send the proven sampler once")
    transcription = text.index("Persist and fsync `transcription: PASS` immediately")
    memory = text.index("Only after that receipt exists")
    cleanup = text.index("On every outcome")
    assert deadline < sampler < transcription < memory < cleanup
    assert "Maximum remaining allowance: `4,610` seconds" in text
    assert "003C-F maximum GPU estimate: `$1.2887`" in text
    assert "One GPU only" in text


def test_003c_f_packet_prohibits_promotion_and_keeps_b6_incomplete_boundary():
    text = PACKET.read_text()
    assert "does not pass B5" in text
    assert "approved-ASR" in text
    assert "production SSM" in text
    assert "complete full B6" in text
