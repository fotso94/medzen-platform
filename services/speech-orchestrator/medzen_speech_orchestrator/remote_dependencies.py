from __future__ import annotations

import http.client
import json
import threading
from typing import Any
from urllib.parse import urlsplit

from .local_dependencies import ASRResult
from .registry import RegistryRoute, canonical_json


MAX_RESPONSE_BYTES = 1_048_576
ALLOWED_HOSTS = {
    "asr-runtime.medzen.svc.cluster.local": 8081,
    "rag-index.medzen.svc.cluster.local": 8083,
    "llm-gateway.medzen.svc.cluster.local": 8082,
    "tts-gateway.medzen.svc.cluster.local": 8080,
}


class RemoteDependencyRefusal(RuntimeError):
    """A cluster dependency refused or violated its adopted contract."""


class ClusterHTTPTransport:
    """Bounded HTTP transport with request-scoped connection cancellation."""

    def __init__(self, *, timeout_seconds: float = 30.0):
        # Ceiling raised 30 -> 120 (owner order 2026-08-29): the dev TTS leg
        # legitimately needs ~40s while Fish s2.1-pro-free runs at peak
        # latency. The default stays 30.0; only an explicit deploy-time
        # override (see app.py) reaches higher.
        if not 0 < timeout_seconds <= 120:
            raise ValueError("dependency timeout must be between 0 and 120 seconds")
        self.timeout_seconds = timeout_seconds
        self._active: dict[str, set[http.client.HTTPConnection]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _target(endpoint: str) -> tuple[str, int, str]:
        parsed = urlsplit(endpoint)
        port = parsed.port
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname not in ALLOWED_HOSTS
            or port != ALLOWED_HOSTS.get(parsed.hostname)
            or not parsed.path.startswith("/internal/v1/")
            or parsed.query
            or parsed.fragment
        ):
            raise RemoteDependencyRefusal(
                "dependency endpoint is outside the reviewed cluster boundary"
            )
        return parsed.hostname, port, parsed.path

    def _register(self, request_id: str, connection: http.client.HTTPConnection) -> None:
        with self._lock:
            self._active.setdefault(request_id, set()).add(connection)

    def _release(self, request_id: str, connection: http.client.HTTPConnection) -> None:
        with self._lock:
            connections = self._active.get(request_id)
            if connections is None:
                return
            connections.discard(connection)
            if not connections:
                self._active.pop(request_id, None)

    def cancel(self, request_id: str) -> None:
        with self._lock:
            connections = tuple(self._active.pop(request_id, ()))
        for connection in connections:
            connection.close()

    def post(
        self,
        *,
        endpoint: str,
        request_id: str,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        host, port, path = self._target(endpoint)
        connection = http.client.HTTPConnection(
            host, port, timeout=self.timeout_seconds
        )
        self._register(request_id, connection)
        request_headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "X-Request-ID": request_id,
            **(headers or {}),
        }
        try:
            connection.request("POST", path, body=body, headers=request_headers)
            response = connection.getresponse()
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise RemoteDependencyRefusal(
                        "dependency response length is malformed"
                    ) from exc
                if declared_size < 0 or declared_size > MAX_RESPONSE_BYTES:
                    raise RemoteDependencyRefusal(
                        "dependency response exceeds the bounded size"
                    )
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RemoteDependencyRefusal(
                    "dependency response exceeds the bounded size"
                )
            media_type = (response.getheader("Content-Type") or "").split(";", 1)[0]
            if response.status != 200 or media_type != "application/json":
                raise RemoteDependencyRefusal(
                    "dependency returned a non-success contract response"
                )
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RemoteDependencyRefusal(
                    "dependency response must be a JSON object"
                )
            return value
        except RemoteDependencyRefusal:
            raise
        except Exception as exc:
            raise RemoteDependencyRefusal("cluster dependency is unavailable") from exc
        finally:
            self._release(request_id, connection)
            connection.close()

    def post_json(
        self, *, endpoint: str, request_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        return self.post(
            endpoint=endpoint,
            request_id=request_id,
            body=canonical_json(value),
            content_type="application/json",
        )


class RemoteASRClient:
    def __init__(self, transport: ClusterHTTPTransport):
        self.transport = transport

    def transcribe(
        self, audio: bytes, *, request_id: str, route: RegistryRoute
    ) -> ASRResult:
        value = self.transport.post(
            endpoint=route.endpoint("asr"),
            request_id=request_id,
            body=audio,
            content_type="audio/wav",
            headers={"X-MedZen-Language": route.response_code},
        )
        try:
            return ASRResult(
                request_id=value["request_id"],
                language=value["language"],
                language_probability=value["language_probability"],
                transcript=value["transcript"],
                duration_seconds=value["duration_seconds"],
                model_versions=value["model_versions"],
                artifact_tree_sha256=value.get("artifact_tree_sha256"),
            )
        except KeyError as exc:
            raise RemoteDependencyRefusal(
                "ASR response fields are incomplete"
            ) from exc

    def cancel(self, request_id: str) -> None:
        self.transport.cancel(request_id)


class RemoteRAGClient:
    def __init__(self, transport: ClusterHTTPTransport):
        self.transport = transport

    def retrieve(
        self, *, request_id: str, query: str, route: RegistryRoute
    ) -> dict[str, Any]:
        return self.transport.post_json(
            endpoint=route.endpoint("rag"),
            request_id=request_id,
            value={
                "request_id": request_id,
                "query": query,
                "language": route.rag_query_language,
                "top_k": 3,
            },
        )

    def cancel(self, request_id: str) -> None:
        self.transport.cancel(request_id)


class RemoteLLMClient:
    def __init__(self, transport: ClusterHTTPTransport):
        self.transport = transport

    def complete(
        self,
        *,
        request_id: str,
        transcript: dict[str, str],
        rag: dict[str, Any],
        versions: dict[str, str | None],
        route: RegistryRoute,
    ) -> dict[str, Any]:
        return self.transport.post_json(
            endpoint=route.endpoint("llm"),
            request_id=request_id,
            value={
                "request_id": request_id,
                "language": route.alias,
                "transcript": transcript,
                "rag": {
                    "query_id": rag["query_id"],
                    "index_snapshot_sha256": rag["index"]["snapshot_sha256"],
                    "citations": rag["citations"],
                },
                "model_versions": versions,
            },
        )

    def cancel(self, request_id: str) -> None:
        self.transport.cancel(request_id)


class RemoteTTSClient:
    def __init__(self, transport: ClusterHTTPTransport):
        self.transport = transport

    def synthesize(
        self,
        *,
        request_id: str,
        language: str,
        text: str,
        versions: dict[str, str | None],
        route: RegistryRoute,
    ) -> dict[str, Any]:
        return self.transport.post_json(
            endpoint=route.endpoint("tts"),
            request_id=request_id,
            value={
                "request_id": request_id,
                "language": language,
                "text": text,
                "model_versions": versions,
            },
        )

    def cancel(self, request_id: str) -> None:
        self.transport.cancel(request_id)
