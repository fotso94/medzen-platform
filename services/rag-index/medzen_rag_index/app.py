from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .bedrock_backend import BedrockRepository
from .index import IndexRefusal, IndexRepository


MAX_BODY_BYTES = 16_384
MAX_QUERY_CHARACTERS = 2_000
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
LOGGER = logging.getLogger("medzen.rag")
CONTRACT_SNAPSHOT = "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001"


def _fallback_versions() -> dict[str, str | None]:
    return {
        "asr": None,
        "registry_snapshot": CONTRACT_SNAPSHOT,
        "llm": None,
        "rag": None,
        "tts": None,
    }


def _valid_request_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("request_id must be a UUID") from exc


def _error(*, request_id: str, versions: dict[str, str | None], code: str,
           message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "request_id": request_id,
            "model_versions": versions,
            "error": {"code": code, "message": message, "retryable": False},
        },
        status_code=status_code,
    )


def _default_index_root() -> Path:
    return Path(__file__).resolve().parents[3] / "platform/testdata/rag-index"


def _build_repository(index_root: Path, alias: str):
    """Backend selection (2026-09-02): ``RAG_BACKEND=bedrock`` serves the real
    dev corpus through a Bedrock Knowledge Base; the default local backend
    keeps serving the synthetic file index unchanged."""
    backend = os.environ.get("RAG_BACKEND", "local")
    if backend == "bedrock":
        corpora = {
            item.strip() for item in os.environ.get("RAG_BEDROCK_CORPORA", "").split(",")
            if item.strip()
        }
        def floors(name: str) -> dict[str, float]:
            out = {}
            for item in os.environ.get(name, "").split(","):
                if item.strip():
                    key, _, floor = item.partition("=")
                    out[key.strip()] = float(floor)
            return out
        return BedrockRepository(
            index_root, alias,
            min_score=float(os.environ.get("RAG_BEDROCK_MIN_SCORE", "0.45")),
            min_score_by_language=floors("RAG_BEDROCK_MIN_SCORE_BY_LANGUAGE"),
            min_score_by_corpus=floors("RAG_BEDROCK_MIN_SCORE_BY_CORPUS"),
            candidates=int(os.environ.get("RAG_BEDROCK_CANDIDATES", "8")),
            corpora=corpora or None,
            timeout_seconds=float(os.environ.get("RAG_BEDROCK_TIMEOUT_SECONDS", "6")),
        )
    if backend != "local":
        raise IndexRefusal("unknown RAG backend")
    return IndexRepository(index_root, alias)


def create_app(repository: IndexRepository | BedrockRepository | None = None, *,
               index_root: Path | None = None,
               max_body_bytes: int = MAX_BODY_BYTES) -> FastAPI:
    supplied_repository = repository

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.repository = supplied_repository or _build_repository(
                index_root or Path(os.environ.get(
                    "RAG_INDEX_ROOT", str(_default_index_root())
                )),
                os.environ.get("RAG_INDEX_ALIAS", "current"),
            )
            app.state.startup_error = None
        except Exception as exc:
            app.state.repository = None
            app.state.startup_error = type(exc).__name__
        yield

    app = FastAPI(
        title="MedZen B6.1 RAG index",
        version="synthetic-v1",
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
        return {"status": "alive", "service": "rag-index"}

    @app.get("/readyz")
    async def readyz(request: Request):
        repo = request.app.state.repository
        ready = repo is not None
        payload: dict[str, Any] = {
            "ready": ready,
            "classification": (
                repo.loaded.classification if ready else "SYNTHETIC_NON_CLINICAL"
            ),
            "index_loaded": ready,
        }
        if ready:
            payload["index"] = {
                "alias": repo.loaded.alias,
                "version": repo.loaded.version,
                "snapshot_sha256": repo.loaded.snapshot_sha256,
            }
        else:
            payload["error_code"] = (
                request.app.state.startup_error or "INDEX_NOT_READY"
            )
        return JSONResponse(payload, status_code=200 if ready else 503)

    @app.post("/internal/v1/retrievals")
    async def retrieve(request: Request):
        started = time.perf_counter()
        repo = request.app.state.repository
        versions = repo.loaded.model_versions if repo is not None else _fallback_versions()
        request_id = str(uuid.uuid4())
        if repo is None:
            return _error(
                request_id=request_id,
                versions=versions,
                code="DEPENDENCY_UNAVAILABLE",
                message="RAG index is not ready",
                status_code=503,
            )
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return _error(
                request_id=request_id,
                versions=versions,
                code="INVALID_REQUEST",
                message="application/json is required",
                status_code=415,
            )
        raw = await request.body()
        if len(raw) > max_body_bytes:
            return _error(
                request_id=request_id,
                versions=versions,
                code="PAYLOAD_TOO_LARGE",
                message="request body exceeds 16384 bytes",
                status_code=413,
            )
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("request body must be an object")
            unknown = set(value).difference({"request_id", "query", "language", "top_k"})
            if unknown:
                raise ValueError("request contains unknown fields")
            request_id = _valid_request_id(value.get("request_id"))
            query = value.get("query")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query must be a non-empty string")
            if len(query) > MAX_QUERY_CHARACTERS:
                raise ValueError("query exceeds 2000 characters")
            language = value.get("language")
            if language is not None and (
                not isinstance(language, str)
                or LANGUAGE_RE.fullmatch(language) is None
            ):
                raise ValueError("language must be a 2-3 letter lowercase code")
            top_k = value.get("top_k", 3)
            if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 5:
                raise ValueError("top_k must be an integer from 1 to 5")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            return _error(
                request_id=request_id,
                versions=versions,
                code="INVALID_REQUEST",
                message=str(exc),
                status_code=400,
            )
        normalized_query = " ".join(query.casefold().split())
        query_id = hashlib.sha256(
            (repo.loaded.snapshot_sha256 + "\n" + (language or "*") + "\n"
             + normalized_query).encode("utf-8")
        ).hexdigest()
        citations = await asyncio.to_thread(
            repo.search, query, language=language, top_k=top_k)
        return {
            "request_id": request_id,
            "query_id": query_id,
            "index": {
                "alias": repo.loaded.alias,
                "version": repo.loaded.version,
                "snapshot_sha256": repo.loaded.snapshot_sha256,
                "classification": repo.loaded.classification,
            },
            "citations": citations,
            "model_versions": versions,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    return app


app = create_app()
