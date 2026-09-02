from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .gateway import GatewayRefusal, LLMGateway
from .policy import PolicyStore
from .provider import BedrockProvider, FakeBedrockProvider
from .shared_resilience import CircuitBreaker, State, load_config


MAX_BODY_BYTES = 65_536
LOGGER = logging.getLogger("medzen.llm")
CONTRACT_SNAPSHOT = "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001"


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


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


def _safe_versions(value: Any) -> dict[str, str | None]:
    if isinstance(value, dict) and set(value) == {
        "asr", "registry_snapshot", "llm", "rag", "tts"
    }:
        return dict(value)
    return _fallback_versions()


def _breaker() -> CircuitBreaker:
    config = load_config()
    defaults = config["circuit_breakers"]["defaults"]
    bedrock = config["circuit_breakers"]["per_provider"]["bedrock"]
    return CircuitBreaker(
        name="bedrock",
        failure_threshold=bedrock["failure_threshold"],
        timeout_threshold=defaults["timeout_threshold"],
        window_s=defaults["window_s"],
        open_duration_s=bedrock["open_duration_s"],
        half_open_max_calls=defaults["half_open_max_calls"],
    )


def create_app(gateway: LLMGateway | None = None, *,
               max_body_bytes: int = MAX_BODY_BYTES) -> FastAPI:
    supplied_gateway = gateway

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            if supplied_gateway is not None:
                app.state.gateway = supplied_gateway
            else:
                mode = os.environ.get("MEDZEN_LLM_PROVIDER", "fake")
                if mode not in ("fake", "bedrock"):
                    raise RuntimeError(
                        f"unknown MEDZEN_LLM_PROVIDER {mode!r} — the gateway "
                        "refuses unrecognised provider modes"
                    )
                root = _root()
                policies = PolicyStore(
                    root / "registry/languages",
                    root / "registry/llm-policies/v1.yaml",
                )
                policies.validate_all()
                if mode == "bedrock":
                    provider = BedrockProvider(
                        model_id=os.environ.get("MEDZEN_BEDROCK_MODEL_ID", ""),
                        region=os.environ.get("AWS_REGION", "eu-central-1"),
                    )
                else:
                    provider = FakeBedrockProvider()
                app.state.gateway = LLMGateway(policies, provider, _breaker())
            app.state.startup_error = None
        except Exception as exc:
            app.state.gateway = None
            app.state.startup_error = type(exc).__name__
        yield

    app = FastAPI(
        title="MedZen B6.2 local LLM gateway",
        version="fake-bedrock-local-v1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

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
        return {"status": "alive", "service": "llm-gateway"}

    @app.get("/readyz")
    async def readyz(request: Request):
        gateway_value = request.app.state.gateway
        state = gateway_value.breaker.state if gateway_value is not None else None
        ready = gateway_value is not None and state is State.CLOSED
        provider_obj = getattr(gateway_value, "provider", None)
        provider_name = getattr(provider_obj, "name", "unavailable")
        payload = {
            "ready": ready,
            "provider": provider_name,
            "provider_network_access": provider_name == "bedrock",
            "model_version": getattr(provider_obj, "model_version", None),
            "language_policies_loaded": gateway_value is not None,
            "breaker_state": state.value if state is not None else "unavailable",
        }
        if not ready:
            payload["error_code"] = (
                request.app.state.startup_error or "PROVIDER_CIRCUIT_OPEN"
            )
        return JSONResponse(payload, status_code=200 if ready else 503)

    @app.post("/internal/v1/responses/stream")
    async def respond_stream(request: Request):
        # Phase 3b: same validation and checks as /internal/v1/responses;
        # the provider call streams and reply-text deltas are relayed as
        # NDJSON {"event":"delta","text":...}, then {"event":"final",...}
        # (the exact buffered response) or {"event":"error",...}.
        import asyncio
        from fastapi.responses import StreamingResponse
        gateway_value = request.app.state.gateway
        if gateway_value is None:
            return JSONResponse({"error": {"code": "PROVIDER_UNAVAILABLE",
                                           "message": "LLM gateway is not ready",
                                           "retryable": True}}, status_code=503)
        raw = await request.body()
        if len(raw) > max_body_bytes:
            return JSONResponse({"error": {"code": "PAYLOAD_TOO_LARGE",
                                           "message": "request body exceeds 65536 bytes",
                                           "retryable": False}}, status_code=413)
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"error": {"code": "INVALID_REQUEST",
                                           "message": "request JSON is malformed",
                                           "retryable": False}}, status_code=400)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()

        def on_delta(text: str) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait, json.dumps({"event": "delta", "text": text}) + "\n")

        async def run() -> None:
            try:
                response = await asyncio.to_thread(
                    gateway_value.complete_stream, value, on_delta)
                queue.put_nowait(json.dumps({"event": "final", **response}) + "\n")
            except GatewayRefusal as exc:
                queue.put_nowait(json.dumps({
                    "event": "error", "code": exc.code, "message": exc.message,
                    "status": exc.status_code, "retryable": exc.retryable}) + "\n")
            except Exception:
                queue.put_nowait(json.dumps({
                    "event": "error", "code": "PROVIDER_UNAVAILABLE",
                    "message": "LLM provider is unavailable",
                    "status": 503, "retryable": True}) + "\n")
            finally:
                queue.put_nowait(sentinel)

        async def lines():
            task = asyncio.create_task(run())
            try:
                while True:
                    item = await queue.get()
                    if item is sentinel:
                        break
                    yield item
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(lines(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store"})

    @app.post("/internal/v1/responses")
    async def respond(request: Request):
        gateway_value = request.app.state.gateway
        request_id = str(uuid.uuid4())
        versions = _fallback_versions()
        if gateway_value is None:
            refusal = GatewayRefusal(
                "PROVIDER_UNAVAILABLE", "LLM gateway is not ready", 503, True
            )
            value = None
        elif request.headers.get("content-type", "").split(";", 1)[0] != (
            "application/json"
        ):
            refusal = GatewayRefusal(
                "UNSUPPORTED_MEDIA_TYPE",
                "application/json is required",
                415,
                False,
            )
            value = None
        else:
            raw = await request.body()
            if len(raw) > max_body_bytes:
                refusal = GatewayRefusal(
                    "PAYLOAD_TOO_LARGE",
                    "request body exceeds 65536 bytes",
                    413,
                    False,
                )
                value = None
            else:
                try:
                    value = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    value = None
                    refusal = GatewayRefusal(
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
                        return gateway_value.complete(value)
                    except GatewayRefusal as exc:
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
