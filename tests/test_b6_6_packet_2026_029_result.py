from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6-PACKET-2026-029-ATTEMPT-1-REFUSED-RAG-ALIGNMENT.json"
IDENTITY = ROOT / "platform/evidence/B6-RAG-IMAGE-INDEX-IDENTITY-2026-001.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_attempt_one_receipts_are_immutable_and_stop_at_file_proof() -> None:
    result = _load(RESULT)
    receipts = result["stage_receipts"]
    assert result["status"] == "TERMINAL_REFUSED_ONE_ATTEMPT_ONE_UNSPENT"
    assert result["attempt_1"]["failure_stage"] == "file_proof"
    assert result["attempt_1"]["http_status"] == 503
    assert result["attempt_1"]["dependency_error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert receipts[-1]["stage"] != "cleanup"  # bindings are path-sorted, not stage-ordered
    assert {item["stage"] for item in receipts} == {
        "stage0", "deadline", "workers_ready", "dra_ready", "rag_ready",
        "asr_ready", "tts_ready", "llm_ready", "orchestrator_ready",
        "controller_window", "controller_ready", "pre_endpoint_images",
        "terraform_window", "endpoints_ready", "alb_ready", "fargate_probe",
        "alb_tag_mutation_warning", "file_proof", "cleanup",
    }
    by_stage = {item["stage"]: item for item in receipts}
    assert by_stage["file_proof"]["status"] == "REFUSED"
    assert by_stage["cleanup"]["status"] == "PASS"
    for item in receipts:
        path = ROOT / item["path"]
        assert _sha256(path) == item["sha256"]
        receipt = _load(path)
        assert receipt["stage"] == item["stage"]
        assert receipt["status"] == item["status"]
        assert receipt["contains_audio_transcript_reply_citations_credentials_or_phi"] is False


def test_attempt_two_is_deliberately_unspent_and_cleanup_is_exact_zero() -> None:
    result = _load(RESULT)
    assert result["attempt_2"] == {
        "status": "NOT_EXECUTED",
        "authorized_but_unspent": True,
        "reason": (
            "Owner directed that an unchanged retry must not be spent after the "
            "deterministic platform configuration finding."
        ),
        "transfer_permitted": False,
        "successor_packet_required": True,
    }
    assert all(value == 0 for key, value in result["zero_state"].items()
               if key in {
                   "cpu_desired", "cpu_asg_instances", "gpu_desired",
                   "gpu_asg_instances", "workload_nodes", "synthetic_pods",
                   "window_ingresses", "window_deployments",
                   "probe_vpc_endpoints", "probe_endpoint_security_groups",
                   "alb_count", "deadline_actions",
                   "production_serving_pointer_count", "approved_asr_objects",
               })
    assert result["zero_state"]["local_token_absent"] is True
    assert result["zero_state"]["persistent_synthetic_secret"] == (
        "RETAINED_OPERATOR_DENIED"
    )


def test_exact_deployed_rag_identity_is_extracted_and_aligned() -> None:
    identity = _load(IDENTITY)
    assert identity["status"] == "VERIFIED_ALIGNED_IDENTITY"
    assert identity["extraction"]["network"] == "NONE"
    assert identity["extraction"]["root_filesystem"] == "READ_ONLY"
    assert identity["comparison"]["exact_match"] is True
    assert identity["comparison"]["alias_match"] is True
    assert identity["embedded_index"]["alias_manifest_hash_matches"] is True
    assert identity["embedded_index"]["manifest_file_sha256"] == (
        "6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160"
    )
    assert identity["aligned_proof_input"]["prior_live_asr_exact_phrase_match"] is True
    assert identity["aligned_proof_input"]["offline_citation_count"] == 3
    assert identity["finding"]["literal_registry_to_image_index_identity_mismatch"] is False
    assert identity["governance"]["new_publication_required"] is False


def test_result_does_not_claim_b6_completion_or_mutate_prohibited_state() -> None:
    result = _load(RESULT)
    assert result["live_control_result"]["apparatus_defect"] is False
    assert result["live_control_result"]["clinical_safety_interpretation"]
    assert all(value == 0 for value in result["prohibited_state_unchanged"].values())
    assert result["cost_and_allowance"]["attempts_consumed"] == 1
    assert result["cost_and_allowance"]["attempts_deliberately_unspent"] == 1
    assert result["cost_and_allowance"]["inside_existing_reservation"] is True
