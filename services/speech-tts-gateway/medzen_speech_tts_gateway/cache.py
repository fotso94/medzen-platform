from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Callable


class CacheRefusal(RuntimeError):
    pass


@dataclass(frozen=True)
class CachedAudio:
    synthesis_key_sha256: str
    audio: bytes
    audio_sha256: str
    media_type: str
    model_version: str


class ContentHashCache:
    """Process-local B6.5 cache; B6.6 must bind the S3 cache implementation."""

    def __init__(self):
        self._entries: dict[str, CachedAudio] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> CachedAudio | None:
        with self._lock:
            return self._entries.get(key)

    def get_or_create(
        self, key: str, factory: Callable[[], CachedAudio]
    ) -> tuple[CachedAudio, bool]:
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                return existing, True
            created = factory()
            if created.synthesis_key_sha256 != key:
                raise CacheRefusal("cache entry does not match its synthesis key")
            if not created.audio:
                raise CacheRefusal("empty audio cannot enter the cache")
            if hashlib.sha256(created.audio).hexdigest() != created.audio_sha256:
                raise CacheRefusal("cached audio checksum does not match")
            self._entries[key] = created
            return created, False

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _process_delivery_url(synthesis_key_sha256: str) -> str:
    # B6v2: the process-local cache keeps the v1-proof local URI scheme;
    # only the S3 cache yields fetchable URLs. This helper lets the
    # gateway ask "how do I deliver this key?" uniformly.
    return f"medzen+local://tts/{synthesis_key_sha256}"
