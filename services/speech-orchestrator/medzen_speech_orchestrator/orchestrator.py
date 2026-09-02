from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from typing import Any, Callable, Protocol

from .emergency import EmergencyChecker
from .local_dependencies import ASRResult
from .registry import (
    DEPLOYED_CLASSIFICATION,
    V2_CLASSIFICATION,
    RegistryRefusal,
    RegistryRoute,
    RegistryRouter,
)


MODEL_VERSION_KEYS = {"asr", "registry_snapshot", "llm", "rag", "tts"}
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")
TRANSCRIPT_KEYS = {"verbatim", "normalized", "normalization_version"}


# Dev-only fallback (owner order 2026-08-29): with this env set, a RAG
# result with ZERO citations is passed through to the LLM as a general-
# knowledge request (the llm-gateway runs the same flag) instead of
# refusing the whole request. Default OFF - grounded behavior unchanged.
ALLOW_UNGROUNDED = os.environ.get("MEDZEN_ALLOW_UNGROUNDED_FALLBACK") == "1"


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
        history: list[dict[str, str]] | None = None,
        on_delta: Callable[[str], None] | None = None,
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
    def _retrieval_query(current: str, history: list[dict[str, str]]) -> str:
        previous_user = [t["text"] for t in history if t.get("role") == "user"]
        if not previous_user:
            return current
        return f"{previous_user[-1]} {current}"

    @staticmethod
    def _citation_binding(citations: list[dict[str, Any]]) -> str:
        raw = json.dumps(
            citations, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _grounding_hash(citations: list[dict[str, Any]]) -> str:
        """The contract-defined grounding block hash (llm-v2.yaml): per
        supplied citation, the first non-blank of grounding_text/content/
        excerpt, stripped then capped at 1200 chars, rendered as
        "[<id>]\\n<grounding>" and joined by blank lines — byte-for-byte
        what the provider sends to the model. B6v2 round 4 (Codex): the
        hash existed but VANISHED here, so no client could ever tie an
        answer to the grounding actually used."""
        def grounding(item: dict[str, Any]) -> str | None:
            for field in ("grounding_text", "content", "excerpt"):
                value = str(item.get(field) or "").strip()
                if value:
                    return value[:1200]
            return None

        block = "\n\n".join(
            f"[{item.get('document_id')}]\n{grounding(item)}"
            for item in citations)
        return hashlib.sha256(block.encode("utf-8")).hexdigest()

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
        history: list[dict[str, str]] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        # Phase 2: client-carried memory. Validated at the edge (app.py);
        # here it only widens the retrieval query and rides to the LLM.
        history = list(history or [])
        # Phase 3 (2026-09-02): ONE pipeline, optionally narrated. Each stage
        # event is emitted only AFTER that stage passed every existing check,
        # so a streamed client never sees anything the buffered client would
        # not have received. The final result is byte-identical either way.
        def emit(name: str, payload: dict[str, Any]) -> None:
            if on_event is not None:
                on_event(name, payload)
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
            # B6v2 round 7 (Codex, FULL_TREE_MISMATCH): the 12-char
            # version prefix is a display label, not an identity — two
            # different artifacts sharing a prefix passed. In v2 the ASR
            # runtime reports the FULL 64-char tree digest and it must
            # equal the registry's binding EXACTLY.
            if route.classification == V2_CLASSIFICATION and (
                getattr(asr, "artifact_tree_sha256", None)
                != route.asr_artifact_tree_sha256
            ):
                raise OrchestratorRefusal(
                    "DEPENDENCY_UNAVAILABLE",
                    "ASR artifact tree digest does not match the registry "
                    "binding exactly",
                    503,
                    True,
                )
            emit("transcript_final", {
                "request_id": request_id,
                "language": route.response_code,
                "transcript": dict(asr.transcript),
                "model_versions": {"asr": route.asr_model_version},
                "latency_ms": {"asr": asr_ms},
            })
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
                    # Phase 2: a follow-up ("what about X?") retrieves badly on
                    # its own; widen with the PREVIOUS user question only —
                    # never assistant text, which would self-reinforce.
                    query=self._retrieval_query(asr.transcript["normalized"], history),
                    route=route,
                )
            )
            reported_rag_versions = self._versions(rag.get("model_versions"), "RAG")
            index = rag.get("index")
            # B6v2 round 4 (Codex HTTP-level finding): over the cluster
            # boundary the rag-index service reports its OWN identity
            # (asr None + its local contract snapshot) — it cannot echo
            # the orchestrator's route. The v2 flow shares the deployed
            # rag contract; only the local file-mode adapter echoes.
            if route.classification in (
                DEPLOYED_CLASSIFICATION, V2_CLASSIFICATION,
            ):
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
                or (not rag["citations"] and not ALLOW_UNGROUNDED)
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
                    # absent == empty: existing clients/fakes need no change
                    **({"history": history} if history else {}),
                    # Phase 3b: narrate reply-text deltas only to a streaming
                    # client; the validated final reply still gates everything
                    **({"on_delta": (lambda t: emit("reply_delta", {
                        "request_id": request_id, "text": t}))}
                       if on_event is not None else {}),
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
                and (bool(reply_citations)
                     or (ALLOW_UNGROUNDED and not rag["citations"]))
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
            # B6v2 round 4 (Codex): when the gateway reports the grounding
            # hash, it must equal the hash of the grounding block derivable
            # from the citations WE supplied — otherwise the model was
            # grounded on something this pipeline never saw.
            grounding_sha256 = llm.get("grounding_sha256")
            if grounding_sha256 is not None and (
                grounding_sha256 != self._grounding_hash(rag["citations"])
            ):
                raise OrchestratorRefusal(
                    "DEPENDENCY_UNAVAILABLE",
                    "LLM grounding hash does not match the supplied citations",
                    503,
                    True,
                )
            emit("reply_final", {
                "request_id": request_id,
                "text": reply["text"],
                "citations": len(reply["citations"]),
                "grounded": bool(reply["citations"]),
                "model_versions": dict(versions),
                "latency_ms": {"rag": rag_ms, "llm": llm_ms},
            })
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
                emit("audio_ready", {
                    "request_id": request_id,
                    "audio_url": tts.get("audio_url"),
                    "tts_backend": tts["tts_backend"],
                    "model_versions": dict(versions),
                    "latency_ms": {"tts": tts_ms},
                })
            total_ms = round((self.clock() - total_started) * 1000, 3)
            reply_out = {
                "text": reply["text"],
                "citations": reply["citations"],
                "citation_binding_sha256": reply["citation_binding_sha256"],
                **tts_reply,
            }
            # round 4: propagate the VERIFIED grounding provenance; absent
            # for the synthetic echo, which grounds nothing
            if grounding_sha256 is not None:
                reply_out["grounding_sha256"] = grounding_sha256
            return session_id, {
                "request_id": request_id,
                "session_id": session_id,
                "language": route.response_code,
                "transcript": dict(asr.transcript),
                "reply": reply_out,
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
