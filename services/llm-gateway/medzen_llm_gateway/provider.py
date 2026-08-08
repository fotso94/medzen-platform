from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
