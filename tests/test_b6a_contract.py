import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = yaml.safe_load(
    (ROOT / "platform/contracts/speech-v1.yaml").read_text())


def test_original_public_endpoints_and_internal_b6a_subset_are_explicit():
    assert CONTRACT["public_file_mode"]["path"] == "/v1/conversations/speech"
    assert CONTRACT["public_streaming"]["path"] == "/v1/conversations/stream"
    assert CONTRACT["internal_asr_file_mode"]["path"] == (
        "/internal/v1/transcriptions")
    assert CONTRACT["internal_asr_streaming"]["path"] == (
        "/internal/v1/transcriptions/stream")
    assert CONTRACT["public_file_mode"]["b6a_implementation_state"] == (
        "NOT_IMPLEMENTED")
    assert CONTRACT["internal_asr_file_mode"]["b6a_implementation_state"] == (
        "REQUIRED")


def test_every_server_event_carries_request_and_model_identity():
    for event, spec in CONTRACT["public_streaming"]["server_events"].items():
        required = set(spec["required"])
        assert {"request_id", "model_versions"} <= required, event


def test_backpressure_and_cancellation_match_base_v5():
    queues = CONTRACT["public_streaming"]["backpressure"]
    assert queues["partial_transcripts"] == {
        "max": 4, "overflow": "drop_oldest"}
    assert queues["audio_chunks"] == {
        "max": 8, "overflow": "pause_upstream"}
    assert "cancellation_propagates_within_250_ms" in CONTRACT["invariants"]


def test_b6a_cannot_claim_full_b6_or_model_approval():
    limits = CONTRACT["b6a_limits"]
    assert limits["classification"] == "PLATFORM_PROOF_ONLY"
    assert limits["allowed_services"] == ["model-loader", "asr-runtime"]
    assert limits["production_registry_alias_change"] is False
    assert limits["approved_version_change"] is False
    assert limits["full_conversation_response"] is False


def test_tts_ambiguity_is_resolved_without_touching_other_project():
    record = json.loads((
        ROOT / "platform/decisions/B6A-TTS-2026-001-service-boundary.json"
    ).read_text())
    assert record["finding"]["conclusion"].endswith(
        "It must not be described as reusable completed code.")
    separate = record["separate_existing_service"]
    assert separate["name"] == "medzen-tts-gateway"
    assert separate["reuse_authorized"] is False
    assert separate["modification_authorized"] is False
    assert record["b6a_decision"]["b6a_services"] == [
        "model-loader", "asr-runtime"]
    assert record["future_full_b6"]["text_only_is_success"] is True
