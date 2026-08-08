from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT / "platform/evidence/B6A-PACKET-2026-003B-FAILED-IMAGE-SCAN.json"
)
AUTH = ROOT / "platform/decisions/B6A-AWS-AUTH-2026-003B-deployment.json"
PACKET = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003B-deployment.md"


def _evidence():
    return json.loads(EVIDENCE.read_text())


def test_failure_is_bound_to_authorization_and_packet():
    evidence = _evidence()
    assert evidence["status"] == "FAILED_CLOSED"
    assert evidence["authorization"]["sha256"] == hashlib.sha256(
        AUTH.read_bytes()
    ).hexdigest()
    assert evidence["packet"]["sha256"] == hashlib.sha256(
        PACKET.read_bytes()
    ).hexdigest()


def test_model_loader_passed_but_asr_authoritative_gate_failed():
    evidence = _evidence()
    loader = evidence["sequential_ecr_gates"]["model_loader"]
    asr = evidence["sequential_ecr_gates"]["asr_runtime"]
    assert loader["automatic_scan_on_push"] is True
    assert loader["scan_status"] == "COMPLETE"
    assert loader["critical"] == loader["high"] == 0
    assert loader["gate"] == "PASS"
    assert asr["automatic_scan_on_push"] is True
    assert asr["scan_status"] == "COMPLETE"
    assert asr["critical"] == 0
    assert asr["high"] == 4
    assert asr["gate"] == "FAIL"
    assert evidence["scanner_discrepancy"] == {
        "local_docker_scout_high": 0,
        "authoritative_ecr_basic_scan_high": 4,
        "interpretation": (
            "The packet correctly treats the automatic ECR scan as authoritative. "
            "The local result cannot override the remote failure."
        ),
    }


def test_stop_prevented_all_later_packet_phases():
    evidence = _evidence()
    stop = evidence["stop_decision"]
    assert stop["sequence_stopped_before_nvidia_dra_push"] is True
    assert stop["security_waiver_used"] is False
    assert stop["artifact_upload_attempted"] is False
    assert stop["identity_apply_attempted"] is False
    assert stop["deployment_attempted"] is False
    assert stop["gpu_window_opened"] is False
    assert evidence["post_stop_verification"]["gpu_nodegroup"]["desired"] == 0
    assert evidence["post_stop_verification"]["approved_asr_writes"] == 0


def test_current_packet_cannot_resume_and_no_waiver_is_allowed():
    remediation = _evidence()["remediation_boundary"]
    assert remediation["current_packet_may_resume"] is False
    assert remediation["new_packet_required"] == (
        "B6A-AWS-CHANGE-PACKET-2026-003C"
    )
    assert remediation["new_owner_approval_required"] is True
    assert remediation["security_waiver_permitted"] is False
