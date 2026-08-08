from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "platform/contracts/speech-v1.yaml"
DECISION = ROOT / "platform/decisions/B6-CONTRACT-2026-001-adoption.json"
FIXTURES = ROOT / "platform/contracts/fixtures/speech-v1"
SCHEMAS = ROOT / "platform/contracts/schemas/speech-v1"


def load_json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def validate(schema_name: str, value: dict) -> None:
    schema = load_json(SCHEMAS / schema_name)
    Draft202012Validator(
        schema, format_checker=FormatChecker()
    ).validate(value)


def test_owner_adoption_binds_the_exact_canonical_contract():
    decision = load_json(DECISION)
    contract = yaml.safe_load(CONTRACT.read_bytes())
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    assert decision["status"] == "OWNER_ADOPTED"
    assert decision["adopted_contract"]["adopted_sha256"] == digest
    assert digest == "e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d"
    assert contract["status"] == "canonical_owner_adopted_full_b6_contract"
    assert contract["authority"]["decision"] == decision["id"]


def test_file_mode_golden_fixture_is_schema_valid_and_text_only_is_success():
    request = load_json(FIXTURES / "file-request.json")
    response = load_json(FIXTURES / "file-response-text-only.json")
    validate("file-response.schema.json", response)
    assert request["headers"]["X-MedZen-Contract-Version"] == "medzen.speech.v1"
    assert request["headers"]["Authorization"] == (
        "Bearer <redacted-synthetic-test-key>"
    )
    assert request["contains_phi"] is False
    assert (ROOT / request["audio_fixture"]).is_file()
    assert response["reply"]["tts_backend"] == "text_only"
    assert response["reply"]["audio_url"] is None


def test_stream_golden_sequence_obeys_event_specific_requirements():
    contract = yaml.safe_load(CONTRACT.read_bytes())
    sequence = load_json(FIXTURES / "stream-sequence.json")
    server_contract = contract["public_streaming"]["server_events"]
    for event in sequence["server"]:
        validate("stream-event.schema.json", event)
        required = set(server_contract[event["type"]]["required"])
        assert required.issubset(event)
        assert set(event["model_versions"]) == {
            "asr", "registry_snapshot", "llm", "rag", "tts"
        }
    assert [event["type"] for event in sequence["server"]] == [
        "ready", "final_transcript", "reply_text", "completed"
    ]


def test_rag_golden_request_and_response_are_schema_valid():
    validate("rag-request.schema.json", load_json(FIXTURES / "rag-request.json"))
    validate("rag-response.schema.json", load_json(FIXTURES / "rag-response.json"))


def test_contract_freezes_auth_payload_compatibility_and_cancellation():
    contract = yaml.safe_load(CONTRACT.read_bytes())
    assert contract["public_authentication"]["mechanism"] == "opaque_bearer_token"
    assert contract["public_file_mode"]["request"]["maximum_audio_bytes"] == 26214400
    assert contract["public_streaming"]["limits"]["maximum_binary_frame_bytes"] == 65536
    assert contract["compatibility"]["required_value"] == contract["version"]
    assert "cancellation_propagates_within_250_ms" in contract["invariants"]


def test_contract_adoption_grants_no_aws_or_model_authority():
    decision = load_json(DECISION)
    denied = " ".join(decision["not_authorized"])
    for boundary in ("AWS resource", "SSM write", "CPU or GPU", "training"):
        assert boundary in denied
    assert decision["preserved_state"] == {
        "b5_gate_outcome": "BLOCKED_UNCHANGED",
        "deferred_language_approved_versions": "NULL_UNCHANGED",
        "cpu_desired_capacity": 0,
        "gpu_desired_capacity": 0,
    }
