from __future__ import annotations

import hashlib
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-orchestrator"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_orchestrator.app import (  # noqa: E402
    build_local_orchestrator,
    create_app,
)
from medzen_speech_orchestrator.local_dependencies import ASRResult  # noqa: E402
from medzen_speech_orchestrator.orchestrator import (  # noqa: E402
    OrchestratorRefusal,
    SpeechOrchestrator,
)


AUDIO = (ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav").read_bytes()
KEY = "medzen-b6-synthetic-client-key"
HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "X-MedZen-Contract-Version": "medzen.speech.v1",
}
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
SCHEMA = json.loads((
    ROOT / "platform/contracts/schemas/orchestrator-file-v2/response.schema.json"
).read_bytes())
GOLDEN = json.loads((
    ROOT / "platform/contracts/fixtures/orchestrator-file-v2/response.json"
).read_bytes())


class StepClock:
    def __init__(self):
        self.values = iter([0.000, 0.001, 0.003, 0.004, 0.007, 0.008, 0.012, 0.013])

    def __call__(self) -> float:
        return next(self.values)


def local_app(*, deterministic: bool = False):
    service, auth = build_local_orchestrator()
    if deterministic:
        service.clock = StepClock()
        service.session_id_factory = lambda: SESSION_ID
    return create_app(service, auth), service


def request(client: TestClient, **overrides: Any):
    headers = overrides.pop("headers", HEADERS)
    data = {
        "request_id": REQUEST_ID,
        "language_hint": "en",
        "response_audio": "false",
    }
    data.update(overrides.pop("data", {}))
    data = {name: value for name, value in data.items() if value is not None}
    audio = overrides.pop("audio", AUDIO)
    media_type = overrides.pop("media_type", "audio/wav")
    assert not overrides
    return client.post(
        "/v1/conversations/speech",
        headers=headers,
        data=data,
        files={"audio": ("synthetic.wav", audio, media_type)},
    )


def test_one_synthetic_file_request_matches_golden_contract_and_logs_no_body(caplog):
    app, _ = local_app(deterministic=True)
    caplog.set_level(logging.INFO, logger="medzen.orchestrator")
    with TestClient(app) as client:
        response = request(client)
    assert response.status_code == 200
    payload = response.json()
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(payload)
    assert payload == GOLDEN
    assert payload["reply"]["tts_backend"] == "text_only"
    assert payload["reply"]["audio_url"] is None
    assert len(payload["reply"]["citations"]) == 3
    assert set(payload["model_versions"]) == {
        "asr", "registry_snapshot", "llm", "rag", "tts"
    }
    assert hashlib.sha256(SESSION_ID.hex.encode()).hexdigest() not in caplog.text
    for forbidden in (
        KEY,
        payload["transcript"]["verbatim"],
        payload["reply"]["text"],
        *(item["excerpt"] for item in payload["reply"]["citations"]),
    ):
        assert forbidden not in caplog.text
    safe_session_hash = hashlib.sha256(str(SESSION_ID).encode()).hexdigest()
    assert safe_session_hash in caplog.text
    assert REQUEST_ID in caplog.text


def test_readiness_binds_the_content_addressed_registry_snapshot():
    app, service = local_app()
    with TestClient(app) as client:
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {
        "ready": True,
        "mode": "local_fixture",
        "registry_loaded": True,
        "authentication_loaded": True,
        "external_network_access": False,
        "registry_snapshot": service.registry_snapshot,
    }


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({"X-MedZen-Contract-Version": "medzen.speech.v1"}, 401, "AUTH_REQUIRED"),
        ({
            "Authorization": "Bearer wrong-key",
            "X-MedZen-Contract-Version": "medzen.speech.v1",
        }, 403, "AUTH_INVALID"),
        ({"Authorization": f"Bearer {KEY}"}, 426, "CONTRACT_VERSION_UNSUPPORTED"),
    ],
)
def test_auth_and_contract_refuse_before_processing_audio(headers, status, code, caplog):
    app, _ = local_app()
    caplog.set_level(logging.INFO, logger="medzen.orchestrator")
    marker = b"SENSITIVE-BODY-MARKER"
    with TestClient(app) as client:
        response = request(client, headers=headers, audio=marker)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert marker.decode() not in caplog.text


def test_unknown_language_and_wrong_audio_fail_closed_without_llm_reply():
    app, _ = local_app()
    with TestClient(app) as client:
        language = request(client, data={"language_hint": "fr"})
        audio = request(client, audio=b"RIFF\x00\x00\x00\x00WAVEbad")
    assert language.status_code == 422
    assert language.json()["error"]["code"] == "INVALID_REQUEST"
    assert audio.status_code == 503
    assert audio.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "reply" not in language.json() and "reply" not in audio.json()


def test_invalid_request_identity_media_type_and_empty_audio_are_rejected():
    app, _ = local_app()
    with TestClient(app) as client:
        invalid_id = request(client, data={"request_id": "not-a-uuid"})
        wrong_type = request(client, media_type="audio/mpeg")
        empty = request(client, audio=b"")
    assert invalid_id.status_code == 400
    assert invalid_id.json()["error"]["code"] == "INVALID_REQUEST"
    assert wrong_type.status_code == 415
    assert wrong_type.json()["error"]["code"] == "UNSUPPORTED_AUDIO_TYPE"
    assert empty.status_code == 400


def test_request_id_is_generated_when_absent_and_sessions_are_unique():
    app, _ = local_app()
    with TestClient(app) as client:
        first = request(client, data={"request_id": None})
        second = request(client, data={"request_id": None})
    assert first.status_code == second.status_code == 200
    uuid.UUID(first.json()["request_id"])
    uuid.UUID(second.json()["request_id"])
    assert first.json()["request_id"] != second.json()["request_id"]
    assert first.json()["session_id"] != second.json()["session_id"]


def test_nonlocal_mode_refuses_startup(monkeypatch):
    monkeypatch.setenv("MEDZEN_ORCHESTRATOR_MODE", "ssm")
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        response = request(client)
    assert ready.status_code == 503
    assert ready.json()["error_code"] == "RuntimeError"
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


class EmergencyASR:
    def transcribe(self, audio: bytes, *, request_id: str, route):
        return ASRResult(
            request_id=request_id,
            language="en",
            language_probability=1.0,
            transcript={
                "verbatim": "Synthetic emergency control test",
                "normalized": "Synthetic emergency control test",
                "normalization_version": "test-v1",
            },
            duration_seconds=1.0,
            model_versions=route.model_versions,
        )


class MustNotRun:
    def __getattr__(self, name):
        raise AssertionError(f"post-emergency dependency ran: {name}")


def test_emergency_check_runs_before_rag_and_llm_and_cannot_be_shed():
    base, _ = build_local_orchestrator()
    service = SpeechOrchestrator(
        router=base.router,
        emergency=base.emergency,
        asr=EmergencyASR(),
        rag=MustNotRun(),
        llm=MustNotRun(),
        session_id_factory=lambda: SESSION_ID,
    )
    session, result = service.handle(
        audio=b"synthetic", request_id=REQUEST_ID, language_hint="en"
    )
    assert session == str(SESSION_ID)
    assert result["reply"]["tts_backend"] == "text_only"
    assert result["reply"]["citations"] == []
    assert result["model_versions"]["llm"] is None
    assert result["model_versions"]["rag"] is None


class TamperedLLM:
    def __init__(self, delegate):
        self.delegate = delegate

    def complete(self, **kwargs):
        value = self.delegate.complete(**kwargs)
        value["model_versions"]["llm"] = "unbound-version"
        return value


def test_dependency_model_version_mismatch_refuses_the_whole_reply():
    base, _ = build_local_orchestrator()
    base.llm = TamperedLLM(base.llm)
    with pytest.raises(OrchestratorRefusal, match="does not match") as caught:
        base.handle(audio=AUDIO, request_id=REQUEST_ID, language_hint="en")
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"


class TamperedBindingLLM:
    def __init__(self, delegate):
        self.delegate = delegate

    def complete(self, **kwargs):
        value = self.delegate.complete(**kwargs)
        value["reply"]["citation_binding_sha256"] = "0" * 64
        return value


def test_dependency_citation_binding_mismatch_refuses_the_whole_reply():
    base, _ = build_local_orchestrator()
    base.llm = TamperedBindingLLM(base.llm)
    with pytest.raises(OrchestratorRefusal, match="citations") as caught:
        base.handle(audio=AUDIO, request_id=REQUEST_ID, language_hint="en")
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"


class TamperedIdentityASR:
    def __init__(self, delegate):
        self.delegate = delegate

    def transcribe(self, audio, *, request_id, route):
        result = self.delegate.transcribe(audio, request_id=request_id, route=route)
        return ASRResult(
            request_id="33333333-3333-4333-8333-333333333333",
            language=result.language,
            language_probability=result.language_probability,
            transcript=result.transcript,
            duration_seconds=result.duration_seconds,
            model_versions=result.model_versions,
        )


def test_asr_request_identity_mismatch_refuses_before_rag_or_llm():
    base, _ = build_local_orchestrator()
    base.asr = TamperedIdentityASR(base.asr)
    base.rag = MustNotRun()
    base.llm = MustNotRun()
    with pytest.raises(OrchestratorRefusal, match="request contract") as caught:
        base.handle(audio=AUDIO, request_id=REQUEST_ID, language_hint="en")
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"


def test_remote_dependencies_do_not_add_a_general_purpose_http_client():
    service_root = ROOT / "services/speech-orchestrator"
    source = "\n".join(
        path.read_text() for path in sorted(service_root.rglob("*.py"))
    )
    requirements = (service_root / "requirements.txt").read_text().casefold()
    deployed_requirements = (
        service_root / "requirements.deployed.txt"
    ).read_text().casefold()
    for forbidden in (
        "import requests", "import httpx", "requests==", "httpx==",
    ):
        assert forbidden not in source.casefold()
        assert forbidden not in requirements
    assert "boto3==1.43.58" in deployed_requirements
    assert "botocore==1.43.63" in deployed_requirements


def test_ndjson_stream_emits_validated_stages_then_the_identical_final_result():
    """Phase 3: Accept: application/x-ndjson narrates the SAME pipeline —
    transcript_final, reply_final, then the buffered result verbatim as the
    final event; a buffered call returns exactly that result. (Real clock:
    the deterministic StepClock only serves a single request.)"""
    app, _ = local_app()
    with TestClient(app) as client:
        buffered = request(client).json()
        headers = dict(HEADERS, Accept="application/x-ndjson")
        streamed = request(client, headers=headers)
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in streamed.text.splitlines() if line.strip()]
        deltas = [e["text"] for e in events if e["event"] == "reply_delta"]
        events = [e for e in events if e["event"] != "reply_delta"]
        names = [e["event"] for e in events]
        assert names == ["transcript_final", "reply_final", "final"]
        assert events[0]["transcript"] == buffered["transcript"]
        assert events[1]["text"] == buffered["reply"]["text"]
        # Phase 3b: deltas narrate exactly the validated reply text
        assert "".join(deltas) == buffered["reply"]["text"]
        final = {k: v for k, v in events[2].items() if k != "event"}
        # only session id (fresh per request) and latency figures may differ
        for key in ("request_id", "language", "transcript", "reply", "model_versions"):
            assert final[key] == buffered[key]


# ---------------------------------------------------------------------------
# Codex review 2026-09-03: retrieval query order, ungrounded reply under the flag
# ---------------------------------------------------------------------------
from medzen_speech_orchestrator import orchestrator as orchestrator_module  # noqa: E402


def test_retrieval_query_keeps_the_current_question_first_and_trims_the_previous_one():
    long_previous = "previous " * 400          # ~3600 characters
    history = [{"role": "user", "text": long_previous},
               {"role": "assistant", "text": "an answer that must never be used"}]
    query = SpeechOrchestrator._retrieval_query("what about the price?", history)
    assert query.startswith("what about the price? previous")
    assert "answer" not in query
    assert len(query) <= len("what about the price? ") + SpeechOrchestrator.PREVIOUS_QUESTION_CHARACTERS
    assert SpeechOrchestrator._retrieval_query("only question", []) == "only question"


class UngroundedLLM:
    """A model that used none of the supplied documents."""

    def __init__(self, delegate):
        self.delegate = delegate

    def complete(self, **kwargs):
        value = self.delegate.complete(**kwargs)
        value["reply"]["citations"] = []
        value["reply"]["citation_binding_sha256"] = SpeechOrchestrator._citation_binding([])
        return value


def test_reply_citing_nothing_is_ungrounded_under_the_flag_and_refused_without(monkeypatch):
    monkeypatch.setattr(orchestrator_module, "ALLOW_UNGROUNDED", False)
    base, _ = build_local_orchestrator()
    base.llm = UngroundedLLM(base.llm)
    with pytest.raises(OrchestratorRefusal, match="does not match"):
        base.handle(audio=AUDIO, request_id=REQUEST_ID, language_hint="en")
    monkeypatch.setattr(orchestrator_module, "ALLOW_UNGROUNDED", True)
    base, _ = build_local_orchestrator()
    base.llm = UngroundedLLM(base.llm)
    _, result = base.handle(audio=AUDIO, request_id=REQUEST_ID, language_hint="en")
    assert result["reply"]["citations"] == []
    assert result["reply"]["text"]
