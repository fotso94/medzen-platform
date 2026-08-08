from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-E-proven-sampler-full-proof.md"
)
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-007.json"
DEBUG = ROOT / "platform/evidence/B6A-SAMPLER-DEBUG-2026-001-RESULT.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_003c_e_packet_is_unapproved_and_orders_review_before_owner_approval():
    text = PACKET.read_text()
    assert "Status: **BLOCKED — INDEPENDENT REVIEW AND OWNER APPROVAL REQUIRED**" in text
    review = text.index("An independent reviewer must first")
    approval = text.index("Approve B6A AWS change packet 2026-003C-E only.")
    assert review < approval
    assert "This packet is not approved for execution" in text


def test_003c_e_packet_binds_debug_evidence_local_evidence_and_proven_sampler():
    text = PACKET.read_text()
    assert sha(DEBUG) in text
    assert sha(EVIDENCE) in text
    sampler = ROOT / "scripts/b6a_003c_e_ssm_sampler.sh"
    assert sha(sampler) == "b6aa0e0621fca7fc6ee9e9a2bb9f59ff543efbb71b06a35e5497919d8a573d96"
    assert sha(sampler) in text
    assert "120 numeric samples" in text


def test_003c_e_local_evidence_binds_all_executable_sources_and_preserves_history():
    record = json.loads(EVIDENCE.read_bytes())
    for relative, expected in record["source_bindings"].items():
        assert sha(ROOT / relative) == expected
    for relative, expected in record["historical_immutability"].items():
        if relative.endswith("changed"):
            continue
        assert sha(ROOT / relative) == expected
    assert record["historical_immutability"][
        "historical_003c_d_sources_or_receipts_changed"
    ] is False


def test_003c_e_packet_orders_deadline_sampler_transcription_memory_and_cleanup():
    text = PACKET.read_text()
    deadline = text.index("arm and read back")
    scale = text.index("Scale only the GPU node group")
    sampler = text.index("run the proven sampler")
    transcription = text.index("Persist and fsync `transcription: PASS` immediately")
    memory = text.index("Only after that receipt exists")
    cleanup = text.index("On every outcome")
    assert deadline < scale < sampler < transcription < memory < cleanup
    assert "cannot void or\n  downgrade the successful transcription receipt" in text


def test_003c_e_packet_binds_remaining_budget_and_prohibits_promotion():
    text = PACKET.read_text()
    assert "Maximum remaining allowance: `5,109` seconds" in text
    assert "003C-E maximum GPU estimate: `$1.4285`" in text
    assert "GPU desired is zero before and after" in text
    assert "approved-ASR write" in text
    assert "production SSM/serving change" in text
    assert "B5 remains `BLOCKED`" in text
