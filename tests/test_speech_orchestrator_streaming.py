from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from starlette.websockets import WebSocketDisconnect


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-orchestrator"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_orchestrator.app import build_local_orchestrator  # noqa: E402
from medzen_speech_orchestrator.streaming import (  # noqa: E402
    InMemoryFinalResultStore,
    StreamLimits,
)
from medzen_speech_orchestrator.streaming_app import create_app  # noqa: E402


WAV = ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav"
with wave.open(str(WAV), "rb") as _audio:
    PCM = _audio.readframes(_audio.getnframes())
KEY = "medzen-b6-synthetic-client-key"
HEADERS = {
    "Authorization": f"Bearer {KEY}",
    "X-MedZen-Contract-Version": "medzen.speech.v1",
}
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
SESSION_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
SCHEMA = json.loads((
    ROOT / "platform/contracts/schemas/orchestrator-stream-v1/server-event.schema.json"
).read_bytes())
GOLDEN = json.loads((
    ROOT / "platform/contracts/fixtures/orchestrator-stream-v1/success-sequence.json"
).read_bytes())["server"]


class StepClock:
    def __init__(self):
        self.values = iter([0.000, 0.001, 0.003, 0.004, 0.007, 0.008, 0.012, 0.013])

    def __call__(self) -> float:
        return next(self.values)


def components(*, pipeline=None, limits=None, deterministic=False):
    service, auth = build_local_orchestrator()
    if deterministic:
        service.clock = StepClock()
        service.session_id_factory = lambda: uuid.UUID(
            "22222222-2222-4222-8222-222222222222"
        )
    store = InMemoryFinalResultStore(clock=lambda: 1.0)
    app = create_app(
        service,
        auth,
        pipeline=pipeline,
        limits=limits,
        final_store=store,
        clock=(lambda: 0.0) if deterministic else time.perf_counter,
        session_id_factory=(lambda: SESSION_ID) if deterministic else uuid.uuid4,
    )
    return app, service, store


def start(websocket, request_id=REQUEST_ID):
    websocket.send_json({
        "type": "start",
        "request_id": request_id,
        "language_hint": "en",
        "audio_format": "pcm_s16le/16000/mono",
    })


def assert_close(websocket, expected: int):
    with pytest.raises(WebSocketDisconnect) as caught:
        websocket.receive_json()
    assert caught.value.code == expected


def test_successful_stream_matches_golden_and_persists_finals_before_delivery(caplog):
    app, _, store = components(deterministic=True)
    caplog.set_level(logging.INFO, logger="medzen.orchestrator.streaming")
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            events = [websocket.receive_json()]
            websocket.send_bytes(PCM)
            events.append(websocket.receive_json())
            websocket.send_json({"type": "end_of_speech"})
            events.extend(websocket.receive_json() for _ in range(3))
            assert_close(websocket, 1000)
    for event in events:
        Draft202012Validator(
            SCHEMA, format_checker=FormatChecker()
        ).validate(event)
    assert events == GOLDEN
    stored = store.get(REQUEST_ID)
    assert stored is not None
    assert stored.delivered_count == 3
    assert [event["type"] for event in stored.events] == [
        "final_transcript", "reply_text", "completed"
    ]
    for forbidden in (
        KEY,
        events[1]["text"],
        events[2]["transcript"]["verbatim"],
        events[3]["text"],
        *(item["excerpt"] for item in events[3]["citations"]),
    ):
        assert forbidden not in caplog.text
    assert REQUEST_ID in caplog.text


def test_composed_b6_4_app_preserves_the_pinned_b6_3_multipart_request():
    app, _, _ = components()
    with TestClient(app) as client:
        response = client.post(
            "/v1/conversations/speech",
            headers=HEADERS,
            data={
                "request_id": "11111111-1111-4111-8111-111111111111",
                "language_hint": "en",
                "response_audio": "false",
            },
            files={"audio": ("synthetic.wav", WAV.read_bytes(), "audio/wav")},
        )
    assert response.status_code == 200
    assert response.json()["reply"]["tts_backend"] == "text_only"


@pytest.mark.parametrize(
    ("headers", "close_code"),
    [
        ({"X-MedZen-Contract-Version": "medzen.speech.v1"}, 4401),
        ({
            "Authorization": "Bearer wrong",
            "X-MedZen-Contract-Version": "medzen.speech.v1",
        }, 4403),
        ({"Authorization": f"Bearer {KEY}"}, 4406),
    ],
)
def test_upgrade_auth_and_contract_fail_with_exact_close_codes(headers, close_code):
    app, _, _ = components()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                "/v1/conversations/stream", headers=headers
            ):
                pass
    assert caught.value.code == close_code


def test_oversized_frame_and_vad_empty_audio_fail_closed():
    app, _, _ = components()
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(b"\x01\x00" * 32_769)
            error = websocket.receive_json()
            assert error["error"]["code"] == "PAYLOAD_TOO_LARGE"
            assert_close(websocket, 4429)
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket, "55555555-5555-4555-8555-555555555555")
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(b"\x00\x00" * 160)
            websocket.send_json({"type": "end_of_speech"})
            error = websocket.receive_json()
            assert error["error"]["code"] == "INVALID_EVENT"
            assert_close(websocket, 4422)


def test_audio_before_start_and_total_session_limit_fail_closed():
    app, _, _ = components(
        limits=StreamLimits(maximum_frame_bytes=8, maximum_session_audio_bytes=8)
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            websocket.send_bytes(b"\xe8\x03")
            assert_close(websocket, 4422)
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket, "66666666-6666-4666-8666-666666666666")
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(b"\xe8\x03" * 4)
            assert websocket.receive_json()["type"] == "partial_transcript"
            websocket.send_bytes(b"\xe8\x03" * 4)
            error = websocket.receive_json()
            assert error["error"]["code"] == "PAYLOAD_TOO_LARGE"
            assert_close(websocket, 4429)


def test_idle_timeout_returns_controlled_error_and_clean_close():
    app, _, _ = components(limits=StreamLimits(idle_timeout_s=0.01))
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            assert websocket.receive_json()["type"] == "ready"
            error = websocket.receive_json()
            assert error["error"] == {
                "code": "DEPENDENCY_UNAVAILABLE",
                "message": "stream idle timeout expired",
                "retryable": True,
            }
            assert_close(websocket, 4503)


class BlockingPipeline:
    def __init__(self):
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.cancel_calls = []

    async def complete(self, *, cancellation, **kwargs):
        self.started.set()
        try:
            await cancellation.wait()
            cancellation.checkpoint()
        finally:
            self.stopped.set()

    async def cancel(self, request_id, reason):
        self.cancel_calls.append((request_id, reason))


class MalformedPipeline:
    async def complete(self, **kwargs):
        return {}

    async def cancel(self, request_id, reason):
        return None


def test_malformed_pipeline_result_fails_closed_with_a_controlled_error():
    app, _, _ = components(pipeline=MalformedPipeline())
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(PCM)
            assert websocket.receive_json()["type"] == "partial_transcript"
            websocket.send_json({"type": "end_of_speech"})
            error = websocket.receive_json()
            assert error["error"] == {
                "code": "DEPENDENCY_UNAVAILABLE",
                "message": "streaming pipeline returned a malformed final result",
                "retryable": False,
            }
            assert_close(websocket, 4503)


def test_processing_control_timeout_cancels_and_reaps_pipeline_task():
    pipeline = BlockingPipeline()
    app, _, _ = components(
        pipeline=pipeline,
        limits=StreamLimits(idle_timeout_s=0.01),
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(PCM)
            assert websocket.receive_json()["type"] == "partial_transcript"
            websocket.send_json({"type": "end_of_speech"})
            assert pipeline.started.wait(1)
            error = websocket.receive_json()
            assert error["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            assert_close(websocket, 4503)
    assert pipeline.stopped.wait(1)
    assert pipeline.cancel_calls == [(REQUEST_ID, "processing_control_refusal")]


def test_audio_while_processing_refuses_and_cancels_downstream_work():
    pipeline = BlockingPipeline()
    app, _, _ = components(pipeline=pipeline)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(PCM)
            assert websocket.receive_json()["type"] == "partial_transcript"
            websocket.send_json({"type": "end_of_speech"})
            assert pipeline.started.wait(1)
            websocket.send_bytes(b"\xe8\x03" * 8)
            error = websocket.receive_json()
            assert error["error"]["code"] == "INVALID_EVENT"
            assert_close(websocket, 4422)
    assert pipeline.stopped.wait(1)
    assert pipeline.cancel_calls == [(REQUEST_ID, "invalid_processing_control")]


@pytest.mark.parametrize(
    "control",
    [
        {"type": "cancel", "reason": "synthetic-client-cancel"},
        {"type": "barge_in"},
    ],
)
def test_cancel_and_barge_in_propagate_and_close_within_250_ms(control):
    pipeline = BlockingPipeline()
    app, _, _ = components(pipeline=pipeline)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(PCM)
            assert websocket.receive_json()["type"] == "partial_transcript"
            websocket.send_json({"type": "end_of_speech"})
            assert pipeline.started.wait(1)
            began = time.perf_counter()
            websocket.send_json(control)
            cancelled = websocket.receive_json()
            elapsed_ms = (time.perf_counter() - began) * 1000
            assert cancelled["type"] == "cancelled"
            assert cancelled["reason"] == control["type"]
            assert cancelled["cancellation_latency_ms"] <= 250
            assert elapsed_ms < 250
            assert_close(websocket, 4000)
    assert pipeline.cancel_calls == [(
        REQUEST_ID,
        control.get("reason") or control["type"],
    )]
    assert pipeline.stopped.wait(1)


def test_client_disconnect_cancels_work_without_server_error_or_final_loss_claim():
    pipeline = BlockingPipeline()
    app, _, store = components(pipeline=pipeline)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/conversations/stream", headers=HEADERS
        ) as websocket:
            start(websocket)
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_bytes(PCM)
            assert websocket.receive_json()["type"] == "partial_transcript"
            websocket.send_json({"type": "end_of_speech"})
            assert pipeline.started.wait(1)
    assert pipeline.stopped.wait(1)
    assert pipeline.cancel_calls == [(REQUEST_ID, "client_disconnect")]
    assert store.get(REQUEST_ID) is None


def test_open_streaming_breaker_is_visible_in_readiness():
    app, _, _ = components()
    with TestClient(app) as client:
        guard = client.app.state.streaming_guard
        for _ in range(guard.breaker.timeout_threshold):
            guard.breaker.record_failure(timeout=True)
        ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["streaming_breaker_state"] == "open"
    assert ready.json()["error_code"] == "STREAMING_CIRCUIT_OPEN"


def test_missing_partial_source_refuses_readiness_before_a_stream_can_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, auth = build_local_orchestrator()
    missing = tmp_path / "deliberately-not-ready.json"
    monkeypatch.setenv("MEDZEN_STREAM_PARTIAL_FIXTURE", str(missing))
    monkeypatch.setenv("MEDZEN_STREAM_PARTIAL_FIXTURE_SHA256", "0" * 64)
    app = create_app(service, auth)
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["streaming_partial_source_loaded"] is False
        assert ready.json()["error_code"] == (
            "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
        )
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                "/v1/conversations/stream", headers=HEADERS
            ):
                pass
    assert caught.value.code == 4503
    assert caught.value.reason == "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
