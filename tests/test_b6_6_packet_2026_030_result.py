from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT
    / "platform/evidence/"
    / "B6-PACKET-2026-030-ATTEMPT-1-REFUSED-PROBE-AUDIO-BINDING.json"
)
RECEIPTS = ROOT / "platform/evidence/receipts/B6-2026-030-A1-LIVE"


def _load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_all_attempt_one_receipts_are_bound_and_terminal() -> None:
    evidence = _load(EVIDENCE)
    assert evidence["status"] == "TERMINAL_REFUSED_ONE_ATTEMPT_ONE_LOCKED"
    assert evidence["attempt_1"]["stages_passed_before_refusal"] == 17
    assert set(evidence["receipt_hashes"]) == {
        path.stem for path in RECEIPTS.glob("*.json")
    }
    for stage, expected_hash in evidence["receipt_hashes"].items():
        path = RECEIPTS / f"{stage}.json"
        assert _sha256(path) == expected_hash
        assert _load(path)["stage"] == stage
    assert _load(RECEIPTS / "file_proof.json")["status"] == "REFUSED"
    assert _load(RECEIPTS / "cleanup.json")["status"] == "PASS"


def test_file_proof_refused_locally_before_any_conversation_request() -> None:
    evidence = _load(EVIDENCE)
    proof = _load(RECEIPTS / "file_proof.json")["payload"]["proof_refusal"]
    assert evidence["attempt_1"]["failure_stage"] == "file_proof"
    assert evidence["attempt_1"]["conversation_request_sent"] is False
    assert evidence["attempt_1"]["http_status"] is None
    assert proof == {
        "failed_assertion": "SYNTHETIC_WAV_SHA256_MATCHES",
        "http_status": None,
        "phi_present": False,
        "probe_exit_code": 10,
        "reason_code": "SYNTHETIC_PROOF_ASSERTION_REFUSED",
        "response_body_sha256": (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        ),
        "response_body_truncated": False,
        "safe_error_text": "synthetic WAV binding differs",
        "sanitized_response_body": "",
        "synthetic_only": True,
    }
    assert evidence["diagnosis"]["classification"] == (
        "PROBE_AUDIO_BINDING_SOURCE_DRIFT"
    )
    assert evidence["diagnosis"]["manifest_expected_audio_sha256"] == (
        evidence["diagnosis"]["selected_audio_file_sha256"]
    )
    assert evidence["diagnosis"]["probe_private_literal_sha256"] != (
        evidence["diagnosis"]["selected_audio_file_sha256"]
    )


def test_cleanup_is_exact_zero_and_one_attempt_remains_locked() -> None:
    evidence = _load(EVIDENCE)
    assert evidence["attempt_2"] == {
        "status": "LOCKED_NOT_EXECUTED",
        "authorized_seconds_remaining": 4500,
        "additional_reservation_required": False,
        "unchanged_retry_permitted": False,
        "continuity_condition": (
            "A narrow successor packet must bind the class fix, fresh cold "
            "rehearsal and this zero-state receipt, then receive independent "
            "review PASS and exact owner approval."
        ),
    }
    numeric_zero_fields = {
        "cpu_desired",
        "cpu_asg_instances",
        "gpu_desired",
        "gpu_asg_instances",
        "workload_nodes",
        "synthetic_pods",
        "window_ingresses",
        "window_deployments",
        "probe_vpc_endpoints",
        "endpoint_security_groups",
        "alb_count",
        "deadline_actions",
        "production_serving_pointer_count",
        "approved_asr_changes",
    }
    assert all(evidence["zero_state"][key] == 0 for key in numeric_zero_fields)
    assert evidence["zero_state"]["local_token_absent"] is True
    assert evidence["zero_state"]["persistent_synthetic_secret"] == (
        "RETAINED_OPERATOR_DENIED"
    )
    assert evidence["cost_and_allowance"]["attempts_consumed"] == 1
    assert evidence["cost_and_allowance"]["attempts_remaining_locked"] == 1
    assert evidence["cost_and_allowance"]["inside_existing_reservation"] is True


def test_attempt_did_not_mutate_prohibited_state() -> None:
    evidence = _load(EVIDENCE)
    assert all(value == 0 for value in evidence["prohibited_state_unchanged"].values())
