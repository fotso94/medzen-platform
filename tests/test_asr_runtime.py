from __future__ import annotations

import json
import logging
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
ASR_ROOT = ROOT / "services/asr-runtime"
sys.path.insert(0, str(ASR_ROOT))

from medzen_asr_runtime.app import create_app  # noqa: E402
from medzen_asr_runtime.backend import (  # noqa: E402
    BackendRefusal,
    Transcript,
    load_ready_marker,
)


REQUEST_ID = "708abbfd-937e-41c9-8dd0-0c81eb0ba912"


class Backend:
    ready = True
    model_versions = {
        "asr": "v0",
        "registry_snapshot": "b6a-non-serving:" + "a" * 64,
        "llm": None,
        "rag": None,
        "tts": None,
    }

    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, language_hint):
        audio = Path(audio_path).read_bytes()
        self.calls.append({"bytes": audio, "language_hint": language_hint})
        return Transcript(
            language=language_hint or "ln",
            language_probability=0.98,
            verbatim="Mbote na yo",
            normalized="Mbote na yo",
            normalization_version="b6a-unicode-nfc-whitespace-v1",
            duration_seconds=1.25,
        )


def test_health_and_readiness_are_separate():
    with TestClient(create_app(Backend())) as client:
        assert client.get("/healthz").status_code == 200
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json() == {
            "ready": True,
            "classification": "PLATFORM_PROOF_ONLY",
            "model_manifest_verified": True,
            "model_tree_verified": True,
            "model_loaded": True,
            "smoke_inference_passed": True,
            "platform_test_disclosure_loaded": True,
        }


def test_file_mode_returns_contract_identity_and_platform_disclosure(caplog):
    backend = Backend()
    caplog.set_level(logging.INFO, logger="medzen.asr")
    with TestClient(create_app(backend)) as client:
        response = client.post(
            "/internal/v1/transcriptions",
            headers={"X-Request-ID": REQUEST_ID,
                     "X-MedZen-Language": "ln",
                     "Content-Type": "audio/wav"},
            content=b"RIFF-test-audio")
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == REQUEST_ID
    assert body["transcript"] == {
        "verbatim": "Mbote na yo",
        "normalized": "Mbote na yo",
        "normalization_version": "b6a-unicode-nfc-whitespace-v1",
    }
    assert body["model_versions"] == backend.model_versions
    assert body["classification"] == "PLATFORM_PROOF_ONLY"
    assert body["production_approved"] is False
    assert backend.calls == [{"bytes": b"RIFF-test-audio", "language_hint": "ln"}]
    # Access logs contain identity/latency only, never content.
    access_log = "\n".join(record.message for record in caplog.records)
    assert REQUEST_ID in access_log
    assert "Mbote" not in access_log
    assert "RIFF-test-audio" not in access_log


@pytest.mark.parametrize("headers,status,code", [
    ({"Content-Type": "audio/wav"}, 400, "INVALID_REQUEST"),
    ({"Content-Type": "audio/mpeg", "X-Request-ID": REQUEST_ID}, 415,
     "UNSUPPORTED_AUDIO_TYPE"),
    ({"Content-Type": "audio/wav", "X-Request-ID": REQUEST_ID,
      "X-MedZen-Language": "lingala"}, 400, "INVALID_REQUEST"),
])
def test_file_mode_refuses_invalid_contract_inputs(headers, status, code):
    with TestClient(create_app(Backend())) as client:
        response = client.post(
            "/internal/v1/transcriptions", headers=headers, content=b"audio")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_file_mode_refuses_empty_and_oversized_audio():
    headers = {"Content-Type": "audio/wav", "X-Request-ID": REQUEST_ID}
    with TestClient(create_app(Backend(), max_audio_bytes=4)) as client:
        assert client.post(
            "/internal/v1/transcriptions", headers=headers, content=b"").json()[
                "error"]["code"] == "EMPTY_AUDIO"
        response = client.post(
            "/internal/v1/transcriptions", headers=headers, content=b"12345")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "AUDIO_TOO_LARGE"


def test_websocket_stream_returns_ready_final_and_completed_with_identity():
    backend = Backend()
    with TestClient(create_app(backend)) as client:
        with client.websocket_connect(
                "/internal/v1/transcriptions/stream") as websocket:
            websocket.send_json({"type": "start", "request_id": REQUEST_ID,
                                 "language_hint": "ln"})
            ready = websocket.receive_json()
            websocket.send_bytes(b"RIFF-stream-audio")
            websocket.send_json({"type": "end_of_speech"})
            final = websocket.receive_json()
            completed = websocket.receive_json()
    assert [ready["type"], final["type"], completed["type"]] == [
        "ready", "final_transcript", "completed"]
    for event in (ready, final, completed):
        assert event["request_id"] == REQUEST_ID
        assert uuid.UUID(event["session_id"])
        assert event["model_versions"] == backend.model_versions
    assert final["transcript"]["verbatim"] == "Mbote na yo"
    assert backend.calls[0] == {
        "bytes": b"RIFF-stream-audio", "language_hint": "ln"}


@pytest.mark.parametrize("event", [
    {"type": "cancel", "reason": "client_cancel"},
    {"type": "barge_in"},
])
def test_websocket_cancel_or_barge_in_returns_clean_cancellation(event):
    backend = Backend()
    with TestClient(create_app(backend)) as client:
        with client.websocket_connect(
                "/internal/v1/transcriptions/stream") as websocket:
            websocket.send_json({"type": "start", "request_id": REQUEST_ID})
            ready = websocket.receive_json()
            websocket.send_bytes(b"partial-audio")
            websocket.send_json(event)
            cancelled = websocket.receive_json()
    assert ready["type"] == "ready"
    assert cancelled["type"] == "cancelled"
    assert cancelled["request_id"] == REQUEST_ID
    assert cancelled["model_versions"] == backend.model_versions
    assert backend.calls == []


def test_ready_marker_refuses_missing_disclosure_or_pin(tmp_path):
    marker = {
        "ready": True,
        "classification": "PLATFORM_PROOF_ONLY",
        "serving_label": "v0",
        "production_approved": False,
        "quality_gate_outcome": "FAIL",
        "manifest_sha256": "a" * 64,
        "artifact_tree_sha256": "b" * 64,
        "precision": "CTranslate2_float16",
        "smoke_inference": {"passed": True},
    }
    (tmp_path / ".medzen-ready.json").write_text(json.dumps(marker))
    assert load_ready_marker(tmp_path, "a" * 64)["ready"] is True
    marker["production_approved"] = True
    (tmp_path / ".medzen-ready.json").write_text(json.dumps(marker))
    with pytest.raises(BackendRefusal, match="production-approved"):
        load_ready_marker(tmp_path, "a" * 64)
    marker["production_approved"] = False
    (tmp_path / ".medzen-ready.json").write_text(json.dumps(marker))
    with pytest.raises(BackendRefusal, match="differs from deployment pin"):
        load_ready_marker(tmp_path, "c" * 64)


def test_runtime_dockerfiles_keep_weights_out_and_run_nonroot():
    for service in ("model-loader", "asr-runtime"):
        source = (ROOT / f"services/{service}/Dockerfile").read_text()
        assert "USER 10001:10001" in source
        assert "COPY artifacts" not in source
        assert "COPY .cache" not in source
        assert "HF_HUB_OFFLINE=1" in source
