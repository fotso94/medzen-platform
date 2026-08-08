from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.b6a_003c_c_deadline import _validate_authorization as validate_deadline_auth
from scripts.run_b6a_003c_c_proof import _authorization as validate_proof_auth


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/B6A-AWS-AUTH-2026-003C-C-stable-readiness-retry.json"
PACKET = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-C-stable-readiness-retry.md"


def _record() -> dict:
    return json.loads(AUTH.read_text())


def test_003c_c_authorization_binds_exact_owner_approval_and_packet() -> None:
    record = _record()
    assert record["id"] == "B6A-AWS-AUTH-2026-003C-C"
    assert record["status"] == "owner-approved"
    assert "Approved: B6A AWS change packet 2026-003C-C only." in record["authorization_source"]
    assert record["packet"]["sha256"] == hashlib.sha256(PACKET.read_bytes()).hexdigest()


def test_003c_c_authorization_is_a_bounded_retry_without_repeating_setup() -> None:
    record = _record()
    scope = record["aws_scope"]
    assert scope["maximum_gpu_nodes"] == 1
    assert scope["maximum_window_seconds"] == 7140
    assert scope["maximum_retry_window_seconds"] == 7140
    assert scope["maximum_cumulative_b6a_seconds"] == 7200
    assert record["prerequisites"]["budget"]["new_reservation_created"] is False
    prohibited = " ".join(record["prohibited_operations"])
    assert "artifact_upload" in prohibited
    assert "terraform_apply" in prohibited
    assert "dra_change_reapply_delete_or_reinstall" in prohibited
    assert "green_bucket" in prohibited
    assert record["bound_resources"] == {
        "workload_render_sha256": "9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68",
        "synthetic_audio_sha256": "3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3",
    }


def test_003c_c_authorization_preserves_fail_closed_outcomes() -> None:
    assert _record()["permitted_outcomes"] == [
        "B6A_PLATFORM_PROOF_COMPLETE",
        "BLOCKED_DRA_STABLE_READINESS",
        "BLOCKED_PLATFORM_PROOF",
        "FAILED_CLOSED_EXECUTION",
    ]


def test_003c_c_authorization_is_accepted_by_exact_runtime_validators() -> None:
    packet_sha = hashlib.sha256(PACKET.read_bytes()).hexdigest()
    validate_deadline_auth(AUTH, packet_sha)
    record = _record()
    workload_sha = record["bound_resources"]["workload_render_sha256"]
    assert validate_proof_auth(AUTH, packet_sha, workload_sha)["id"] == (
        "B6A-AWS-AUTH-2026-003C-C"
    )
