from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003-deployment.md"
AMENDMENT = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003A-deployment.md"


def test_unapproved_original_packet_remains_unchanged():
    assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == (
        "e5af50e5bded83ec2feffa682481a76fc706b8aee0a3fea7c56ab33341192da1"
    )


def test_amendment_stays_blocked_and_does_not_extend_a_waiver():
    text = AMENDMENT.read_text()
    assert "BLOCKED — NOT AUTHORIZED" in text
    assert "PACKET_2026_004_STANDARD_SUPPORT" in text
    assert "EXPLICIT_OWNER_APPROVAL_OF_2026_003A" in text
    assert "No serving-plane vulnerability waiver is used" in text
    assert "0 critical / 0 high" in text
    assert "GPU desired size: `0` before and after" in text


def test_amendment_preserves_non_promotion_boundaries():
    text = AMENDMENT.read_text()
    for required in (
        "Approved ASR writes: `0`",
        "Registered models/model versions: `0 / 0`",
        "Production SSM changes: `0`",
        "B5 BLOCKED report: unchanged",
        "not B6.1 or full B6",
    ):
        assert required in text
