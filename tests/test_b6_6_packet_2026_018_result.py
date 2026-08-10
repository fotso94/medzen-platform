from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result() -> dict:
    return json.loads(RESULT.read_bytes())


def test_result_is_bound_to_reviewed_packet_and_owner_authorization():
    value = result()
    for binding in ("packet", "authorization", "cost_registry"):
        item = value["governance_bindings"][binding]
        assert sha256(ROOT / item["path"]) == item["sha256"]
    assert value["governance_bindings"]["independent_review"]["reviewed_repository_commit"] == (
        "9b87f7fcf0752956734d3d3ad0a5a57cc3ed4d9f"
    )


def test_all_persisted_credential_receipts_are_hash_exact_and_ordered():
    receipts = result()["credential_stage_zero"]["receipts"]
    assert [item["stage"] for item in receipts] == [
        "preflight",
        "restore",
        "terraform_import",
        "terraform_normalization",
        "terraform_reconciliation",
        "rotation",
        "cleanup",
    ]
    assert [item["recorded_utc"] for item in receipts] == sorted(
        item["recorded_utc"] for item in receipts
    )
    for item in receipts:
        receipt = ROOT / item["path"]
        assert sha256(receipt) == item["sha256"]
        assert json.loads(receipt.read_bytes())["status"] == item["status"]


def test_verification_refusal_receipt_gap_is_disclosed_not_reconstructed():
    value = result()["verification_refusal"]
    assert value["process_exit_code"] == 2
    assert value["terminal_signal"] == {
        "status": "REFUSED",
        "reason_code": "RestorationRefusal",
    }
    assert value["verification_receipt_persisted"] is False
    assert not (
        ROOT / "platform/evidence/receipts/B6-2026-018-LIVE/credential/verification.json"
    ).exists()
    assert value["receipt_gap_classification"] == (
        "CONTROL_DEFECT_DISCLOSED_NO_RETROACTIVE_RECEIPT_CREATED"
    )


def test_root_cause_records_three_legacy_versions_against_two_fixed_indexes():
    value = result()
    diagnosis = value["read_only_diagnosis"]
    assert diagnosis["classification"] == "PROVEN_LEGACY_VERSION_CARDINALITY_ADAPTER_DEFECT"
    authorization = json.loads(
        (ROOT / value["governance_bindings"]["authorization"]["path"]).read_bytes()
    )
    assert authorization["source_bindings"][
        "scripts/b6_6_successor_credential_stage.py"
    ] == "451b18a39cad59c3ce665bdc2bcdcbb1c7a3aa01a5ac25a54259801dd0fd5dee"
    assert authorization["source_bindings"][
        "scripts/b6_6_images_before_endpoints_credential_stage.py"
    ] == "a72d1187011da126f8df02a68a9360e677471ca1ed82d2b398bb01e8424c39f0"


def test_zero_state_and_budget_refuse_a_retry():
    value = result()
    zero = value["zero_state_verification"]
    assert zero["verified_after_cleanup"] is True
    assert zero["cpu_nodegroup"]["desired"] == zero["gpu_nodegroup"]["desired"] == 0
    assert zero["kubernetes_worker_nodes"] == zero["synthetic_pods"] == 0
    assert zero["window_vpc_endpoints"] == zero["deadline_actions_remaining"] == 0
    budget = value["budget_control"]
    assert budget["worker_window_seconds_charged"] == 0
    assert budget["remaining_window_seconds"] == 6852
    assert budget["retry_permitted"] is False


def test_b6_remains_incomplete_and_packet_018_is_closed():
    value = result()
    assert value["project_state"]["b6_6_complete"] is False
    assert value["project_state"]["synthetic_conversation_proof_completed"] is False
    assert "No retry is authorized" in value["next_boundary"]
    assert len(value["stages_not_run"]) == 22
