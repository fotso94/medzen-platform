from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "platform/evidence/"
    / "B6-PACKET-2026-030A-ATTEMPT-2-TERMINAL-WEBSOCKET-REFUSAL.json"
)
RECEIPTS = ROOT / "platform/evidence/receipts/B6-2026-030A-A2-LIVE"


def _load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_terminal_result_binds_every_live_receipt() -> None:
    evidence = _load(EVIDENCE)
    assert evidence["status"] == "TERMINAL_REFUSED_NO_ATTEMPTS_REMAIN"
    assert set(evidence["receipt_hashes"]) == {
        path.stem for path in RECEIPTS.glob("*.json")
    }
    for stage, expected in evidence["receipt_hashes"].items():
        path = RECEIPTS / f"{stage}.json"
        assert _sha256(path) == expected
        assert _load(path)["stage"] == stage


def test_file_conversation_passed_before_websocket_refused() -> None:
    evidence = _load(EVIDENCE)
    file_receipt = _load(RECEIPTS / "file_proof.json")
    websocket_receipt = _load(RECEIPTS / "websocket_proof.json")
    assert file_receipt["status"] == "PASS"
    assert file_receipt["payload"]["http_status"] == 200
    assert file_receipt["payload"]["citation_count"] == 3
    assert file_receipt["payload"]["tts_backend"] == "text_only"
    assert websocket_receipt["status"] == "REFUSED"
    assert websocket_receipt["recorded_utc"] > file_receipt["recorded_utc"]
    refusal = websocket_receipt["payload"]["proof_refusal"]
    assert refusal["failed_assertion"] == "WEBSOCKET_UPGRADE_STATUS_IS_101"
    assert refusal["http_status"] == 404
    assert refusal["probe_exit_code"] == 53
    assert refusal["synthetic_only"] is True
    assert refusal["phi_present"] is False
    assert evidence["refusal"]["root_cause"] == (
        "RUNTIME_IMAGE_MISSING_WEBSOCKET_PROTOCOL_BACKEND"
    )
    diagnosis = evidence["local_read_only_diagnosis"]
    assert diagnosis["status"] == "ROOT_CAUSE_CONFIRMED"
    assert diagnosis["websocket_route_type"] == "APIWebSocketRoute"
    assert diagnosis["websockets_package_present"] is False
    assert diagnosis["wsproto_package_present"] is False
    assert diagnosis["aws_calls"] == 0
    assert diagnosis["mutations"] == 0


def test_packet_has_no_attempts_remaining_and_b6_is_not_complete() -> None:
    evidence = _load(EVIDENCE)
    assert evidence["allowance"]["packet_attempts_authorized"] == 1
    assert evidence["allowance"]["packet_attempts_consumed"] == 1
    assert evidence["allowance"]["packet_attempts_remaining"] == 0
    assert evidence["allowance"]["additional_execution_authorized"] is False
    assert evidence["completion_semantics"]["b6_complete"] is False
    assert evidence["completion_semantics"]["fresh_owner_allowance_required"] is True
    assert evidence["stages_not_executed"] == [
        "cancellation_proof",
        "failure_drills",
        "isolation_proof",
    ]


def test_cleanup_is_pass_and_every_ephemeral_count_is_zero() -> None:
    evidence = _load(EVIDENCE)
    cleanup = _load(RECEIPTS / "cleanup.json")
    assert cleanup["status"] == "PASS"
    assert cleanup["recorded_utc"] == evidence["execution"]["cleanup_completed_utc"]
    numeric_zero_fields = {
        "cpu_desired",
        "cpu_asg_instances",
        "gpu_desired",
        "gpu_asg_instances",
        "workload_nodes",
        "synthetic_pods",
        "window_ingresses",
        "window_deployments",
        "window_terraform_resources",
        "probe_vpc_endpoints",
        "endpoint_security_groups",
        "alb_count",
        "deadline_actions",
        "production_serving_pointer_count",
        "approved_asr_objects",
    }
    assert all(evidence["zero_state"][key] == 0 for key in numeric_zero_fields)
    assert evidence["zero_state"]["local_token_absent"] is True
    assert evidence["zero_state"]["persistent_synthetic_secret"] == (
        "RETAINED_OPERATOR_DENIED"
    )
    assert all(value == 0 for value in evidence["prohibited_state_unchanged"].values())
