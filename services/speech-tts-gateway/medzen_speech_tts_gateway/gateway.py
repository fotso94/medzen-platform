from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from .cache import CacheRefusal, CachedAudio, ContentHashCache
from .provider import FishProvider, FishRequest
from .shared_resilience import CircuitBreaker


LANGUAGE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MODEL_VERSION_KEYS = {"asr", "registry_snapshot", "llm", "rag", "tts"}
MAX_TEXT_CHARACTERS = 8192
VOICE_ID = "b6-local-synthetic-fish-voice"
MEDIA_TYPE = "audio/vnd.medzen.synthetic"
FISH_TIMEOUT_MS = 2000


class TTSRefusal(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def _invalid(message: str) -> TTSRefusal:
    return TTSRefusal("INVALID_REQUEST", message, 400, False)


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def synthesis_key(
    *, text_sha256: str, language: str, provider: str,
    voice_id: str, model_version: str, media_type: str
) -> str:
    material = json.dumps(
        {
            "language": language,
            "media_type": media_type,
            "model_version": model_version,
            "provider": provider,
            "text_sha256": text_sha256,
            "voice_id": voice_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class TTSGateway:
    def __init__(
        self,
        *,
        provider: FishProvider | None,
        breaker: CircuitBreaker | None,
        cache: ContentHashCache | None = None,
        voice_resolver: Any | None = None,
    ):
        if (provider is None) != (breaker is None):
            raise ValueError("Fish provider and breaker must be configured together")
        self.provider = provider
        self.breaker = breaker
        self.cache = cache or ContentHashCache()
        # default keeps the synthetic voice id — fake/text-only behavior is
        # byte-identical; the real mode injects the SSM-backed resolver
        self._voice_id = voice_resolver or (lambda language: VOICE_ID)
        # the DEEP-REVIEW catch (2026-08-20): validating against the
        # synthetic constant would reject every real Fish result as
        # malformed — the expected media type belongs to the provider
        self._media_type = getattr(provider, "media_type", MEDIA_TYPE)

    @property
    def backend_mode(self) -> str:
        return "fake_fish_with_text_only_fallback" if self.provider else "text_only"

    def _validate(self, value: Any) -> tuple[str, str, str, dict[str, str | None]]:
        if not isinstance(value, dict) or set(value) != {
            "request_id", "language", "text", "model_versions"
        }:
            raise _invalid("request fields are incomplete or unknown")
        try:
            request_id = str(uuid.UUID(str(value["request_id"])))
        except (ValueError, TypeError, AttributeError) as exc:
            raise _invalid("request_id must be a UUID") from exc
        language = value["language"]
        if not isinstance(language, str) or LANGUAGE_RE.fullmatch(language) is None:
            raise _invalid("language must be a lowercase registry alias")
        text = value["text"]
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT_CHARACTERS:
            raise _invalid("text must contain between 1 and 8192 characters")
        versions = value["model_versions"]
        if not isinstance(versions, dict) or set(versions) != MODEL_VERSION_KEYS:
            raise _invalid("model_versions fields are incomplete or unknown")
        if not isinstance(versions["registry_snapshot"], str) or not versions[
            "registry_snapshot"
        ]:
            raise _invalid("registry snapshot identity is missing")
        if versions["tts"] is not None:
            raise _invalid("incoming TTS model version must be null")
        return request_id, language, text, dict(versions)

    @staticmethod
    def _response(
        *, request_id: str, language: str, text: str, text_hash: str,
        key: str | None, backend: str, provider: str, audio_url: str | None,
        media_type: str | None, audio_sha256: str | None,
        versions: dict[str, str | None], cache_hit: bool,
        provider_attempted: bool, degradation_reason: str | None,
    ) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "language": language,
            "text": text,
            "content_sha256": text_hash,
            "synthesis_key_sha256": key,
            "tts_backend": backend,
            "provider": provider,
            "audio_url": audio_url,
            "media_type": media_type,
            "audio_sha256": audio_sha256,
            "model_versions": versions,
            "cache_hit": cache_hit,
            "provider_attempted": provider_attempted,
            "degradation_reason": degradation_reason,
        }

    def _text_only(
        self, *, request_id: str, language: str, text: str, text_hash: str,
        versions: dict[str, str | None], key: str | None, provider: str,
        provider_attempted: bool, reason: str,
    ) -> dict[str, Any]:
        output_versions = dict(versions)
        output_versions["tts"] = None
        return self._response(
            request_id=request_id,
            language=language,
            text=text,
            text_hash=text_hash,
            key=key,
            backend="text_only",
            provider=provider,
            audio_url=None,
            media_type=None,
            audio_sha256=None,
            versions=output_versions,
            cache_hit=False,
            provider_attempted=provider_attempted,
            degradation_reason=reason,
        )

    def synthesize(self, value: Any) -> dict[str, Any]:
        request_id, language, text, versions = self._validate(value)
        text_hash = content_sha256(text)
        if self.provider is None:
            return self._text_only(
                request_id=request_id,
                language=language,
                text=text,
                text_hash=text_hash,
                versions=versions,
                key=None,
                provider="text_only",
                provider_attempted=False,
                reason="POLICY_TEXT_ONLY",
            )
        assert self.breaker is not None
        voice_id = self._voice_id(language)
        key = synthesis_key(
            text_sha256=text_hash,
            language=language,
            provider=self.provider.name,
            voice_id=voice_id,
            model_version=self.provider.model_version,
            media_type=self._media_type,
        )
        cached = self.cache.get(key)
        if cached is not None:
            return self._fish_response(
                request_id, language, text, text_hash, cached, versions,
                cache_hit=True, provider_attempted=False,
            )
        if not self.breaker.allow():
            return self._text_only(
                request_id=request_id,
                language=language,
                text=text,
                text_hash=text_hash,
                versions=versions,
                key=key,
                provider=self.provider.name,
                provider_attempted=False,
                reason="FISH_CIRCUIT_OPEN",
            )
        attempted = False

        def create() -> CachedAudio:
            nonlocal attempted
            attempted = True
            result = self.provider.synthesize(
                FishRequest(text, language, voice_id, key),
                timeout_ms=FISH_TIMEOUT_MS,
            )
            if (
                not isinstance(result.audio, bytes)
                or not result.audio
                or result.media_type != self._media_type
                or result.model_version != self.provider.model_version
            ):
                raise CacheRefusal("Fish response does not match the local contract")
            return CachedAudio(
                key,
                result.audio,
                hashlib.sha256(result.audio).hexdigest(),
                result.media_type,
                result.model_version,
            )

        try:
            audio, hit = self.cache.get_or_create(key, create)
        except TimeoutError:
            self.breaker.record_failure(timeout=True)
            return self._text_only(
                request_id=request_id, language=language, text=text,
                text_hash=text_hash, versions=versions, key=key,
                provider=self.provider.name, provider_attempted=attempted,
                reason="FISH_TIMEOUT",
            )
        except CacheRefusal:
            self.breaker.record_failure()
            return self._text_only(
                request_id=request_id, language=language, text=text,
                text_hash=text_hash, versions=versions, key=key,
                provider=self.provider.name, provider_attempted=attempted,
                reason="FISH_INVALID_RESPONSE",
            )
        except Exception:
            self.breaker.record_failure()
            return self._text_only(
                request_id=request_id, language=language, text=text,
                text_hash=text_hash, versions=versions, key=key,
                provider=self.provider.name, provider_attempted=attempted,
                reason="FISH_UNAVAILABLE",
            )
        if not hit:
            self.breaker.record_success()
        return self._fish_response(
            request_id, language, text, text_hash, audio, versions,
            cache_hit=hit, provider_attempted=attempted,
        )

    def _fish_response(
        self, request_id: str, language: str, text: str, text_hash: str,
        audio: CachedAudio, versions: dict[str, str | None], *,
        cache_hit: bool, provider_attempted: bool,
    ) -> dict[str, Any]:
        output_versions = dict(versions)
        output_versions["tts"] = audio.model_version
        return self._response(
            request_id=request_id,
            language=language,
            text=text,
            text_hash=text_hash,
            key=audio.synthesis_key_sha256,
            backend="fish",
            provider=self.provider.name if self.provider else "text_only",
            audio_url=f"medzen+local://tts/{audio.synthesis_key_sha256}",
            media_type=audio.media_type,
            audio_sha256=audio.audio_sha256,
            versions=output_versions,
            cache_hit=cache_hit,
            provider_attempted=provider_attempted,
            degradation_reason=None,
        )
