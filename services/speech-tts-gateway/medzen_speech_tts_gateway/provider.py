from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FishRequest:
    text: str
    language: str
    voice_id: str
    synthesis_key_sha256: str


@dataclass(frozen=True)
class FishResult:
    audio: bytes
    media_type: str
    model_version: str


class FishProvider(Protocol):
    name: str
    model_version: str

    def synthesize(self, request: FishRequest, *, timeout_ms: int) -> FishResult: ...


class FakeFishProvider:
    """Deterministic synthetic provider with no credentials or network path."""

    name = "fake_fish"
    model_version = "fake-fish-local-v1"
    media_type = "audio/vnd.medzen.synthetic"

    def __init__(self, outcomes: list[str] | None = None):
        self.outcomes = list(outcomes or ["success"])
        self.calls: list[tuple[FishRequest, int]] = []

    def synthesize(self, request: FishRequest, *, timeout_ms: int) -> FishResult:
        self.calls.append((request, timeout_ms))
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if outcome == "timeout":
            raise TimeoutError("synthetic Fish timeout")
        if outcome == "unavailable":
            raise RuntimeError("synthetic Fish unavailable")
        if outcome == "malformed":
            return FishResult(b"", "application/octet-stream", "wrong-model")
        if outcome != "success":
            raise RuntimeError("unknown synthetic Fish outcome")
        material = json.dumps(
            {
                "language": request.language,
                "synthesis_key_sha256": request.synthesis_key_sha256,
                "text": request.text,
                "voice_id": request.voice_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        audio = b"MEDZEN_FAKE_FISH_V1\x00" + hashlib.sha256(material).digest()
        return FishResult(audio, self.media_type, self.model_version)
