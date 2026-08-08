from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Callable, Protocol

from .emergency import EmergencyChecker
from .local_dependencies import ASRResult
from .registry import RegistryRefusal, RegistryRoute, RegistryRouter


MODEL_VERSION_KEYS = {"asr", "registry_snapshot", "llm", "rag", "tts"}
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
TRANSCRIPT_KEYS = {"verbatim", "normalized", "normalization_version"}


class OrchestratorRefusal(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class ASRClient(Protocol):
    def transcribe(
        self, audio: bytes, *, request_id: str, route: RegistryRoute
    ) -> ASRResult: ...


class RAGClient(Protocol):
    def retrieve(
        self, *, request_id: str, query: str, route: RegistryRoute
    ) -> dict[str, Any]: ...


class LLMClient(Protocol):
    def complete(
        self,
        *,
        request_id: str,
        transcript: dict[str, str],
        rag: dict[str, Any],
        versions: dict[str, str | None],
        route: RegistryRoute,
    ) -> dict[str, Any]: ...


class SpeechOrchestrator:
    def __init__(
        self,
        *,
        router: RegistryRouter,
        emergency: EmergencyChecker,
        asr: ASRClient,
        rag: RAGClient,
        llm: LLMClient,
        clock: Callable[[], float] = time.perf_counter,
        session_id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ):
        self.router = router
        self.emergency = emergency
        self.asr = asr
        self.rag = rag
        self.llm = llm
        self.clock = clock
        self.session_id_factory = session_id_factory

    @property
    def registry_snapshot(self) -> str:
        return self.router.registry_snapshot

    def _timed(self, operation: Callable[[], Any]) -> tuple[Any, float]:
        started = self.clock()
        result = operation()
        return result, round((self.clock() - started) * 1000, 3)

    @staticmethod
    def _versions(value: Any, label: str) -> dict[str, str | None]:
        if not isinstance(value, dict) or set(value) != MODEL_VERSION_KEYS:
            raise OrchestratorRefusal(
                "DEPENDENCY_UNAVAILABLE",
                f"{label} did not return complete model versions",
                503,
                True,
            )
        if not isinstance(value["registry_snapshot"], str) or not value["registry_snapshot"]:
            raise OrchestratorRefusal(
                "DEPENDENCY_UNAVAILABLE",
                f"{label} omitted registry identity",
                503,
                True,
            )
        return dict(value)

    @staticmethod
    def _citation_binding(citations: list[dict[str, Any]]) -> str:
        raw = json.dumps(
            citations, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def handle(
        self, *, audio: bytes, request_id: str, language_hint: str | None
    ) -> tuple[str, dict[str, Any]]:
        total_started = self.clock()
        try:
            initial_route = self.router.resolve(language_hint)
        except RegistryRefusal as exc:
            raise OrchestratorRefusal(
                "INVALID_REQUEST", "language is unavailable", 422, False
            ) from exc
        try:
            asr, asr_ms = self._timed(
                lambda: self.asr.transcribe(
                    audio, request_id=request_id, route=initial_route
                )
            )
            if (
                asr.request_id != request_id
                or not isinstance(asr.language, str)
                or LANGUAGE_RE.fullmatch(asr.language) is None
                or not isinstance(asr.transcript, dict)
                or set(asr.transcript) != TRANSCRIPT_KEYS
                or not all(
                    isinstance(asr.transcript[field], str) and asr.transcript[field]
                    for field in TRANSCRIPT_KEYS
                )
            ):
                raise OrchestratorRefusal(
                    "DEPENDENCY_UNAVAILABLE",
                    "ASR result does not match the request contract",
                    503,
                    True,
                )
            route = self.router.resolve(asr.language)
            asr_versions = self._versions(asr.model_versions, "ASR")
            if asr_versions != route.model_versions:
                raise OrchestratorRefusal(
                    "DEPENDENCY_UNAVAILABLE",
                    "ASR result does not match the registry route",
                    503,
                    True,
                )
            emergency = self.emergency.check(asr.transcript["normalized"])
            session_id = str(self.session_id_factory())
            if emergency.triggered:
                versions = dict(route.model_versions)
                total_ms = round((self.clock() - total_started) * 1000, 3)
                return session_id, {
                    "request_id": request_id,
                    "session_id": session_id,
                    "language": route.response_code,
                    "transcript": dict(asr.transcript),
                    "reply": {
                        "text": emergency.response_text,
                        "citations": [],
                        "citation_binding_sha256": None,
                        "audio_url": None,
                        "tts_backend": "text_only",
                    },
                    "model_versions": versions,
                    "latency_ms": {
                        "total": total_ms, "asr": asr_ms,
                        "rag": 0.0, "llm": 0.0, "tts": 0.0,
                    },
                }
            rag, rag_ms = self._timed(
                lambda: self.rag.retrieve(
                    request_id=request_id,
                    query=asr.transcript["normalized"],
                    route=route,
                )
            )
            rag_versions = self._versions(rag.get("model_versions"), "RAG")
            index = rag.get("index")
            if (
                rag.get("request_id") != request_id
                or not isinstance(index, dict)
                or index.get("alias") != route.rag_alias
                or index.get("snapshot_sha256") != route.rag_snapshot_sha256
                or not isinstance(rag.get("citations"), list)
                or not rag["citations"]
                or rag_versions["asr"] != route.asr_model_version
                or rag_versions["registry_snapshot"] != route.registry_snapshot
                or rag_versions["rag"] != f"sha256:{route.rag_snapshot_sha256}"
            ):
                raise OrchestratorRefusal(
                    "DEPENDENCY_UNAVAILABLE",
                    "RAG result does not match the request and registry",
                    503,
                    True,
                )
            llm, llm_ms = self._timed(
                lambda: self.llm.complete(
                    request_id=request_id,
                    transcript=asr.transcript,
                    rag=rag,
                    versions=rag_versions,
                    route=route,
                )
            )
            versions = self._versions(llm.get("model_versions"), "LLM")
            expected = {
                "asr": route.asr_model_version,
                "registry_snapshot": route.registry_snapshot,
                "llm": route.llm_model_version,
                "rag": f"sha256:{route.rag_snapshot_sha256}",
                "tts": None,
            }
            reply = llm.get("reply")
            if (
                llm.get("request_id") != request_id
                or llm.get("language") != route.alias
                or versions != expected
                or not isinstance(reply, dict)
                or not isinstance(reply.get("text"), str)
                or not reply["text"]
                or reply.get("citations") != rag["citations"]
                or reply.get("citation_binding_sha256")
                != self._citation_binding(rag["citations"])
            ):
                raise OrchestratorRefusal(
                    "DEPENDENCY_UNAVAILABLE",
                    "LLM result does not match the request, citations and registry",
                    503,
                    True,
                )
            total_ms = round((self.clock() - total_started) * 1000, 3)
            return session_id, {
                "request_id": request_id,
                "session_id": session_id,
                "language": route.response_code,
                "transcript": dict(asr.transcript),
                "reply": {
                    "text": reply["text"],
                    "citations": reply["citations"],
                    "citation_binding_sha256": reply["citation_binding_sha256"],
                    "audio_url": None,
                    "tts_backend": "text_only",
                },
                "model_versions": versions,
                "latency_ms": {
                    "total": total_ms, "asr": asr_ms,
                    "rag": rag_ms, "llm": llm_ms, "tts": 0.0,
                },
            }
        except OrchestratorRefusal:
            raise
        except Exception as exc:
            raise OrchestratorRefusal(
                "DEPENDENCY_UNAVAILABLE",
                "a local dependency refused the request",
                503,
                True,
            ) from exc
