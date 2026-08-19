from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .gateway import TTSGateway, TTSRefusal
from .shared_resilience import CircuitBreaker, State, load_config


MAX_BODY_BYTES = 65_536
LOGGER = logging.getLogger("medzen.speech_tts")
CONTRACT_SNAPSHOT = "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001"


def fish_breaker() -> CircuitBreaker:
    config = load_config()
    defaults = config["circuit_breakers"]["defaults"]
    fish = config["circuit_breakers"]["per_provider"]["fish"]
    return CircuitBreaker(
        name="fish",
        failure_threshold=fish["failure_threshold"],
        timeout_threshold=defaults["timeout_threshold"],
        window_s=defaults["window_s"],
        open_duration_s=fish["open_duration_s"],
        half_open_max_calls=defaults["half_open_max_calls"],
    )


def _fallback_versions() -> dict[str, str | None]:
    return {
        "asr": None,
        "registry_snapshot": CONTRACT_SNAPSHOT,
        "llm": None,
        "rag": None,
        "tts": None,
    }


def _safe_request_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid4())


def _log_request_id(value: str | None) -> str:
    try:
        return str(uuid.UUID(value or ""))
    except (ValueError, TypeError, AttributeError):
        return "absent"


def _safe_versions(value: Any) -> dict[str, str | None]:
    if isinstance(value, dict) and set(value) == {
        "asr", "registry_snapshot", "llm", "rag", "tts"
    }:
        return dict(value)
    return _fallback_versions()


def create_app(
    gateway: TTSGateway | None = None, *, max_body_bytes: int = MAX_BODY_BYTES
) -> FastAPI:
    supplied_gateway = gateway

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            if supplied_gateway is not None:
                app.state.gateway = supplied_gateway
            else:
                mode = os.environ.get("MEDZEN_SPEECH_TTS_PROVIDER", "text_only")
                if mode not in ("text_only", "fish"):
                    raise RuntimeError(
                        f"unknown MEDZEN_SPEECH_TTS_PROVIDER {mode!r} — the "
                        "gateway refuses unrecognised provider modes"
                    )
                if mode == "fish":
                    from .provider import RealFishProvider
                    from .voices import resolve as _resolve_voice
                    app.state.gateway = TTSGateway(
                        provider=RealFishProvider(),
                        breaker=fish_breaker(),
                        voice_resolver=lambda language:
                            _resolve_voice(language).reference_id,
                    )
                else:
                    app.state.gateway = TTSGateway(provider=None, breaker=None)
            app.state.startup_error = None
        except Exception as exc:
            app.state.gateway = None
            app.state.startup_error = type(exc).__name__
        yield

    app = FastAPI(
        title="MedZen B6.5 local speech TTS gateway",
        version="medzen-tts-local-v1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def safe_access_log(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        LOGGER.info(json.dumps({
            "event": "http_request",
            "request_id": _log_request_id(request.headers.get("X-Request-ID")),
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }, sort_keys=True))
        return response

    @app.get("/healthz")
    async def healthz():
        return {"status": "alive", "service": "speech-tts-gateway"}

    @app.get("/readyz")
    async def readyz(request: Request):
        gateway_value = request.app.state.gateway
        ready = gateway_value is not None
        breaker = gateway_value.breaker if gateway_value is not None else None
        state = breaker.state if breaker is not None else None
        payload = {
            "ready": ready,
            "backend_mode": (
                gateway_value.backend_mode if gateway_value is not None else "unavailable"
            ),
            "text_only_available": ready,
            "fish_breaker_state": state.value if state is not None else "not_applicable",
            "fish_available": ready and breaker is not None and state is State.CLOSED,
            "provider_network_access": False,
        }
        if not ready:
            payload["error_code"] = request.app.state.startup_error
        return JSONResponse(payload, status_code=200 if ready else 503)

    @app.post("/internal/v1/syntheses")
    async def synthesize(request: Request):
        gateway_value = request.app.state.gateway
        request_id = str(uuid.uuid4())
        versions = _fallback_versions()
        if gateway_value is None:
            refusal = TTSRefusal(
                "SERVICE_UNAVAILABLE", "TTS gateway is not ready", 503, True
            )
        elif request.headers.get("content-type", "").split(";", 1)[0] != (
            "application/json"
        ):
            refusal = TTSRefusal(
                "UNSUPPORTED_MEDIA_TYPE", "application/json is required", 415, False
            )
        else:
            raw = await request.body()
            if len(raw) > max_body_bytes:
                refusal = TTSRefusal(
                    "PAYLOAD_TOO_LARGE",
                    "request body exceeds 65536 bytes",
                    413,
                    False,
                )
            else:
                try:
                    value = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    refusal = TTSRefusal(
                        "INVALID_REQUEST", "request JSON is malformed", 400, False
                    )
                else:
                    request_id = _safe_request_id(
                        value.get("request_id") if isinstance(value, dict) else None
                    )
                    versions = _safe_versions(
                        value.get("model_versions") if isinstance(value, dict) else None
                    )
                    try:
                        return gateway_value.synthesize(value)
                    except TTSRefusal as exc:
                        refusal = exc
        return JSONResponse(
            {
                "request_id": request_id,
                "model_versions": versions,
                "error": {
                    "code": refusal.code,
                    "message": refusal.message,
                    "retryable": refusal.retryable,
                },
            },
            status_code=refusal.status_code,
        )

    return app


app = create_app()
