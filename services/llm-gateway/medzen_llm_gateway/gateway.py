from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from .policy import PolicyRefusal, PolicyStore
from .provider import FakeBedrockProvider, ProviderRequest
from .shared_resilience import CircuitBreaker


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MODEL_VERSION_KEYS = {"asr", "registry_snapshot", "llm", "rag", "tts"}
CITATION_KEYS = {
    "rank",
    "document_id",
    "title",
    "source_uri",
    "section",
    "content_sha256",
    "excerpt",
    "score",
}


class GatewayRefusal(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def citation_binding(citations: list[dict[str, Any]]) -> str:
    raw = json.dumps(
        citations, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _invalid(message: str) -> GatewayRefusal:
    return GatewayRefusal("INVALID_REQUEST", message, 400, False)


class LLMGateway:
    def __init__(self, policies: PolicyStore, provider: FakeBedrockProvider,
                 breaker: CircuitBreaker):
        self.policies = policies
        self.provider = provider
        self.breaker = breaker

    def complete(self, value: Any) -> dict[str, Any]:
        started = time.perf_counter()
        if not isinstance(value, dict) or set(value) != {
            "request_id", "language", "transcript", "rag", "model_versions"
        }:
            raise _invalid("request fields are incomplete or unknown")
        try:
            request_id = str(uuid.UUID(str(value["request_id"])))
        except (ValueError, TypeError, AttributeError) as exc:
            raise _invalid("request_id must be a UUID") from exc
        language = value["language"]
        if not isinstance(language, str):
            raise _invalid("language must be a string")
        try:
            policy = self.policies.load(language)
        except PolicyRefusal as exc:
            raise GatewayRefusal(
                "LANGUAGE_POLICY_UNAVAILABLE", str(exc), 422, False
            ) from exc
        transcript = value["transcript"]
        if not isinstance(transcript, dict) or set(transcript) != {
            "verbatim", "normalized", "normalization_version"
        }:
            raise _invalid("transcript fields are incomplete or unknown")
        for field in ("verbatim", "normalized", "normalization_version"):
            if not isinstance(transcript[field], str) or not transcript[field]:
                raise _invalid(f"transcript {field} must be a non-empty string")
        if max(len(transcript["verbatim"]), len(transcript["normalized"])) > (
            policy.maximum_input_characters
        ):
            raise _invalid("transcript exceeds language policy input limit")
        rag = value["rag"]
        if not isinstance(rag, dict) or set(rag) != {
            "query_id", "index_snapshot_sha256", "citations"
        }:
            raise _invalid("RAG fields are incomplete or unknown")
        if not isinstance(rag["query_id"], str) or SHA256_RE.fullmatch(
            rag["query_id"]
        ) is None:
            raise _invalid("RAG query identity is malformed")
        snapshot = rag["index_snapshot_sha256"]
        if not isinstance(snapshot, str) or SHA256_RE.fullmatch(snapshot) is None:
            raise _invalid("RAG index snapshot is malformed")
        citations = rag["citations"]
        if not isinstance(citations, list) or not citations:
            raise GatewayRefusal(
                "CITATIONS_REQUIRED",
                "at least one RAG citation is required",
                422,
                False,
            )
        if len(citations) > policy.maximum_citations:
            raise _invalid("citation count exceeds language policy limit")
        identities: set[str] = set()
        for expected_rank, citation in enumerate(citations, start=1):
            if not isinstance(citation, dict) or set(citation) != CITATION_KEYS:
                raise _invalid("citation fields are incomplete or unknown")
            if citation["rank"] != expected_rank:
                raise _invalid("citation ranks must be contiguous and ordered")
            for field in (
                "document_id", "title", "source_uri", "section", "excerpt"
            ):
                if not isinstance(citation[field], str) or not citation[field]:
                    raise _invalid(f"citation {field} must be a non-empty string")
            if citation["document_id"] in identities:
                raise _invalid("citation document identities must be unique")
            identities.add(citation["document_id"])
            if not isinstance(citation["content_sha256"], str) or SHA256_RE.fullmatch(
                citation["content_sha256"]
            ) is None:
                raise _invalid("citation content hash is malformed")
            score = citation["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)) or score <= 0:
                raise _invalid("citation score must be positive")
        versions = value["model_versions"]
        if not isinstance(versions, dict) or set(versions) != MODEL_VERSION_KEYS:
            raise _invalid("model_versions fields are incomplete or unknown")
        if versions["rag"] != f"sha256:{snapshot}":
            raise _invalid("RAG model version does not match the index snapshot")
        if not isinstance(versions["registry_snapshot"], str) or not versions[
            "registry_snapshot"
        ]:
            raise _invalid("registry snapshot identity is missing")
        binding = citation_binding(citations)
        if not self.breaker.allow():
            raise GatewayRefusal(
                "PROVIDER_CIRCUIT_OPEN",
                "LLM provider circuit is open",
                503,
                True,
            )
        provider_request = ProviderRequest(
            language=language,
            response_language=policy.response_language,
            policy_id=policy.policy_id,
            normalized_transcript=transcript["normalized"],
            citations=tuple(dict(item) for item in citations),
            citation_binding_sha256=binding,
            maximum_output_tokens=policy.maximum_output_tokens,
        )
        try:
            result = self.provider.invoke(
                provider_request, timeout_ms=policy.timeout_ms
            )
        except TimeoutError as exc:
            self.breaker.record_failure(timeout=True)
            raise GatewayRefusal(
                "PROVIDER_TIMEOUT", "LLM provider timed out", 504, True
            ) from exc
        except Exception as exc:
            self.breaker.record_failure()
            raise GatewayRefusal(
                "PROVIDER_UNAVAILABLE", "LLM provider is unavailable", 503, True
            ) from exc
        expected_ids = tuple(item["document_id"] for item in citations)
        if (
            result.model_version != self.provider.model_version
            or result.cited_document_ids != expected_ids
            or result.citation_binding_sha256 != binding
        ):
            self.breaker.record_failure()
            raise GatewayRefusal(
                "CITATION_BINDING_INVALID",
                "provider response citation binding is invalid",
                502,
                False,
            )
        if not isinstance(result.text, str) or not result.text:
            self.breaker.record_failure()
            raise GatewayRefusal(
                "PROVIDER_UNAVAILABLE", "LLM provider returned no answer", 503, True
            )
        self.breaker.record_success()
        output_versions = dict(versions)
        output_versions["llm"] = result.model_version
        return {
            "request_id": request_id,
            "language": language,
            "reply": {
                "text": result.text,
                "citations": citations,
                "citation_binding_sha256": binding,
            },
            "policy": {"id": policy.policy_id, "sha256": policy.policy_sha256},
            "provider": self.provider.name,
            "model_versions": output_versions,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
