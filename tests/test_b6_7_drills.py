"""B6.7 failure drills (Base v5 §4), executable locally.

The three drills the plan requires, composed from the same harnesses the
green suites use — each proves degradation per A6 with no 500 cascade:

  1. kill Fish            -> the answer survives as tts_backend=text_only
  2. kill ASR mid-stream  -> clean controlled session error, no hang
  3. open the LLM breaker -> controlled error; no invented clinical text

Plus the queue-semantics unit the conformance matrix names: partials
drop-oldest, finals are never displaced.
"""

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
for service_dir in ("speech-orchestrator", "speech-tts-gateway", "llm-gateway"):
    sys.path.insert(0, str(ROOT / "services" / service_dir))

from medzen_speech_orchestrator.streaming import (  # noqa: E402
    EventBuffer,
    StreamLimits,
)

sys.path.insert(0, str(ROOT / "tests"))
from test_speech_orchestrator_streaming import (  # noqa: E402
    HEADERS,
    PCM,
    assert_close,
    components,
    start,
)
from test_tts_gateway import REQUEST, fish_breaker, service  # noqa: E402


# --------------------------------------------------------------------------
# queue semantics (matrix: backpressure)
# --------------------------------------------------------------------------

def test_partial_queue_drops_oldest_and_finals_never_dropped():
    buffer = EventBuffer(StreamLimits())
    for index in range(10):
        buffer.partials.put({"seq": index})
    assert buffer.partials.dropped == 6, "partials beyond 4 drop the OLDEST"
    kept = []
    while (item := buffer.partials.get()) is not None:
        kept.append(item["seq"])
    assert kept == [6, 7, 8, 9], "newest four survive"
    for index in range(16):
        buffer.finals.put({"final": index})
    assert buffer.finals.dropped == 0, "finals are NEVER dropped"
    with pytest.raises(Exception):
        buffer.finals.put({"final": 16})  # block semantics refuse, not displace


# --------------------------------------------------------------------------
# drill 1 — kill Fish
# --------------------------------------------------------------------------

def test_drill_kill_fish_degrades_to_text_only():
    """Fish dies outright (error then timeout): every response still carries
    the full clinical text with tts_backend=text_only and no exception."""
    gateway, provider = service(["error", "timeout", "error"])
    for _ in range(3):
        response = gateway.synthesize(dict(REQUEST))
        assert response["tts_backend"] == "text_only"
        assert response["text"] == REQUEST["text"]
        assert response["audio_url"] is None


# --------------------------------------------------------------------------
# drill 2 — kill ASR mid-stream
# --------------------------------------------------------------------------

class DyingASRPipeline:
    """The streaming pipeline's ASR dependency dies after the stream starts."""

    def __init__(self):
        self.cancel_calls = []

    async def complete(self, **kwargs):
        raise RuntimeError("asr pod terminated mid-stream (drill)")

    async def cancel(self, request_id, reason):
        self.cancel_calls.append((request_id, reason))


def test_drill_asr_death_mid_stream_errors_cleanly():
    app, _, _ = components(pipeline=DyingASRPipeline())
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
            assert error["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            assert error["error"]["retryable"] is True
            message = error["error"]["message"].lower()
            assert "drill" not in message and "terminated" not in message, (
                "internal exception text must never leak to the client")
            assert_close(websocket, 4503)  # clean close, not a hang


# --------------------------------------------------------------------------
# drill 3 — open LLM breaker
# --------------------------------------------------------------------------

def test_drill_open_llm_breaker_controlled_error():
    """With the bedrock breaker forced open, the turn fails with a controlled
    retryable error and NO invented clinical text (A6: no textual fallback
    for understanding)."""
    from test_speech_orchestrator import local_app, request

    app, service_obj = local_app(deterministic=True)
    breaker = service_obj.llm._gateway.breaker
    while breaker.allow():
        breaker.record_failure(timeout=False)
    assert not breaker.allow(), "breaker must be open for the drill"
    with TestClient(app) as client:
        response = request(client)
    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert payload["error"]["retryable"] is True
    assert "reply" not in payload or not payload.get("reply", {}).get("text"), (
        "an open LLM breaker must never yield invented clinical text"
    )
