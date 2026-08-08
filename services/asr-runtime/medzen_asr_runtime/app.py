from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .backend import BackendRefusal, FasterWhisperBackend, Transcript


MAX_AUDIO_BYTES = 25 * 1024 * 1024
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
LOGGER = logging.getLogger("medzen.asr")


class Backend(Protocol):
    ready: bool
    model_versions: dict[str, str | None]

    def transcribe(self, audio_path: Path, language_hint: str | None) -> Transcript:
        ...


def _request_id(value: str | None) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("X-Request-ID must be a UUID") from exc
    return str(parsed)


def _language(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    value = value.lower()
    if LANGUAGE_RE.fullmatch(value) is None:
        raise ValueError("language hint must be a 2-3 letter code")
    return value


def _transcript_payload(request_id: str, transcript: Transcript,
                        model_versions: dict[str, str | None],
                        latency_ms: float) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "language": transcript.language,
        "language_probability": transcript.language_probability,
        "transcript": {
            "verbatim": transcript.verbatim,
            "normalized": transcript.normalized,
            "normalization_version": transcript.normalization_version,
        },
        "duration_seconds": transcript.duration_seconds,
        "model_versions": model_versions,
        "latency_ms": round(latency_ms, 3),
        "classification": "PLATFORM_PROOF_ONLY",
        "production_approved": False,
    }


async def _transcribe_bytes(backend: Backend, audio: bytes,
                            language_hint: str | None) -> Transcript:
    with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
        handle.write(audio)
        handle.flush()
        return await asyncio.to_thread(
            backend.transcribe, Path(handle.name), language_hint)


def create_app(backend: Backend | None = None, *,
               max_audio_bytes: int = MAX_AUDIO_BYTES) -> FastAPI:
    supplied_backend = backend

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if supplied_backend is None:
            try:
                app.state.backend = FasterWhisperBackend(
                    Path(os.environ.get("MODEL_DIR", "/models")),
                    os.environ.get("MODEL_MANIFEST_SHA256", ""))
                app.state.startup_error = None
            except Exception as exc:
                app.state.backend = None
                app.state.startup_error = type(exc).__name__
        else:
            app.state.backend = supplied_backend
            app.state.startup_error = None
        yield

    app = FastAPI(title="MedZen B6A ASR runtime", version="v0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def safe_access_log(request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("X-Request-ID", "absent")
        response = await call_next(request)
        LOGGER.info(json.dumps({
            "event": "http_request",
            "request_id": request_id,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }, sort_keys=True))
        return response

    @app.get("/healthz")
    async def healthz():
        return {"status": "alive", "service": "asr-runtime", "version": "v0"}

    @app.get("/readyz")
    async def readyz(request: Request):
        runtime = request.app.state.backend
        ready = runtime is not None and runtime.ready is True
        payload = {
            "ready": ready,
            "classification": "PLATFORM_PROOF_ONLY",
            "model_manifest_verified": ready,
            "model_tree_verified": ready,
            "model_loaded": ready,
            "smoke_inference_passed": ready,
            "platform_test_disclosure_loaded": ready,
        }
        if not ready:
            payload["error_code"] = request.app.state.startup_error or "MODEL_NOT_READY"
        return JSONResponse(payload, status_code=200 if ready else 503)

    @app.post("/internal/v1/transcriptions")
    async def transcribe(request: Request):
        runtime = request.app.state.backend
        if runtime is None or runtime.ready is not True:
            return JSONResponse({"error": {"code": "MODEL_NOT_READY",
                                           "message": "ASR model is not ready",
                                           "retryable": True}}, status_code=503)
        try:
            request_id = _request_id(request.headers.get("X-Request-ID"))
            language_hint = _language(request.headers.get("X-MedZen-Language"))
        except ValueError as exc:
            return JSONResponse({"error": {"code": "INVALID_REQUEST",
                                           "message": str(exc),
                                           "retryable": False}}, status_code=400)
        if request.headers.get("content-type", "").split(";", 1)[0] != "audio/wav":
            return JSONResponse({"error": {"code": "UNSUPPORTED_AUDIO_TYPE",
                                           "message": "audio/wav is required",
                                           "retryable": False}}, status_code=415)
        audio = await request.body()
        if not audio:
            return JSONResponse({"error": {"code": "EMPTY_AUDIO",
                                           "message": "audio body is empty",
                                           "retryable": False}}, status_code=400)
        if len(audio) > max_audio_bytes:
            return JSONResponse({"error": {"code": "AUDIO_TOO_LARGE",
                                           "message": "audio exceeds the B6A limit",
                                           "retryable": False}}, status_code=413)
        started = time.perf_counter()
        try:
            result = await _transcribe_bytes(runtime, audio, language_hint)
        except Exception:
            return JSONResponse({"error": {"code": "ASR_INFERENCE_FAILED",
                                           "message": "ASR inference failed",
                                           "retryable": True},
                                 "request_id": request_id,
                                 "model_versions": runtime.model_versions},
                                status_code=503)
        return _transcript_payload(
            request_id, result, runtime.model_versions,
            (time.perf_counter() - started) * 1000)

    @app.websocket("/internal/v1/transcriptions/stream")
    async def stream(websocket: WebSocket):
        await websocket.accept()
        runtime = websocket.app.state.backend
        if runtime is None or runtime.ready is not True:
            await websocket.send_json({
                "type": "error", "request_id": "unknown", "session_id": "unknown",
                "error": {"code": "MODEL_NOT_READY", "message": "ASR model is not ready",
                          "retryable": True}, "model_versions": {}})
            await websocket.close(code=1013)
            return
        try:
            first = await websocket.receive_text()
            start = json.loads(first)
            if start.get("type") != "start":
                raise ValueError("first event must be start")
            request_id = _request_id(start.get("request_id"))
            language_hint = _language(start.get("language_hint"))
        except (ValueError, json.JSONDecodeError):
            await websocket.send_json({
                "type": "error", "request_id": "unknown", "session_id": "unknown",
                "error": {"code": "INVALID_START", "message": "invalid stream start",
                          "retryable": False}, "model_versions": runtime.model_versions})
            await websocket.close(code=1008)
            return
        session_id = str(uuid.uuid4())

        def event(kind: str, **fields: Any) -> dict[str, Any]:
            return {"type": kind, "request_id": request_id,
                    "session_id": session_id,
                    "model_versions": runtime.model_versions, **fields}

        await websocket.send_json(event("ready"))
        audio = bytearray()
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    audio.extend(message["bytes"])
                    if len(audio) > max_audio_bytes:
                        await websocket.send_json(event("error", error={
                            "code": "AUDIO_TOO_LARGE",
                            "message": "audio exceeds the B6A limit",
                            "retryable": False}))
                        await websocket.close(code=1009)
                        return
                    continue
                try:
                    control = json.loads(message.get("text") or "{}")
                except json.JSONDecodeError:
                    control = {}
                kind = control.get("type")
                if kind in {"cancel", "barge_in"}:
                    await websocket.send_json(event("cancelled"))
                    await websocket.close(code=1000)
                    return
                if kind != "end_of_speech":
                    await websocket.send_json(event("error", error={
                        "code": "INVALID_EVENT", "message": "invalid stream event",
                        "retryable": False}))
                    await websocket.close(code=1008)
                    return
                if not audio:
                    await websocket.send_json(event("error", error={
                        "code": "EMPTY_AUDIO", "message": "audio stream is empty",
                        "retryable": False}))
                    await websocket.close(code=1008)
                    return
                started = time.perf_counter()
                result = await _transcribe_bytes(runtime, bytes(audio), language_hint)
                payload = _transcript_payload(
                    request_id, result, runtime.model_versions,
                    (time.perf_counter() - started) * 1000)
                await websocket.send_json(event(
                    "final_transcript", transcript=payload["transcript"],
                    language=payload["language"],
                    language_probability=payload["language_probability"]))
                await websocket.send_json(event(
                    "completed", latency_ms=payload["latency_ms"]))
                await websocket.close(code=1000)
                return
        except WebSocketDisconnect:
            return
        except Exception:
            await websocket.send_json(event("error", error={
                "code": "ASR_INFERENCE_FAILED", "message": "ASR inference failed",
                "retryable": True}))
            await websocket.close(code=1011)

    return app


app = create_app()
