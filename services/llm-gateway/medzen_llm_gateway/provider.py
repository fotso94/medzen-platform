from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    """B6v2: a provider-level refusal (e.g. blank grounding) — the
    gateway maps it to a refusal response; it must never surface as an
    unhandled 500."""


@dataclass(frozen=True)
class ProviderRequest:
    language: str
    response_language: str
    policy_id: str
    normalized_transcript: str
    citations: tuple[dict[str, Any], ...]
    citation_binding_sha256: str
    maximum_output_tokens: int


@dataclass(frozen=True)
class ProviderResult:
    text: str
    cited_document_ids: tuple[str, ...]
    citation_binding_sha256: str
    model_version: str
    # B6v2: hash of the exact citation bytes sent to the provider —
    # auditable grounding identity (was computed then DISCARDED)
    grounding_sha256: str | None = None


class FakeBedrockProvider:
    """Deterministic local provider. It contains no AWS client or network path."""

    name = "fake_bedrock"
    model_version = "fake-bedrock-local-v1"

    def __init__(self, outcomes: list[str] | None = None):
        self.outcomes = list(outcomes or ["success"])
        self.calls: list[tuple[ProviderRequest, int]] = []

    def invoke(self, request: ProviderRequest, *, timeout_ms: int) -> ProviderResult:
        self.calls.append((request, timeout_ms))
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if outcome == "timeout":
            raise TimeoutError("synthetic provider timeout")
        if outcome == "unavailable":
            raise RuntimeError("synthetic provider unavailable")
        document_ids = tuple(item["document_id"] for item in request.citations)
        binding = request.citation_binding_sha256
        if outcome == "tampered_citation":
            document_ids = ("not-supplied",)
            binding = "0" * 64
        elif outcome != "success":
            raise RuntimeError("unknown synthetic provider outcome")
        return ProviderResult(
            text=(
                f"[{request.response_language} synthetic] "
                f"Answer supported only by {', '.join(document_ids)}."
            ),
            cited_document_ids=document_ids,
            citation_binding_sha256=binding,
            model_version=self.model_version,
        )


class BedrockProvider:
    """Real Bedrock backend via the Converse API (model-family agnostic, so
    the pinned model can move between Anthropic/Nova without code changes).

    The model NEVER sees or invents the citation binding: the gateway's own
    binding is echoed back verbatim, and the model may cite only supplied
    document ids — anything else is surfaced for the gateway's tamper check.
    boto3 is imported lazily so contract tests never need AWS installed.
    """

    name = "bedrock"

    def __init__(self, model_id: str, region: str, client: Any | None = None):
        if not model_id:
            raise ValueError("MEDZEN_BEDROCK_MODEL_ID is required for the "
                             "bedrock provider — there is no default model")
        self.model_id = model_id
        # B6v2 round 3 (Codex): the registry's v2 identity is
        # "bedrock:<model-id>" (V2_LLM_RE); a bare model id can never
        # match a v2 route, so the orchestrator would refuse every
        # real response at the version check.
        self.model_version = f"bedrock:{model_id}"
        self._region = region
        self._client = client

    def _bedrock(self, timeout_ms: int):
        if self._client is not None:
            return self._client
        import boto3
        from botocore.config import Config
        self._client = boto3.client(
            "bedrock-runtime", region_name=self._region,
            config=Config(read_timeout=max(1, timeout_ms // 1000),
                          connect_timeout=5,
                          retries={"max_attempts": 1}))
        return self._client

    def invoke(self, request: ProviderRequest, *, timeout_ms: int) -> ProviderResult:
        allowed_ids = [item["document_id"] for item in request.citations]
        # B6v2 (Codex serving review): grounding comes from the explicit
        # grounding_text field; a citation with BLANK grounding refuses —
        # a document id without its text produces confidently 'cited'
        # answers that are actually ungrounded. Legacy field names are
        # accepted as fallback but blankness is never silently tolerated.
        def _grounding(item):
            for field in ("grounding_text", "content", "excerpt"):
                value = str(item.get(field) or "").strip()
                if value:
                    return value[:1200]
            raise ProviderError(
                f"citation {item.get('document_id')!r} carries no "
                "grounding text — refusing to send blank grounding")
        citations_block = "\n\n".join(
            f"[{item['document_id']}]\n{_grounding(item)}"
            for item in request.citations)
        import hashlib as _hashlib
        grounding_sha256 = _hashlib.sha256(
            citations_block.encode("utf-8")).hexdigest()
        # Dev (owner instruction 2026-08-28): the assistant is GENERAL
        # PURPOSE — users may say anything, not only medical topics. The
        # grounding contract is unchanged (answer from the supplied
        # citations and always cite at least one), but the medical framing
        # no longer causes the model to decline non-clinical transcripts,
        # which produced an empty cited_document_ids and a hard
        # CITATION_BINDING_INVALID refusal at the gateway.
        system = (
            "You are a careful, helpful assistant for the MedZen platform.\n"
            "The user may ask about ANY topic; engage with whatever they say.\n"
            f"Respond ONLY in {request.response_language}.\n"
            "Ground factual claims in the supplied citations. You MUST cite at "
            "least one supplied document id in cited_document_ids, choosing the "
            "most relevant one even if the match is partial.\n"
            "If the citations do not fully answer, say what you can and note "
            "the limit; for health questions suggest consulting a clinician.\n"
            "Never repeat or invent personal identifiers.\n"
            "Reply with EXACTLY one JSON object, no markdown fences:\n"
            '{"text": "<your reply>", "cited_document_ids": ["<id>", ...]}\n'
            f"cited_document_ids MUST be a subset of {allowed_ids}."
        )
        user = (
            f"User transcript ({request.language}):\n"
            f"{request.normalized_transcript}\n\n"
            f"Citations:\n{citations_block if citations_block else '(none supplied)'}"
        )
        response = self._bedrock(timeout_ms).converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={
                "maxTokens": request.maximum_output_tokens,
                "temperature": 0.2,
            },
        )
        parts = response.get("output", {}).get("message", {}).get("content", [])
        raw = "".join(p.get("text", "") for p in parts).strip()
        import json as _json
        try:
            if raw.startswith("```"):
                raw = raw.strip("`").removeprefix("json").strip()
            payload = _json.loads(raw)
            text = str(payload["text"])
            cited = tuple(str(x) for x in payload.get("cited_document_ids", []))
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"bedrock reply was not the contracted JSON shape: {exc}") from exc
        return ProviderResult(
            text=text,
            cited_document_ids=cited,
            citation_binding_sha256=request.citation_binding_sha256,
            model_version=self.model_version,
            grounding_sha256=grounding_sha256,
        )
