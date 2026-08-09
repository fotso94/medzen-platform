from __future__ import annotations

import copy
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-tts-gateway"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_tts_gateway.app import create_app, fish_breaker  # noqa: E402
from medzen_speech_tts_gateway.gateway import TTSGateway  # noqa: E402
from medzen_speech_tts_gateway.provider import FakeFishProvider  # noqa: E402


REQUEST = __import__("json").loads((
    ROOT / "platform/contracts/fixtures/tts-v1/request.json"
).read_bytes())


def fish_app(outcomes=None):
    provider = FakeFishProvider(outcomes)
    service = TTSGateway(provider=provider, breaker=fish_breaker())
    return create_app(service), service, provider


def test_default_http_path_is_a_text_only_200_success():
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json()["status"] == "alive"
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["backend_mode"] == "text_only"
        assert ready.json()["fish_available"] is False
        response = client.post("/internal/v1/syntheses", json=REQUEST)
    assert response.status_code == 200
    assert response.json()["text"] == REQUEST["text"]
    assert response.json()["tts_backend"] == "text_only"


@pytest.mark.parametrize(
    ("outcome", "backend", "reason"),
    [
        ("success", "fish", None),
        ("timeout", "text_only", "FISH_TIMEOUT"),
        ("unavailable", "text_only", "FISH_UNAVAILABLE"),
        ("malformed", "text_only", "FISH_INVALID_RESPONSE"),
    ],
)
def test_fake_fish_success_and_failures_are_http_200_without_text_loss(
    outcome, backend, reason
):
    app, _, _ = fish_app([outcome])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/internal/v1/syntheses", json=REQUEST)
    assert response.status_code == 200
    assert response.json()["text"] == REQUEST["text"]
    assert response.json()["tts_backend"] == backend
    assert response.json()["degradation_reason"] == reason


def test_open_fish_breaker_is_visible_but_text_only_keeps_readiness_healthy():
    app, service, provider = fish_app(["timeout"] * 3)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/internal/v1/syntheses", json=REQUEST).status_code == 200
        ready = client.get("/readyz")
        degraded = client.post("/internal/v1/syntheses", json=REQUEST)
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["fish_breaker_state"] == "open"
    assert ready.json()["fish_available"] is False
    assert degraded.status_code == 200
    assert degraded.json()["degradation_reason"] == "FISH_CIRCUIT_OPEN"
    assert len(provider.calls) == 3
    assert service.breaker.state.value == "open"


def test_access_logs_exclude_text_audio_and_invalid_header_content(caplog):
    app, _, _ = fish_app()
    caplog.set_level(logging.INFO, logger="medzen.speech_tts")
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/syntheses",
            json=REQUEST,
            headers={"X-Request-ID": "sensitive-not-a-uuid"},
        )
    assert response.status_code == 200
    assert REQUEST["text"] not in caplog.text
    assert "MEDZEN_FAKE_FISH" not in caplog.text
    assert "sensitive-not-a-uuid" not in caplog.text
    assert '"request_id": "absent"' in caplog.text


def test_payload_media_type_and_unknown_fields_fail_at_the_http_boundary():
    app, _, provider = fish_app()
    with TestClient(create_app(max_body_bytes=32)) as client:
        oversized = client.post("/internal/v1/syntheses", json=REQUEST)
        assert oversized.status_code == 413
    with TestClient(app) as client:
        media = client.post(
            "/internal/v1/syntheses", content="{}",
            headers={"content-type": "text/plain"},
        )
        invalid = copy.deepcopy(REQUEST)
        invalid["unknown"] = True
        unknown = client.post("/internal/v1/syntheses", json=invalid)
    assert media.status_code == 415
    assert unknown.status_code == 400
    assert provider.calls == []


def test_every_environment_selected_real_provider_mode_refuses_startup(monkeypatch):
    monkeypatch.setenv("MEDZEN_SPEECH_TTS_PROVIDER", "fish")
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        response = client.post("/internal/v1/syntheses", json=REQUEST)
    assert ready.status_code == 503
    assert ready.json()["provider_network_access"] is False
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
