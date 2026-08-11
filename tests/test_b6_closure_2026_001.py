from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "platform/evidence/B6-CLOSURE-2026-001.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_binding(binding: dict) -> None:
    assert _sha256(ROOT / binding["path"]) == binding["sha256"]


def test_closure_binds_plan_contract_local_exits_registry_and_cost() -> None:
    closure = _load(CLOSURE)
    _assert_binding(closure["governing_plan"])
    _assert_binding(closure["contract"]["adoption_record"])
    _assert_binding(closure["contract"]["canonical_contract"])
    for binding in closure["local_engineering_exits"]:
        _assert_binding(binding)
    for binding in closure["registry_evidence"]:
        _assert_binding(binding)
    _assert_binding(closure["b6a_foundation"])
    _assert_binding(closure["final_window"]["packet_result"])
    _assert_binding(closure["cost_closure"]["registry"])
    _assert_binding(closure["cost_closure"]["reconciliation"])


def test_stage_a_binds_exactly_eight_pass_receipts() -> None:
    closure = _load(CLOSURE)
    stage_a = closure["stage_a_receipts"]
    directory = ROOT / stage_a["directory"]
    assert set(stage_a["receipt_hashes"]) == {
        path.stem for path in directory.glob("*.json")
    }
    assert len(stage_a["receipt_hashes"]) == 8
    for stage, expected in stage_a["receipt_hashes"].items():
        path = directory / f"{stage}.json"
        assert _sha256(path) == expected
        assert _load(path)["status"] == "PASS"
    aggregate = _load(directory / "stage_a.json")
    assert aggregate["payload"]["stable_probe_passes"] == 3
    assert aggregate["payload"]["cleanup_complete"] is True


def test_file_proof_is_immutable_pass_and_not_in_final_window() -> None:
    closure = _load(CLOSURE)
    proof = closure["file_conversation_proof"]
    path = ROOT / proof["path"]
    assert _sha256(path) == proof["sha256"]
    receipt = _load(path)
    assert receipt["status"] == "PASS"
    assert receipt["payload"]["http_status"] == 200
    assert receipt["payload"]["citation_count"] == 3
    assert receipt["payload"]["tts_backend"] == "text_only"
    assert set(receipt["payload"]["model_versions"]) == {
        "asr",
        "llm",
        "rag",
        "registry_snapshot",
        "tts",
    }
    assert not (
        ROOT / closure["final_window"]["directory"] / "file_proof.json"
    ).exists()


def test_final_window_binds_exactly_all_22_green_receipts() -> None:
    closure = _load(CLOSURE)
    window = closure["final_window"]
    directory = ROOT / window["directory"]
    assert set(window["receipt_hashes"]) == {
        path.stem for path in directory.glob("*.json")
    }
    assert len(window["receipt_hashes"]) == 22
    for stage, expected in window["receipt_hashes"].items():
        path = directory / f"{stage}.json"
        assert _sha256(path) == expected
        assert _load(path)["status"] == "PASS"

    websocket = _load(directory / "websocket_proof.json")["payload"]
    cancellation = _load(directory / "cancellation_proof.json")["payload"]
    drills = _load(directory / "failure_drills.json")["payload"]
    isolation = _load(directory / "isolation_proof.json")["payload"]
    assert websocket["event_types"][-3:] == [
        "final_transcript",
        "reply_text",
        "completed",
    ]
    assert websocket["final_result_preserved"] is True
    assert cancellation["barge_in_latency_ms"] < cancellation["maximum_ms"] == 250
    assert drills["controlled_refusals"]["http_500_cascades"] == 0
    assert isolation["orchestrator_ingresses"] == 1
    assert isolation["dependency_ingresses"] == 0
    assert isolation["public_load_balancers"] == 0


def test_scan_chain_is_hash_bound_clean_and_final_digests_are_scanned() -> None:
    closure = _load(CLOSURE)
    for generation in closure["scan_chain"]:
        _assert_binding(generation)
        assert generation["outcome"] == "PASS_SCAN_ONLY"
        assert generation["critical"] == 0
        assert generation["high"] == 0
        assert generation["waivers"] == 0

    deployed = closure["final_deployed_child_digests"]
    base = closure["scan_chain"][0]["child_digests"]
    services = closure["scan_chain"][1]["child_digests"]
    final_orchestrator = closure["scan_chain"][3]["child_digest"]
    assert deployed["model_loader"] == base["model_loader"]
    assert deployed["asr_runtime"] == base["asr_runtime"]
    assert deployed["nvidia_dra"] == base["nvidia_dra"]
    assert deployed["rag_index"] == services["rag_index"]
    assert deployed["llm_gateway"] == services["llm_gateway"]
    assert deployed["tts_gateway"] == services["tts_gateway"]
    assert deployed["orchestrator"] == final_orchestrator
    assert len(deployed) == 7


def test_cost_and_zero_state_close_without_claiming_b5_or_production() -> None:
    closure = _load(CLOSURE)
    registry = _load(ROOT / closure["cost_closure"]["registry"]["path"])
    assert registry["guardrail_summary"]["active_reservations_usd"] == 0.0
    assert closure["cost_closure"]["active_reservations_usd"] == 0.0
    assert all(value == 0 for value in closure["zero_state"].values())
    boundaries = closure["immutable_boundaries"]
    assert boundaries["b5_outcome"] == "BLOCKED_UNCHANGED"
    assert boundaries["v0_production_approved"] is False
    assert boundaries["b7_authorized"] is False
    assert closure["closure_semantics"]["b6_complete"] is False
    assert closure["closure_semantics"]["b6_final_closure_review"] == "PENDING"
    assert closure["closure_semantics"]["new_aws_packet_required"] is False
