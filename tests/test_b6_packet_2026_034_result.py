from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6-PACKET-2026-034-ATTEMPT-1-COMPLETE.json"
RECEIPTS = ROOT / "platform/evidence/receipts/B6-2026-034-A1-LIVE"
FILE_PROOF = ROOT / "platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_result_binds_exactly_all_live_receipts_and_every_stage_passed() -> None:
    evidence = _load(EVIDENCE)
    assert evidence["status"] == "PASS_REMAINING_PROOFS_CLEANUP_COMPLETE"
    assert set(evidence["receipt_hashes"]) == {
        path.stem for path in RECEIPTS.glob("*.json")
    }
    assert len(evidence["receipt_hashes"]) == 22
    for stage, expected in evidence["receipt_hashes"].items():
        path = RECEIPTS / f"{stage}.json"
        receipt = _load(path)
        assert _sha256(path) == expected
        assert receipt["stage"] == stage
        assert receipt["status"] == "PASS"


def test_file_proof_is_preserved_and_was_not_rerun() -> None:
    evidence = _load(EVIDENCE)
    preserved = evidence["preserved_milestones"]["file_proof"]
    assert _sha256(FILE_PROOF) == preserved["sha256"]
    assert preserved["status"] == "PASS_HISTORICAL_UNCHANGED"
    assert preserved["rerun"] is False
    assert not (RECEIPTS / "file_proof.json").exists()
    assert evidence["prohibited_state_unchanged"]["file_proof_reruns"] == 0


def test_remaining_streaming_cancellation_drill_and_isolation_proofs_pass() -> None:
    evidence = _load(EVIDENCE)
    results = evidence["remaining_proof_results"]
    websocket = _load(RECEIPTS / "websocket_proof.json")["payload"]
    cancellation = _load(RECEIPTS / "cancellation_proof.json")["payload"]
    drills = _load(RECEIPTS / "failure_drills.json")["payload"]
    isolation = _load(RECEIPTS / "isolation_proof.json")["payload"]

    assert websocket["status"] == results["websocket"]["status"] == "PASS"
    assert websocket["event_types"][-3:] == [
        "final_transcript",
        "reply_text",
        "completed",
    ]
    assert websocket["partial_queue_limit"] == 4
    assert websocket["audio_queue_limit"] == 8
    assert websocket["final_result_preserved"] is True

    assert cancellation["status"] == results["cancellation"]["status"] == "PASS"
    assert cancellation["barge_in_latency_ms"] == 96.646
    assert cancellation["barge_in_latency_ms"] < cancellation["maximum_ms"] == 250

    assert drills["status"] == results["failure_drills"]["status"] == "PASS"
    assert drills["rag_unavailable"]["controlled_http_status"] == 503
    assert drills["rag_unavailable"]["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert drills["controlled_refusals"]["http_500_cascades"] == 0

    assert isolation["status"] == results["isolation"]["status"] == "PASS"
    assert isolation["orchestrator_ingresses"] == 1
    assert isolation["dependency_ingresses"] == 0
    assert isolation["public_load_balancers"] == 0


def test_cleanup_and_independent_sweep_are_exact_zero() -> None:
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
        "running_probe_tasks",
        "alb_count",
        "alb_controller",
        "nvidia_dra_driver",
        "deadline_actions",
        "production_serving_pointer_count",
        "approved_asr_objects",
    }
    assert all(evidence["zero_state"][key] == 0 for key in numeric_zero_fields)
    assert evidence["zero_state"]["local_token_absent"] is True
    assert evidence["zero_state"]["local_alb_hostname_absent"] is True
    assert evidence["zero_state"]["persistent_synthetic_secret"] == (
        "RETAINED_KMS_BOUND_OPERATOR_DENIED"
    )
    assert all(value == 0 for value in evidence["prohibited_state_unchanged"].values())


def test_attempt_one_pass_terminates_unused_attempt_without_rollover() -> None:
    evidence = _load(EVIDENCE)
    allowance = evidence["allowance"]
    assert evidence["execution"]["outcome"] == "PASS"
    assert evidence["execution"]["wall_clock_seconds"] <= 4500
    assert allowance["packet_attempts_authorized"] == 2
    assert allowance["packet_attempts_consumed"] == 1
    assert allowance["packet_attempts_unused"] == 1
    assert allowance["packet_attempts_remaining"] == 0
    assert allowance["attempt_2_status"] == "TERMINATED_UNUSED_BY_ATTEMPT_1_PASS"
    assert allowance["unused_seconds_transferable"] is False
    assert allowance["additional_execution_under_packet_2026_034"] is False


def test_result_does_not_promote_the_blocked_model_or_preempt_closure_review() -> None:
    evidence = _load(EVIDENCE)
    assert evidence["preserved_milestones"]["b5_gate_report"]["outcome"] == "BLOCKED"
    assert evidence["completion_semantics"]["b6_bounded_integration"] == "PASS"
    assert evidence["completion_semantics"]["b6_complete"] is False
    assert evidence["completion_semantics"]["b6_closure_status"] == (
        "PENDING_INDEPENDENT_EXIT_REVIEW_AND_COST_RECONCILIATION"
    )
    assert evidence["completion_semantics"]["b7_authorized"] is False
