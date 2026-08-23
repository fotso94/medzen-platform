from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import RegistryRoute


class LocalDependencyRefusal(RuntimeError):
    """A local synthetic dependency is missing or disagrees with the registry."""


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _add_service_import(name: str) -> None:
    path = str(_repository_root() / "services" / name)
    if path not in sys.path:
        sys.path.insert(0, path)


@dataclass(frozen=True)
class ASRResult:
    request_id: str
    language: str
    language_probability: float
    transcript: dict[str, str]
    duration_seconds: float
    model_versions: dict[str, str | None]
    # round 7 (Codex): the FULL 64-char artifact tree digest a v2 ASR
    # runtime reports; None on the frozen v0/v1 paths
    artifact_tree_sha256: str | None = None


class SyntheticASRClient:
    """Accepts exactly one generated non-speech WAV fixture."""

    def __init__(self, fixture_binding: Path):
        try:
            value: Any = json.loads(fixture_binding.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LocalDependencyRefusal("synthetic ASR fixture is unavailable") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "classification", "audio_sha256", "audio_format",
            "duration_seconds", "language", "transcript", "model_version"
        }:
            raise LocalDependencyRefusal("synthetic ASR fixture is malformed")
        transcript = value["transcript"]
        if (
            value["schema_version"] != 1
            or value["classification"] != "B6_3_LOCAL_SYNTHETIC_NON_SPEECH"
            or value["audio_format"] != {
                "container": "wav", "encoding": "pcm_s16le",
                "sample_rate_hz": 16000, "channels": 1
            }
            or not isinstance(value["audio_sha256"], str)
            or len(value["audio_sha256"]) != 64
            or not isinstance(value["duration_seconds"], (int, float))
            or value["duration_seconds"] <= 0
            or value["language"] != "en"
            or not isinstance(transcript, dict)
            or set(transcript) != {"verbatim", "normalized", "normalization_version"}
            or not all(isinstance(item, str) and item for item in transcript.values())
            or value["model_version"] != "v0-local-synthetic-asr"
        ):
            raise LocalDependencyRefusal("synthetic ASR fixture is unsafe or inconsistent")
        self._binding = value

    def transcribe(
        self, audio: bytes, *, request_id: str, route: RegistryRoute
    ) -> ASRResult:
        if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
            raise LocalDependencyRefusal("audio must be a WAV file")
        digest = hashlib.sha256(audio).hexdigest()
        if digest != self._binding["audio_sha256"]:
            raise LocalDependencyRefusal(
                "B6.3 accepts only the checksum-bound synthetic audio fixture"
            )
        if (
            route.asr_backend != "local_synthetic_fixture"
            or route.asr_fixture_sha256 != digest
            or route.asr_model_version != self._binding["model_version"]
        ):
            raise LocalDependencyRefusal("ASR fixture and registry route do not match")
        versions = route.model_versions
        return ASRResult(
            request_id=request_id,
            language=self._binding["language"],
            language_probability=1.0,
            transcript=dict(self._binding["transcript"]),
            duration_seconds=float(self._binding["duration_seconds"]),
            model_versions=versions,
        )


class LocalRAGClient:
    def __init__(self, index_root: Path):
        _add_service_import("rag-index")
        from medzen_rag_index.index import IndexRepository

        self._repository = IndexRepository(index_root, "current")

    def retrieve(
        self, *, request_id: str, query: str, route: RegistryRoute
    ) -> dict[str, Any]:
        loaded = self._repository.loaded
        if (
            route.rag_alias != loaded.alias
            or route.rag_snapshot_sha256 != loaded.snapshot_sha256
        ):
            raise LocalDependencyRefusal("RAG index and registry route do not match")
        normalized = " ".join(query.casefold().split())
        query_id = hashlib.sha256(
            (
                loaded.snapshot_sha256 + "\n" + route.rag_query_language
                + "\n" + normalized
            ).encode("utf-8")
        ).hexdigest()
        citations = self._repository.search(
            query, language=route.rag_query_language, top_k=3
        )
        return {
            "request_id": request_id,
            "query_id": query_id,
            "index": {
                "alias": loaded.alias,
                "version": loaded.version,
                "snapshot_sha256": loaded.snapshot_sha256,
                "classification": loaded.classification,
            },
            "citations": citations,
            "model_versions": {
                **route.model_versions,
                "rag": f"sha256:{loaded.snapshot_sha256}",
            },
        }


class LocalLLMClient:
    def __init__(self, registry_dir: Path, policy_source: Path):
        _add_service_import("llm-gateway")
        from medzen_llm_gateway.gateway import LLMGateway
        from medzen_llm_gateway.policy import PolicyStore
        from medzen_llm_gateway.provider import FakeBedrockProvider
        from medzen_llm_gateway.shared_resilience import CircuitBreaker, load_config

        policies = PolicyStore(registry_dir, policy_source)
        policies.validate_all()
        config = load_config()
        defaults = config["circuit_breakers"]["defaults"]
        bedrock = config["circuit_breakers"]["per_provider"]["bedrock"]
        breaker = CircuitBreaker(
            name="bedrock",
            failure_threshold=bedrock["failure_threshold"],
            timeout_threshold=defaults["timeout_threshold"],
            window_s=defaults["window_s"],
            open_duration_s=bedrock["open_duration_s"],
            half_open_max_calls=defaults["half_open_max_calls"],
        )
        self._gateway = LLMGateway(policies, FakeBedrockProvider(), breaker)

    def complete(
        self,
        *,
        request_id: str,
        transcript: dict[str, str],
        rag: dict[str, Any],
        versions: dict[str, str | None],
        route: RegistryRoute,
    ) -> dict[str, Any]:
        result = self._gateway.complete({
            "request_id": request_id,
            "language": route.alias,
            "transcript": transcript,
            "rag": {
                "query_id": rag["query_id"],
                "index_snapshot_sha256": rag["index"]["snapshot_sha256"],
                "citations": rag["citations"],
            },
            "model_versions": versions,
        })
        if (
            result["policy"]["id"] != route.llm_policy_id
            or result["model_versions"]["llm"] != route.llm_model_version
        ):
            raise LocalDependencyRefusal("LLM result and registry route do not match")
        return result
