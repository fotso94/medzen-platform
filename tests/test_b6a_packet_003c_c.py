from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-C-stable-readiness-retry.md"
)
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-005.json"


def test_packet_003c_c_is_unapproved_and_binds_remediation_evidence():
    text = PACKET.read_text()
    assert "Status: **BLOCKED — NOT AUTHORIZED**" in text
    assert "Approve B6A AWS change packet 2026-003C-C only." in text
    assert hashlib.sha256(EVIDENCE.read_bytes()).hexdigest() in text


def test_packet_003c_c_does_not_repeat_completed_aws_phases():
    text = PACKET.read_text()
    normalized = " ".join(text.split())
    assert "does not repeat artifact publication" in text
    assert "Re-uploading the artifact" in text
    assert "applying Terraform" in text
    assert "DRA apply/delete/reinstall" in normalized
    assert "Artifact upload/overwrite" in text
    assert "Terraform apply" in text


def test_packet_003c_c_stable_gate_precedes_workload_and_is_diagnostic():
    text = PACKET.read_text()
    normalized = " ".join(text.split())
    stable = text.index("**Require stable DRA readiness before workload.**")
    diagnostic = text.index("**Persist diagnostics before cleanup.**")
    proof = text.index("**Run the unchanged proof only after stable DRA.**")
    cleanup = text.index("**Clean up on every outcome.**")
    assert stable < diagnostic < proof < cleanup
    assert "three consecutive reads" in text
    assert "same Pod UID, node and ResourceSlice fingerprint" in normalized
    assert "B6A_003C_C_NO_PHI_V1" in text


def test_packet_003c_c_preserves_cumulative_budget_and_deadline():
    text = PACKET.read_text()
    assert "New maximum window: `7,140` seconds" in text
    assert "Conservative cumulative B6A maximum: `7,200` seconds" in text
    assert "Existing reservation: `$15`; new reservation: `$0`" in text
    assert "medzen-b6a-003c-c-deadline-scale-zero" in text
    assert "more than 7,140 retry seconds" in text


def test_packet_003c_c_has_fail_closed_outcomes_and_no_promotion():
    text = PACKET.read_text()
    for outcome in (
        "B6A_PLATFORM_PROOF_COMPLETE",
        "BLOCKED_DRA_STABLE_READINESS",
        "BLOCKED_PLATFORM_PROOF",
        "FAILED_CLOSED_EXECUTION",
    ):
        assert outcome in text
    assert "B5 remains `BLOCKED`" in text
    assert "approved-ASR write" in text
    assert "production SSM change" in text
    assert "B6.1 or full B6" in text
