from __future__ import annotations

import asyncio
import struct
import sys
import wave
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-orchestrator"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_orchestrator.streaming import (  # noqa: E402
    EventBuffer,
    FinalDeliveryInterrupted,
    InMemoryFinalResultStore,
    StreamLimits,
    StreamRefusal,
    StreamingPipelineGuard,
    deliver_final_batch,
    pcm_s16le_to_wav,
)
from medzen_speech_orchestrator.streaming_resilience import (  # noqa: E402
    CircuitBreaker,
    State,
)
from medzen_speech_orchestrator.vad import (  # noqa: E402
    LocalEnergyVAD,
    SileroVADAdapter,
    VADRefusal,
)


WAV = ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav"


def pcm_fixture() -> bytes:
    with wave.open(str(WAV), "rb") as audio:
        return audio.readframes(audio.getnframes())


def test_local_vad_and_injected_silero_adapter_share_the_interface():
    silence = b"\x00\x00" * 160
    active = struct.pack("<h", 2000) * 160
    local = LocalEnergyVAD(threshold=0.01)
    assert local.detect(silence).is_speech is False
    assert local.detect(active).is_speech is True
    calls = []

    def runner(samples, sample_rate_hz):
        calls.append((len(samples), sample_rate_hz, max(samples)))
        return 0.75

    silero = SileroVADAdapter(runner, threshold=0.5)
    result = silero.detect(active)
    assert result.is_speech is True and result.probability == 0.75
    assert calls == [(160, 16000, pytest.approx(2000 / 32768))]


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan"), True, "0.8"])
def test_silero_adapter_rejects_malformed_probabilities(probability):
    adapter = SileroVADAdapter(lambda samples, sample_rate: probability)
    with pytest.raises(VADRefusal, match="invalid probability"):
        adapter.detect(b"\x01\x00" * 8)


def test_vad_rejects_partial_pcm_samples():
    with pytest.raises(VADRefusal, match="complete signed 16-bit"):
        LocalEnergyVAD().detect(b"\x01")


def test_partial_queue_is_exactly_four_and_drops_only_the_oldest():
    buffer = EventBuffer(StreamLimits())
    for sequence in range(1, 7):
        buffer.put_partial({"sequence": sequence})
    assert buffer.partials.max_size == 4
    assert buffer.partials_dropped == 2
    assert [item["sequence"] for item in buffer.drain_partials()] == [3, 4, 5, 6]


def test_audio_queue_is_exactly_eight_and_pauses_then_resumes_upstream():
    buffer = EventBuffer(StreamLimits())
    for sequence in range(1, 9):
        assert buffer.offer_audio({"sequence": sequence}) is True
    assert buffer.audio.max_size == 8
    assert buffer.offer_audio({"sequence": 9}) is False
    assert buffer.audio_upstream_paused is True
    drained = buffer.drain_audio()
    assert [item["sequence"] for item in drained] == list(range(1, 9))
    assert buffer.audio_upstream_paused is False


def test_final_queue_refuses_atomically_and_never_drops_existing_results():
    buffer = EventBuffer(StreamLimits())
    original = [{"sequence": value} for value in range(1, 16)]
    buffer.put_final_batch(original)
    with pytest.raises(StreamRefusal, match="complete batch"):
        buffer.put_final_batch([{"sequence": 16}, {"sequence": 17}])
    assert buffer.finals.max_size == 16
    assert buffer.drain_finals() == original


class DisconnectingSender:
    def __init__(self, store: InMemoryFinalResultStore, request_id: str):
        self.store = store
        self.request_id = request_id
        self.calls = 0

    async def send_json(self, data):
        stored = self.store.get(self.request_id)
        assert stored is not None, "final batch must exist before first send"
        assert len(stored.events) == 3
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("synthetic slow-client disconnect")


def test_complete_final_batch_survives_a_disconnect_during_delivery():
    async def scenario():
        request_id = "33333333-3333-4333-8333-333333333333"
        events = [{"type": value} for value in ("final", "reply", "completed")]
        store = InMemoryFinalResultStore(clock=lambda: 1.0)
        sender = DisconnectingSender(store, request_id)
        with pytest.raises(FinalDeliveryInterrupted, match="interrupted"):
            await deliver_final_batch(sender, store, request_id, events)
        stored = store.get(request_id)
        assert stored is not None
        assert list(stored.events) == events
        assert stored.persisted_at == 1.0
        assert stored.delivered_count == 1

    asyncio.run(scenario())


def test_final_persistence_refusal_is_not_misclassified_as_a_disconnect():
    class Sender:
        async def send_json(self, data):
            raise AssertionError("delivery must not start when persistence refuses")

    async def scenario():
        request_id = "33333333-3333-4333-8333-333333333333"
        store = InMemoryFinalResultStore()
        store.persist(request_id, [{"type": "existing"}])
        with pytest.raises(StreamRefusal, match="already exists"):
            await deliver_final_batch(
                Sender(), store, request_id, [{"type": "replacement"}]
            )

    asyncio.run(scenario())


def test_pcm_conversion_reconstructs_the_bound_wav_byte_for_byte():
    assert pcm_s16le_to_wav(pcm_fixture()) == WAV.read_bytes()


class HangingPipeline:
    def __init__(self):
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        await asyncio.Event().wait()

    async def cancel(self, request_id, reason):
        return None


def test_three_pipeline_timeouts_open_the_shared_breaker_and_short_circuit():
    async def scenario():
        pipeline = HangingPipeline()
        breaker = CircuitBreaker(
            name="stream-test",
            failure_threshold=99,
            timeout_threshold=3,
            window_s=30,
            open_duration_s=20,
        )
        guard = StreamingPipelineGuard(pipeline, breaker, timeout_s=0.001)
        for _ in range(3):
            with pytest.raises(StreamRefusal, match="timed out"):
                await guard.complete()
        assert breaker.state is State.OPEN
        with pytest.raises(StreamRefusal, match="circuit is open"):
            await guard.complete()
        assert pipeline.calls == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "overrides",
    [
        {"partial_queue_size": 5},
        {"audio_queue_size": 9},
        {"final_queue_size": 15},
        {"cancellation_budget_s": 0.251},
    ],
)
def test_contract_queue_and_cancellation_limits_cannot_drift(overrides):
    with pytest.raises(ValueError, match="differ from the B6.4 contract"):
        StreamLimits(**overrides)
