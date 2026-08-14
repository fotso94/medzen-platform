from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-003.json"
PACKET = (
    ROOT
    / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-003-gpu-storage.md"
)


def test_authorization_binds_review_and_exact_owner_phrase():
    value = json.loads(AUTH.read_text())
    assert value["status"] == "AUTHORIZED"
    assert value["authorization_source"]["owner_exact_phrase"] == (
        "Approve ASR base-model AWS change packet 2026-003 only."
    )
    assert value["independent_review"]["review_id"] == (
        "CLAUDE-REVIEW-2026-08-14-002"
    )
    assert value["independent_review"]["decision"] == "APPROVED"
    assert value["packet"]["sha256"] == hashlib.sha256(PACKET.read_bytes()).hexdigest()


def test_authorization_is_storage_only_and_attempt_20_remains_prohibited():
    value = json.loads(AUTH.read_text())
    allowed = "\n".join(value["authorized_operations"])
    prohibited = "\n".join(value["prohibited_operations"])
    assert "20 GiB to 40 GiB" in allowed
    assert "min=0 desired=0 max=1" in allowed
    assert "Attempt 20" in prohibited
    assert value["failure_boundary"]["unreviewed_retry_or_recovery_permitted"] is False
