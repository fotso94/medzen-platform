from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .app import create_app as create_file_app
from .auth import AuthRefusal, KeyStore
from .orchestrator import SpeechOrchestrator
from .registry import RegistryRefusal
from .streaming import (
    FinalDeliveryInterrupted,
    InMemoryFinalResultStore,
    LocalStreamingPipeline,
    StreamCancelled,
    StreamLimits,
    StreamRefusal,
    StreamingPipeline,
    StreamingPipelineGuard,
    StreamingSession,
    SyntheticPartialSource,
    deliver_final_batch,
)
from .streaming_resilience import CircuitBreaker, State, load_config
from .vad import LocalEnergyVAD, VoiceActivityDetector


LOGGER = logging.getLogger("medzen.orchestrator.streaming")
CONTRACT_VERSION = "medzen.speech.v1"
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
AUDIO_FORMAT = "pcm_s16le/16000/mono"
PARTIAL_SOURCE_PATH_ENV = "MEDZEN_STREAM_PARTIAL_FIXTURE"
PARTIAL_SOURCE_SHA256_ENV = "MEDZEN_STREAM_PARTIAL_FIXTURE_SHA256"


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _breaker() -> CircuitBreaker:
    defaults = load_config()["circuit_breakers"]["defaults"]
    return CircuitBreaker(
        name="orchestrator-stream",
        failure_threshold=defaults["failure_threshold"],
        timeout_threshold=defaults["timeout_threshold"],
        window_s=defaults["window_s"],
        open_duration_s=defaults["open_duration_s"],
        half_open_max_calls=defaults["half_open_max_calls"],
    )


def _partial_source() -> SyntheticPartialSource:
    path = Path(os.environ.get(
        PARTIAL_SOURCE_PATH_ENV,
        _root() / "platform/testdata/orchestrator/asr-fixture.json",
    ))
    expected_sha256 = os.environ.get(PARTIAL_SOURCE_SHA256_ENV)
    return SyntheticPartialSource(path, expected_sha256)


def _log(
    *,
    event_type: str,
    request_id: str = "absent",
    session_id: str | None = None,
    language: str = "absent",
    model_versions: dict[str, str | None] | None = None,
    latency_ms: float = 0.0,
    status_code: int,
    error_code: str | None = None,
) -> None:
    LOGGER.info(json.dumps({
        "event_type": event_type,
        "request_id": request_id,
        "hashed_session_id": (
            hashlib.sha256(session_id.encode("utf-8")).hexdigest()
            if session_id is not None else "absent"
        ),
        "language": language,
        "model_versions": model_versions,
        "latency_ms": round(latency_ms, 3),
        "status_code": status_code,
        "error_code": error_code,
    }, sort_keys=True))


def _start(value: Any) -> tuple[str, str | None]:
    if not isinstance(value, dict) or value.get("type") != "start":
        raise StreamRefusal("INVALID_EVENT", "first event must be start", 4422, False)
    try:
        request_id = str(uuid.UUID(str(value.get("request_id"))))
    except (ValueError, TypeError, AttributeError) as exc:
        raise StreamRefusal(
            "INVALID_EVENT", "start request_id must be a UUID", 4422, False
        ) from exc
    language = value.get("language_hint")
    if language is not None and (
        not isinstance(language, str) or LANGUAGE_RE.fullmatch(language) is None
    ):
        raise StreamRefusal(
            "INVALID_EVENT", "language_hint must be a lowercase language code", 4422, False
        )
    audio_format = value.get("audio_format", AUDIO_FORMAT)
    if audio_format != AUDIO_FORMAT:
        raise StreamRefusal(
            "INVALID_EVENT", "audio_format must be pcm_s16le/16000/mono", 4422, False
        )
    return request_id, language


def _control(message: dict[str, Any]) -> tuple[str, str | None]:
    if message.get("bytes") is not None:
        return "audio", None
    try:
        value = json.loads(message.get("text") or "{}")
    except json.JSONDecodeError as exc:
        raise StreamRefusal(
            "INVALID_EVENT", "stream control is malformed", 4422, False
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise StreamRefusal(
            "INVALID_EVENT", "stream control is malformed", 4422, False
        )
    kind = value["type"]
    if kind == "cancel":
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason or len(reason) > 128:
            raise StreamRefusal(
                "INVALID_EVENT", "cancel reason is required and bounded", 4422, False
            )
        return kind, reason
    if kind in {"barge_in", "end_of_speech"}:
        return kind, None
    raise StreamRefusal("INVALID_EVENT", "stream control is not allowed", 4422, False)


async def _receive(websocket: WebSocket, timeout_s: float) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(websocket.receive(), timeout=timeout_s)
    except TimeoutError as exc:
        raise StreamRefusal(
            "DEPENDENCY_UNAVAILABLE", "stream idle timeout expired", 4503, True
        ) from exc


async def _cancel_processing(
    *,
    session: StreamingSession,
    guard: StreamingPipelineGuard,
    processing: asyncio.Task | None,
    reason: str,
) -> tuple[bool, float]:
    started = session.cancel(reason)
    hook = await guard.cancel(
        session.request_id, reason, session.limits.cancellation_budget_s
    )
    if processing is not None:
        if not processing.done():
            processing.cancel()
        with contextlib.suppress(
            asyncio.CancelledError, StreamCancelled, StreamRefusal
        ):
            await processing
    latency = (session.clock() - started) * 1000
    return hook and latency <= session.limits.cancellation_budget_s * 1000, latency


async def _await_pipeline_or_control(
    *,
    websocket: WebSocket,
    session: StreamingSession,
    guard: StreamingPipelineGuard,
    processing: asyncio.Task,
) -> tuple[str, Any]:
    while True:
        receiving = asyncio.create_task(
            _receive(websocket, session.limits.idle_timeout_s)
        )
        done, _ = await asyncio.wait(
            {processing, receiving}, return_when=asyncio.FIRST_COMPLETED
        )
        if receiving in done:
            try:
                message = receiving.result()
                kind, reason = (
                    ("disconnect", None)
                    if message["type"] == "websocket.disconnect"
                    else _control(message)
                )
            except StreamRefusal:
                session.cancel("processing_control_refusal")
                await guard.cancel(
                    session.request_id,
                    "processing_control_refusal",
                    session.limits.cancellation_budget_s,
                )
                processing.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError, StreamCancelled, StreamRefusal
                ):
                    await processing
                raise
            if kind == "disconnect":
                session.disconnect()
                await guard.cancel(
                    session.request_id,
                    "client_disconnect",
                    session.limits.cancellation_budget_s,
                )
                processing.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError, StreamCancelled, StreamRefusal
                ):
                    await processing
                return "disconnected", None
            if kind in {"cancel", "barge_in"}:
                success, latency = await _cancel_processing(
                    session=session,
                    guard=guard,
                    processing=processing,
                    reason=reason or kind,
                )
                return "cancelled", (success, latency, kind)
            await _cancel_processing(
                session=session,
                guard=guard,
                processing=processing,
                reason="invalid_processing_control",
            )
            raise StreamRefusal(
                "INVALID_EVENT", "only cancel or barge_in is valid while processing",
                4422, False,
            )
        receiving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receiving
        return "result", processing.result()


def create_app(
    orchestrator: SpeechOrchestrator | None = None,
    auth: KeyStore | None = None,
    *,
    pipeline: StreamingPipeline | None = None,
    vad_factory: Callable[[], VoiceActivityDetector] = LocalEnergyVAD,
    limits: StreamLimits | None = None,
    final_store: InMemoryFinalResultStore | None = None,
    clock: Callable[[], float] = time.perf_counter,
    session_id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> FastAPI:
    supplied_orchestrator = orchestrator
    supplied_auth = auth
    supplied_pipeline = pipeline
    configured_limits = limits or StreamLimits()
    store = final_store or InMemoryFinalResultStore(clock)
    app = create_file_app(supplied_orchestrator, supplied_auth)
    base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def streaming_lifespan(application: FastAPI):
        async with base_lifespan(application):
            try:
                service = application.state.orchestrator
                if service is None or application.state.auth is None:
                    raise RuntimeError("file-mode orchestrator is not ready")
                runtime = supplied_pipeline or LocalStreamingPipeline(service)
                partial_source = _partial_source()
                application.state.streaming_guard = StreamingPipelineGuard(
                    runtime, _breaker(), configured_limits.pipeline_timeout_s
                )
                application.state.streaming_partial_source = partial_source
                application.state.streaming_error = None
                application.state.final_result_store = store
            except Exception as exc:
                application.state.streaming_guard = None
                application.state.streaming_partial_source = None
                application.state.streaming_error = getattr(
                    exc, "reason_code", type(exc).__name__
                )
                application.state.orchestrator = None
                application.state.auth = None
            yield

    app.router.lifespan_context = streaming_lifespan

    @app.middleware("http")
    async def streaming_readiness(request: Request, call_next):
        if request.url.path != "/readyz":
            return await call_next(request)
        service = getattr(request.app.state, "orchestrator", None)
        key_store = getattr(request.app.state, "auth", None)
        guard = getattr(request.app.state, "streaming_guard", None)
        partial_source = getattr(
            request.app.state, "streaming_partial_source", None
        )
        state = guard.breaker.state if guard is not None else None
        ready = (
            service is not None
            and key_store is not None
            and partial_source is not None
            and state is State.CLOSED
        )
        payload: dict[str, Any] = {
            "ready": ready,
            "mode": request.app.state.mode,
            "registry_loaded": service is not None,
            "authentication_loaded": key_store is not None,
            "streaming_ready": guard is not None,
            "streaming_partial_source_loaded": partial_source is not None,
            "streaming_breaker_state": state.value if state is not None else "unavailable",
            "external_network_access": False,
        }
        if service is not None:
            payload["registry_snapshot"] = service.registry_snapshot
        if not ready:
            payload["error_code"] = (
                getattr(request.app.state, "streaming_error", None)
                or "STREAMING_CIRCUIT_OPEN"
            )
        return JSONResponse(payload, status_code=200 if ready else 503)

    @app.websocket("/v1/conversations/stream")
    async def stream(websocket: WebSocket):
        started = clock()
        request_id = "absent"
        session: StreamingSession | None = None
        service = websocket.app.state.orchestrator
        key_store = websocket.app.state.auth
        guard = websocket.app.state.streaming_guard
        partial_source = websocket.app.state.streaming_partial_source
        if (
            service is None
            or key_store is None
            or guard is None
            or partial_source is None
        ):
            reason_code = (
                websocket.app.state.streaming_error or "STREAMING_NOT_READY"
            )
            await websocket.close(code=4503, reason=reason_code)
            _log(
                event_type="stream_refused", status_code=4503,
                error_code="DEPENDENCY_UNAVAILABLE"
            )
            return
        try:
            key_store.authenticate(websocket.headers.get("Authorization"))
        except AuthRefusal as exc:
            close_code = 4401 if exc.code == "AUTH_REQUIRED" else 4403
            await websocket.close(code=close_code)
            _log(
                event_type="stream_refused", status_code=close_code,
                error_code=exc.code
            )
            return
        if websocket.headers.get("X-MedZen-Contract-Version") != CONTRACT_VERSION:
            await websocket.close(code=4406)
            _log(
                event_type="stream_refused", status_code=4406,
                error_code="CONTRACT_VERSION_UNSUPPORTED"
            )
            return
        await websocket.accept()
        try:
            first = await _receive(websocket, configured_limits.idle_timeout_s)
            if first["type"] == "websocket.disconnect":
                return
            if first.get("text") is None:
                raise StreamRefusal(
                    "INVALID_EVENT", "first WebSocket frame must be start JSON",
                    4422, False,
                )
            try:
                start_value = json.loads(first["text"])
            except json.JSONDecodeError as exc:
                raise StreamRefusal(
                    "INVALID_EVENT", "start event is malformed", 4422, False
                ) from exc
            request_id, language_hint = _start(start_value)
            try:
                route = service.router.resolve(language_hint)
            except RegistryRefusal as exc:
                raise StreamRefusal(
                    "INVALID_EVENT", "language is unavailable", 4422, False
                ) from exc
            session = StreamingSession(
                request_id=request_id,
                session_id=str(session_id_factory()),
                route=route,
                vad=vad_factory(),
                partial_source=partial_source.clone(),
                limits=configured_limits,
                clock=clock,
            )
            await websocket.send_json(session.ready_event())
            while True:
                message = await _receive(websocket, configured_limits.idle_timeout_s)
                if message["type"] == "websocket.disconnect":
                    session.disconnect()
                    await guard.cancel(
                        request_id, "client_disconnect",
                        configured_limits.cancellation_budget_s,
                    )
                    _log(
                        event_type="stream_disconnected",
                        request_id=request_id,
                        session_id=session.session_id,
                        language=route.response_code,
                        model_versions=route.model_versions,
                        latency_ms=(clock() - started) * 1000,
                        status_code=1000,
                    )
                    return
                kind, reason = _control(message)
                if kind == "audio":
                    session.ingest(message["bytes"])
                    for event in session.buffer.drain_partials():
                        await websocket.send_json(event)
                    continue
                if kind in {"cancel", "barge_in"}:
                    success, latency = await _cancel_processing(
                        session=session,
                        guard=guard,
                        processing=None,
                        reason=reason or kind,
                    )
                    if not success:
                        raise StreamRefusal(
                            "DEPENDENCY_UNAVAILABLE",
                            "cancellation exceeded the 250 ms budget",
                            4503,
                            True,
                        )
                    await websocket.send_json(session.event(
                        "cancelled",
                        reason=kind,
                        cancellation_latency_ms=round(latency, 3),
                    ))
                    await websocket.close(code=4000)
                    _log(
                        event_type="stream_cancelled",
                        request_id=request_id,
                        session_id=session.session_id,
                        language=route.response_code,
                        model_versions=route.model_versions,
                        latency_ms=latency,
                        status_code=4000,
                    )
                    return
                pcm = session.finish_audio()
                processing = asyncio.create_task(guard.complete(
                    pcm_audio=pcm,
                    request_id=request_id,
                    language_hint=language_hint,
                    cancellation=session.cancellation,
                ))
                outcome, value = await _await_pipeline_or_control(
                    websocket=websocket,
                    session=session,
                    guard=guard,
                    processing=processing,
                )
                if outcome == "disconnected":
                    _log(
                        event_type="stream_disconnected",
                        request_id=request_id,
                        session_id=session.session_id,
                        language=route.response_code,
                        model_versions=route.model_versions,
                        latency_ms=(clock() - started) * 1000,
                        status_code=1000,
                    )
                    return
                if outcome == "cancelled":
                    success, latency, control = value
                    if not success:
                        raise StreamRefusal(
                            "DEPENDENCY_UNAVAILABLE",
                            "cancellation exceeded the 250 ms budget",
                            4503,
                            True,
                        )
                    await websocket.send_json(session.event(
                        "cancelled",
                        reason=control,
                        cancellation_latency_ms=round(latency, 3),
                    ))
                    await websocket.close(code=4000)
                    _log(
                        event_type="stream_cancelled",
                        request_id=request_id,
                        session_id=session.session_id,
                        language=route.response_code,
                        model_versions=route.model_versions,
                        latency_ms=latency,
                        status_code=4000,
                    )
                    return
                batch = session.final_batch(value)
                try:
                    await deliver_final_batch(websocket, store, request_id, batch)
                except (WebSocketDisconnect, FinalDeliveryInterrupted):
                    session.disconnect()
                    _log(
                        event_type="stream_disconnected_final_retained",
                        request_id=request_id,
                        session_id=session.session_id,
                        language=route.response_code,
                        model_versions=value["model_versions"],
                        latency_ms=(clock() - started) * 1000,
                        status_code=1000,
                    )
                    return
                await websocket.close(code=1000)
                _log(
                    event_type="stream_completed",
                    request_id=request_id,
                    session_id=session.session_id,
                    language=value["language"],
                    model_versions=value["model_versions"],
                    latency_ms=(clock() - started) * 1000,
                    status_code=1000,
                )
                return
        except WebSocketDisconnect:
            if session is not None:
                session.disconnect()
                await guard.cancel(
                    request_id, "client_disconnect",
                    configured_limits.cancellation_budget_s,
                )
            return
        except StreamRefusal as exc:
            if session is not None:
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.send_json(session.event(
                        "error",
                        error={
                            "code": exc.code,
                            "message": exc.message,
                            "retryable": exc.retryable,
                        },
                    ))
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close(
                    code=exc.close_code, reason=exc.reason_code
                )
            _log(
                event_type="stream_refused",
                request_id=request_id,
                session_id=session.session_id if session else None,
                language=session.route.response_code if session else "absent",
                model_versions=session.route.model_versions if session else None,
                latency_ms=(clock() - started) * 1000,
                status_code=exc.close_code,
                error_code=exc.code,
            )

    return app


app = create_app()
