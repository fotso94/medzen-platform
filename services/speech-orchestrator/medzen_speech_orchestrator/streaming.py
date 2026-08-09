from __future__ import annotations

import asyncio
import copy
import io
import json
import time
import wave
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from .orchestrator import SpeechOrchestrator
from .registry import RegistryRoute
from .streaming_resilience import BoundedQueue, CircuitBreaker
from .vad import VADRefusal, VoiceActivityDetector


class StreamRefusal(RuntimeError):
    def __init__(self, code: str, message: str, close_code: int, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.close_code = close_code
        self.retryable = retryable


class StreamCancelled(RuntimeError):
    pass


class FinalDeliveryInterrupted(RuntimeError):
    """The client disconnected after the complete final batch was persisted."""


@dataclass(frozen=True)
class StreamLimits:
    maximum_frame_bytes: int = 65_536
    maximum_session_audio_bytes: int = 26_214_400
    idle_timeout_s: float = 30.0
    pipeline_timeout_s: float = 30.0
    cancellation_budget_s: float = 0.250
    partial_queue_size: int = 4
    audio_queue_size: int = 8
    final_queue_size: int = 16

    def __post_init__(self) -> None:
        if (
            self.maximum_frame_bytes < 1
            or self.maximum_session_audio_bytes < self.maximum_frame_bytes
            or self.idle_timeout_s <= 0
            or self.pipeline_timeout_s <= 0
            or not 0 < self.cancellation_budget_s <= 0.250
            or self.partial_queue_size != 4
            or self.audio_queue_size != 8
            or self.final_queue_size != 16
        ):
            raise ValueError("streaming limits differ from the B6.4 contract")


class StreamState(str, Enum):
    RECEIVING = "receiving"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"


@dataclass
class CancellationToken:
    clock: Callable[[], float] = time.perf_counter
    reason: str | None = field(default=None, init=False)
    requested_at: float | None = field(default=None, init=False)
    _event: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str) -> float:
        if not self._event.is_set():
            self.reason = reason
            self.requested_at = self.clock()
            self._event.set()
        assert self.requested_at is not None
        return self.requested_at

    async def wait(self) -> None:
        await self._event.wait()

    def checkpoint(self) -> None:
        if self.cancelled:
            raise StreamCancelled(self.reason or "cancelled")


class EventBuffer:
    """The three A6 queues, kept separate so finals cannot be displaced."""

    def __init__(self, limits: StreamLimits):
        self.partials = BoundedQueue(limits.partial_queue_size, "drop_oldest")
        self.audio = BoundedQueue(limits.audio_queue_size, "pause_upstream")
        self.finals = BoundedQueue(limits.final_queue_size, "block")

    @property
    def partials_dropped(self) -> int:
        return self.partials.dropped

    @property
    def audio_upstream_paused(self) -> bool:
        return self.audio.paused

    def put_partial(self, event: dict[str, Any]) -> None:
        self.partials.put(copy.deepcopy(event))

    def offer_audio(self, event: dict[str, Any]) -> bool:
        return self.audio.put(copy.deepcopy(event))

    def put_final_batch(self, events: list[dict[str, Any]]) -> None:
        if not events or len(self.finals) + len(events) > self.finals.max_size:
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE",
                "final-result queue cannot accept the complete batch",
                4503,
                True,
            )
        # The capacity check makes the following insertions all-or-nothing in
        # this session-owned, single-task buffer. No await can interleave here.
        for event in events:
            self.finals.put(copy.deepcopy(event))

    @staticmethod
    def _drain(queue: BoundedQueue) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        while len(queue):
            values.append(queue.get())
        return values

    def drain_partials(self) -> list[dict[str, Any]]:
        return self._drain(self.partials)

    def drain_audio(self) -> list[dict[str, Any]]:
        return self._drain(self.audio)

    def drain_finals(self) -> list[dict[str, Any]]:
        return self._drain(self.finals)


@dataclass
class StoredFinalBatch:
    events: tuple[dict[str, Any], ...]
    persisted_at: float
    delivered_count: int = 0


class InMemoryFinalResultStore:
    """Process-local B6.4 proof store; B6.6 must select durable storage."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self.clock = clock
        self._batches: dict[str, StoredFinalBatch] = {}

    def persist(self, request_id: str, events: list[dict[str, Any]]) -> StoredFinalBatch:
        if request_id in self._batches:
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE",
                "a final result already exists for this request",
                4503,
                False,
            )
        stored = StoredFinalBatch(
            events=tuple(copy.deepcopy(events)), persisted_at=self.clock()
        )
        self._batches[request_id] = stored
        return stored

    def mark_delivered(self, request_id: str, event: dict[str, Any]) -> None:
        stored = self._batches[request_id]
        if (
            stored.delivered_count >= len(stored.events)
            or stored.events[stored.delivered_count] != event
        ):
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE",
                "final-result delivery order is inconsistent",
                4503,
                False,
            )
        stored.delivered_count += 1

    def get(self, request_id: str) -> StoredFinalBatch | None:
        return self._batches.get(request_id)


class JsonSender(Protocol):
    async def send_json(self, data: Any) -> None: ...


async def deliver_final_batch(
    sender: JsonSender,
    store: InMemoryFinalResultStore,
    request_id: str,
    events: list[dict[str, Any]],
) -> None:
    store.persist(request_id, events)
    for event in events:
        try:
            await sender.send_json(event)
        except (ConnectionError, RuntimeError) as exc:
            raise FinalDeliveryInterrupted(
                "final-result delivery was interrupted"
            ) from exc
        store.mark_delivered(request_id, event)


class SyntheticPartialSource:
    def __init__(self, binding: Path):
        try:
            value = json.loads(binding.read_bytes())
            transcript = value["transcript"]["normalized"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE",
                "synthetic partial source is unavailable",
                4503,
                True,
            ) from exc
        if (
            value.get("classification") != "B6_3_LOCAL_SYNTHETIC_NON_SPEECH"
            or not isinstance(transcript, str)
            or not transcript
        ):
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE",
                "synthetic partial source is not local-fixture scoped",
                4503,
                True,
            )
        self.words = tuple(transcript.split())
        self.index = 0

    def next(self) -> str | None:
        if self.index >= len(self.words):
            return None
        self.index += 1
        return " ".join(self.words[:self.index])


class StreamingSession:
    def __init__(
        self,
        *,
        request_id: str,
        session_id: str,
        route: RegistryRoute,
        vad: VoiceActivityDetector,
        partial_source: SyntheticPartialSource,
        limits: StreamLimits,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.request_id = request_id
        self.session_id = session_id
        self.route = route
        self.vad = vad
        self.partial_source = partial_source
        self.limits = limits
        self.clock = clock
        self.state = StreamState.RECEIVING
        self.buffer = EventBuffer(limits)
        self.cancellation = CancellationToken(clock)
        self.total_audio_bytes = 0
        self.speech_audio = bytearray()
        self.partial_sequence = 0
        self.started_at = clock()

    def event(
        self, kind: str, *, versions: dict[str, str | None] | None = None,
        **fields: Any
    ) -> dict[str, Any]:
        return {
            "type": kind,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "model_versions": dict(versions or self.route.model_versions),
            **fields,
        }

    def ready_event(self) -> dict[str, Any]:
        return self.event("ready")

    def ingest(self, frame: bytes) -> None:
        if self.state is not StreamState.RECEIVING:
            raise StreamRefusal(
                "INVALID_EVENT", "audio arrived outside the receiving state", 4422, False
            )
        if not frame or len(frame) % 2:
            raise StreamRefusal(
                "INVALID_EVENT", "audio frame is empty or has a partial sample", 4422, False
            )
        if len(frame) > self.limits.maximum_frame_bytes:
            raise StreamRefusal(
                "PAYLOAD_TOO_LARGE", "audio frame exceeds 65536 bytes", 4429, False
            )
        self.total_audio_bytes += len(frame)
        if self.total_audio_bytes > self.limits.maximum_session_audio_bytes:
            raise StreamRefusal(
                "PAYLOAD_TOO_LARGE", "session audio exceeds 26214400 bytes", 4429, False
            )
        try:
            result = self.vad.detect(frame)
        except VADRefusal as exc:
            raise StreamRefusal(
                "INVALID_EVENT", "VAD refused the audio frame", 4422, False
            ) from exc
        if not result.is_speech:
            return
        self.speech_audio.extend(frame)
        partial = self.partial_source.next()
        if partial is not None:
            self.partial_sequence += 1
            self.buffer.put_partial(self.event(
                "partial_transcript",
                sequence=self.partial_sequence,
                text=partial,
            ))

    def finish_audio(self) -> bytes:
        if self.state is not StreamState.RECEIVING:
            raise StreamRefusal(
                "INVALID_EVENT", "end_of_speech is out of sequence", 4422, False
            )
        if not self.speech_audio:
            raise StreamRefusal(
                "INVALID_EVENT", "VAD found no speech activity", 4422, False
            )
        self.state = StreamState.PROCESSING
        return bytes(self.speech_audio)

    def cancel(self, reason: str) -> float:
        requested = self.cancellation.cancel(reason)
        self.state = StreamState.CANCELLED
        return requested

    def disconnect(self) -> None:
        self.cancellation.cancel("client_disconnect")
        self.state = StreamState.DISCONNECTED

    def final_batch(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        if self.state is not StreamState.PROCESSING:
            raise StreamRefusal(
                "INVALID_EVENT", "final result arrived outside processing", 4422, False
            )
        try:
            transcript = result["transcript"]
            reply = result["reply"]
            versions = result["model_versions"]
            language = result["language"]
            latency_ms = result["latency_ms"]
            structurally_valid = (
                isinstance(transcript, dict)
                and all(
                    isinstance(transcript.get(field), str)
                    for field in ("verbatim", "normalized", "normalization_version")
                )
                and isinstance(reply, dict)
                and isinstance(reply.get("text"), str)
                and isinstance(reply.get("citations"), list)
                and reply.get("tts_backend") in {"fish", "self_hosted", "text_only"}
                and isinstance(versions, dict)
                and set(versions) == {"asr", "registry_snapshot", "llm", "rag", "tts"}
                and isinstance(language, str)
                and isinstance(latency_ms, dict)
                and set(latency_ms) == {"total", "asr", "rag", "llm", "tts"}
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and value >= 0
                    for value in latency_ms.values()
                )
            )
        except (KeyError, TypeError) as exc:
            structurally_valid = False
            cause: Exception | None = exc
        else:
            cause = None
        if not structurally_valid:
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE",
                "streaming pipeline returned a malformed final result",
                4503,
                False,
            ) from cause
        batch = [
            self.event(
                "final_transcript",
                transcript=transcript,
                language=language,
            ),
            self.event(
                "reply_text",
                versions=versions,
                text=reply["text"],
                citations=reply["citations"],
                tts_backend=reply["tts_backend"],
            ),
            self.event(
                "completed",
                versions=versions,
                latency_ms=latency_ms,
            ),
        ]
        self.buffer.put_final_batch(batch)
        self.state = StreamState.COMPLETED
        return self.buffer.drain_finals()


def pcm_s16le_to_wav(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % 2:
        raise StreamRefusal(
            "INVALID_EVENT", "PCM audio is empty or incomplete", 4422, False
        )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(pcm)
    return output.getvalue()


class StreamingPipeline(Protocol):
    async def complete(
        self,
        *,
        pcm_audio: bytes,
        request_id: str,
        language_hint: str | None,
        cancellation: CancellationToken,
    ) -> dict[str, Any]: ...

    async def cancel(self, request_id: str, reason: str) -> None: ...


class LocalStreamingPipeline:
    def __init__(self, orchestrator: SpeechOrchestrator):
        self.orchestrator = orchestrator
        self.cancel_calls: list[tuple[str, str]] = []

    async def complete(
        self,
        *,
        pcm_audio: bytes,
        request_id: str,
        language_hint: str | None,
        cancellation: CancellationToken,
    ) -> dict[str, Any]:
        cancellation.checkpoint()
        wav = pcm_s16le_to_wav(pcm_audio)
        _, result = await asyncio.to_thread(
            self.orchestrator.handle,
            audio=wav,
            request_id=request_id,
            language_hint=language_hint,
        )
        cancellation.checkpoint()
        return result

    async def cancel(self, request_id: str, reason: str) -> None:
        self.cancel_calls.append((request_id, reason))
        await asyncio.sleep(0)


class StreamingPipelineGuard:
    def __init__(
        self,
        pipeline: StreamingPipeline,
        breaker: CircuitBreaker,
        timeout_s: float,
    ):
        self.pipeline = pipeline
        self.breaker = breaker
        self.timeout_s = timeout_s

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        if not self.breaker.allow():
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE", "streaming pipeline circuit is open", 4503, True
            )
        try:
            result = await asyncio.wait_for(
                self.pipeline.complete(**kwargs), timeout=self.timeout_s
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            self.breaker.record_failure(timeout=True)
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE", "streaming pipeline timed out", 4503, True
            ) from exc
        except StreamCancelled:
            raise
        except Exception as exc:
            self.breaker.record_failure()
            raise StreamRefusal(
                "DEPENDENCY_UNAVAILABLE", "streaming pipeline is unavailable", 4503, True
            ) from exc
        self.breaker.record_success()
        return result

    async def cancel(
        self, request_id: str, reason: str, budget_s: float
    ) -> bool:
        try:
            await asyncio.wait_for(
                self.pipeline.cancel(request_id, reason), timeout=budget_s
            )
        except Exception:
            return False
        return True
