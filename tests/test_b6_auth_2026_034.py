from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/B6-AWS-AUTH-2026-034-window.json"
PACKET_SHA256 = "356a45b74a6060686fcafaad7fe595ea95298803a499a1a974804731efc825e2"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_authorization_is_exactly_owner_and_review_bound():
    value = json.loads(AUTH.read_bytes())
    assert value["status"] == "owner-approved"
    assert value["packet"] == {
        "id": "B6-AWS-CHANGE-PACKET-2026-034",
        "sha256": PACKET_SHA256,
    }
    assert value["prepared_repository_commit"] == (
        "20fa2d874d9842040f3d0de7c593a9ecb19c6010"
    )
    assert value["independent_review"]["status"] == "PASS"
    assert value["independent_review"]["findings"] == 0
    assert value["owner_approval"]["exact_phrase"] == (
        "Approve B6 AWS change packet 2026-034 only, including two "
        "non-transferable 4,500-second attempts within the existing $10 "
        "reservation."
    )


def test_authorization_source_bindings_and_runtime_validator_pass():
    from scripts.b6_remaining_bindings import REQUIRED_SOURCES, validate

    value = json.loads(AUTH.read_bytes())
    assert set(value["source_bindings"]) == REQUIRED_SOURCES
    # B6v2 round 4: the CLOSED window's bindings attest the bytes at its
    # prepared_repository_commit; sources reviewed and changed since then
    # verify at that commit — the validator below applies exactly that
    # rule, so the raw working-tree loop is gone (it froze bound sources
    # forever).
    validated = validate(AUTH, PACKET_SHA256, ROOT)
    assert validated["allowance"]["requested_attempts"] == 2
    assert validated["allowance"]["maximum_requested_worker_seconds"] == 9000


def test_authorization_does_not_expand_production_or_model_scope():
    value = json.loads(AUTH.read_bytes())
    assert value["proof_scope"] == {
        "preserved_not_rerun": ["file_proof"],
        "remaining_live_proofs": [
            "websocket_proof",
            "cancellation_proof",
            "failure_drills",
            "isolation_proof",
        ],
        "production_traffic": False,
        "synthetic_only": True,
    }
    prohibited = " ".join(value["prohibited_operations"])
    assert "Production SSM" in prohibited
    assert "approved/asr" in prohibited
    assert "third attempt" in prohibited
