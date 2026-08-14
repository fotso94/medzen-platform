from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_runner import validate_authorization_payload


AUTH = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-002S.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002S-attempt-20.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002S.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002S-COLD/cold-rehearsal.json"
RISK_SHA = "06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def authorization() -> dict:
    return json.loads(AUTH.read_bytes())


def test_authorization_schema_binds_exact_attempt_packet_and_risk() -> None:
    value = authorization()
    result = validate_authorization_payload(
        value,
        expected_id="ASR-BASE-MODEL-AWS-AUTH-2026-002S",
        packet_sha256=sha(PACKET),
        risk_sha256=RISK_SHA,
        attempt=20,
    )
    assert result == {
        "status": "PASS_AUTHORIZATION_SCHEMA",
        "attempt": 20,
        "authorized_numbers": [20],
        "seconds_each": 10800,
        "non_transferable": True,
    }


def test_owner_phrase_and_review_transmission_are_exact() -> None:
    value = authorization()
    assert value["owner_approval"]["exact_phrase"] == (
        "Approve ASR base-model AWS change packet 2026-002S only, authorizing "
        "numbered attempt 20 for one non-transferable 10,800-second offline "
        "evaluation attempt within a fresh $10 ceiling and continuing "
        "ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 "
        f"{RISK_SHA}."
    )
    assert value["independent_review"]["review_id"] == (
        "CLAUDE-REVIEW-2026-08-14-003"
    )
    assert value["owner_approval"]["transmitted_utc"] == "2026-08-14T05:50:00Z"


def test_reviewed_artifacts_and_40_gib_capacity_are_immutable() -> None:
    value = authorization()
    immutable = value["immutable_bindings"]
    assert immutable["bindings_manifest"]["sha256"] == sha(BINDINGS)
    assert immutable["cold_rehearsal"]["sha256"] == sha(COLD)
    assert immutable["gpu_storage_capacity_qualification"] == {
        "path": "platform/evidence/ASR-EVAL-RUNTIME-GPU-EPHEMERAL-STORAGE-QUALIFICATION-2026-001.json",
        "sha256": "1c2723537b9d157ee19dc3ad8aad5db55565b349b3db166bf18655902362a6d6",
        "calculated_minimum_gib": 29,
        "operational_floor_gib": 40,
    }
    assert immutable["gpu_storage_apply_evidence"]["live_root_volume_gib"] == 40


def test_allowance_arithmetic_and_history_boundary_are_exact() -> None:
    allowance = authorization()["allowance"]
    ceiling = Decimal(str(allowance["aggregate_project_ceiling_usd"]))
    committed = Decimal(str(allowance["recognized_committed_guardrail_usd"]))
    request = Decimal(str(allowance["fresh_reservation_usd"]))
    assert ceiling - committed == Decimal(
        str(allowance["guardrail_headroom_before_new_reservation_usd"])
    )
    assert ceiling - committed - request == Decimal(
        str(allowance["guardrail_headroom_after_new_reservation_usd"])
    )
    assert allowance["attempts_1_through_19_reuse_permitted"] is False
    assert allowance["attempt_20_non_transferable"] is True
