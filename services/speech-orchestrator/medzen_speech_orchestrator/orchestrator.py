from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Callable, Protocol

from .emergency import EmergencyChecker
from .local_dependencies import ASRResult
from .registry import (
    DEPLOYED_CLASSIFICATION,
    RegistryRefusal,
    RegistryRoute,
    RegistryRouter,
)


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


class TTSClient(Protocol):
    def synthesize(
        self,
        *,
        request_id: str,
        language: str,
        text: str,
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
        tts: TTSClient | None = None,
        clock: Callable[[], float] = time.perf_counter,
        session_id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ):
        self.router = router
        self.emergency = emergency
        self.asr = asr
        self.rag = rag
        self.llm = llm
        self.tts = tts
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

    def cancel(self, request_id: str) -> None:
        """Propagate cancellation to every adapter with active request state."""
        seen: set[int] = set()
        for dependency in (self.asr, self.rag, self.llm, self.tts):
            if dependency is None or id(dependency) in seen:
                continue
            seen.add(id(dependency))
            cancel = getattr(dependency, "cancel", None)
            if callable(cancel):
                cancel(request_id)

    def handle(
        self, *, audio: bytes, request_id: str, language_hint: str | None,
        response_audio: bool = False,
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
            if asr_versions != route.expected_asr_versions:
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
            reported_rag_versions = self._versions(rag.get("model_versions"), "RAG")
            index = rag.get("index")
            if route.classification == DEPLOYED_CLASSIFICATION:
                rag_identity_valid = reported_rag_versions == {
                    "asr": None,
                    "registry_snapshot": (
                        "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001"
                    ),
                    "llm": None,
                    "rag": f"sha256:{route.rag_snapshot_sha256}",
                    "tts": None,
                }
                rag_versions = {
                    **route.model_versions,
                    "rag": f"sha256:{route.rag_snapshot_sha256}",
                }
            else:
                rag_identity_valid = (
                    reported_rag_versions["asr"] == route.asr_model_version
                    and reported_rag_versions["registry_snapshot"]
                    == route.registry_snapshot
                    and reported_rag_versions["rag"]
                    == f"sha256:{route.rag_snapshot_sha256}"
                )
                rag_versions = reported_rag_versions
            if (
                rag.get("request_id") != request_id
                or not isinstance(index, dict)
                or index.get("alias") != route.rag_alias
                or index.get("snapshot_sha256") != route.rag_snapshot_sha256
                or not isinstance(rag.get("citations"), list)
                or not rag["citations"]
                or not rag_identity_valid
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
            # B6v2 round 3 (Codex): a REAL provider cites a SUBSET of the
            # supplied documents — exact-tuple equality only ever fit the
            # synthetic echo. Every cited entry must still be byte-equal
            # to a supplied one (no invented or altered citations), cited
            # at most once, and the binding hash covers what was actually
            # cited. The echo provider's full set remains a valid subset.
            reply_citations = (
                reply.get("citations") if isinstance(reply, dict) else None
            )
            citations_ok = (
                isinstance(reply_citations, list)
                and bool(reply_citations)
                and all(c in rag["citations"] for c in reply_citations)
                and len({self._citation_binding([c]) for c in reply_citations})
                == len(reply_citations)
            )
            if (
                llm.get("request_id") != request_id
                or llm.get("language") != route.alias
                or versions != expected
                or not isinstance(reply, dict)
                or not isinstance(reply.get("text"), str)
                or not reply["text"]
                or not citations_ok
                or reply.get("citation_binding_sha256")
                != self._citation_binding(reply_citations)
            ):
                raise OrchestratorRefusal(
                    "DEPENDENCY_UNAVAILABLE",
                    "LLM result does not match the request, citations and registry",
                    503,
                    True,
                )
            tts_ms = 0.0
            tts_reply = {
                "audio_url": None,
                "tts_backend": "text_only",
            }
            # B6v2 (Codex serving review): response_audio=false means
            # ZERO synthesis calls — text must never reach the TTS
            # provider against the caller's request. Default is False.
            if self.tts is not None and response_audio:
                tts, tts_ms = self._timed(
                    lambda: self.tts.synthesize(
                        request_id=request_id,
                        language=route.alias,
                        text=reply["text"],
                        versions=versions,
                        route=route,
                    )
                )
                tts_versions = self._versions(tts.get("model_versions"), "TTS")
                # B6v2 round 3 (Codex): the round-2 fill accepted ANY
                # value the TTS service claimed — an identity check that
                # cannot fail. The only version this step may introduce
                # is the one the REGISTRY bound to this route (fish:s1
                # or None); every other key must be preserved exactly.
                expected_tts = dict(versions)
                allowed = [expected_tts]
                if route.tts_model_version is not None:
                    allowed.append(dict(versions, tts=route.tts_model_version))
                tts_identity_ok = tts_versions in allowed
                if (
                    tts.get("request_id") != request_id
                    or tts.get("language") != route.alias
                    or tts.get("text") != reply["text"]
                    or tts.get("tts_backend") not in {
                        "fish", "self_hosted", "text_only"
                    }
                    or not isinstance(tts.get("content_sha256"), str)
                    or tts["content_sha256"]
                    != hashlib.sha256(reply["text"].encode("utf-8")).hexdigest()
                    or not tts_identity_ok
                    or (
                        tts.get("tts_backend") == "text_only"
                        and tts.get("audio_url") is not None
                    )
                ):
                    raise OrchestratorRefusal(
                        "DEPENDENCY_UNAVAILABLE",
                        "TTS result does not preserve the reply and registry identity",
                        503,
                        True,
                    )
                tts_reply = {
                    "audio_url": tts.get("audio_url"),
                    "tts_backend": tts["tts_backend"],
                }
                # adopt the (possibly route-filled) tts version — already
                # constrained to the registry-bound identity above
                versions = tts_versions
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
                    **tts_reply,
                },
                "model_versions": versions,
                "latency_ms": {
                    "total": total_ms, "asr": asr_ms,
                    "rag": rag_ms, "llm": llm_ms, "tts": tts_ms,
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
