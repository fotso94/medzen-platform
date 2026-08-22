from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class FishRequest:
    text: str
    language: str
    voice_id: str
    synthesis_key_sha256: str
    # B6v2 round 3 (Codex): each voice declares its Fish model in the
    # registry, but the request never carried it — the provider always
    # used its constructor default, silently switching models. None
    # keeps the provider default (v1-proof paths).
    model: str | None = None


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


class RealFishProvider:
    """Fish Audio cloud synthesis — the call shape ported verbatim from the
    proven live medzen-tts-dev provider (reviewed 2026-08-20, not modified).
    The API key comes from Secrets Manager (medzen/speech/fish-api-key —
    the platform-namespace secret; the live dev service keeps its own
    medzen/tts/dev/fish-api-key) or MEDZEN_FISH_API_KEY for local runs; it
    is never logged and 401/403 bodies are never echoed (they can contain
    key fragments). requests/boto3 import lazily so contract tests need
    neither. Handoff-verification fix 2026-08-20: the previous default
    named a secret that does not exist in Secrets Manager."""

    name = "fish"
    media_type = "audio/mpeg"

    def __init__(self, model: str = "s1", api_key: str | None = None,
                 secret_id: str = "medzen/speech/fish-api-key",
                 base_url: str = "https://api.fish.audio",
                 session: Any | None = None):
        self.model = model
        self.model_version = f"fish:{model}"
        self._api_key = api_key
        self._secret_id = secret_id
        self._base_url = base_url.rstrip("/")
        self._session = session

    def _key(self) -> str:
        if self._api_key:
            return self._api_key
        import os
        env_key = os.environ.get("MEDZEN_FISH_API_KEY")
        if env_key:
            self._api_key = env_key
            return env_key
        import boto3
        client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "eu-central-1"))
        raw = client.get_secret_value(SecretId=self._secret_id)["SecretString"]
        try:
            self._api_key = json.loads(raw).get("api_key", raw)
        except ValueError:
            self._api_key = raw
        return self._api_key

    def _http(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def synthesize(self, request: FishRequest, *, timeout_ms: int) -> FishResult:
        # round 3: honour the registry-declared per-voice model; the
        # constructor default only covers requests that carry none
        model = request.model or self.model
        response = self._http().post(
            f"{self._base_url}/v1/tts",
            headers={"Authorization": f"Bearer {self._key()}",
                     "Content-Type": "application/json",
                     "model": model},
            json={"text": request.text,
                  "reference_id": request.voice_id,
                  "format": "mp3",
                  "normalize": True,
                  "latency": "balanced"},
            timeout=(5, max(1, timeout_ms // 1000)),
        )
        if response.status_code in (401, 403):
            raise RuntimeError("fish rejected the API key")   # never echo body
        if response.status_code == 429:
            raise RuntimeError("fish rate limited")
        if not response.ok:
            raise RuntimeError(f"fish HTTP {response.status_code}: "
                               f"{response.text[:200]}")
        if not response.content:
            raise RuntimeError("fish returned an empty body")
        return FishResult(response.content, self.media_type, f"fish:{model}")
