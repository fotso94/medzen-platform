"""B6v2 S3-backed audio cache (Codex serving review finding 4; round 3).

The v1 ContentHashCache holds synthesized audio in ONE pod's memory and
returns unresolvable `medzen+local://` URIs: a restart or a second
replica loses every result and nothing can ever be fetched. This cache
stores audio in S3 under SSE-KMS and returns AUTHENTICATED, EXPIRING
presigned GET URLs. Keys are content-addressed by the synthesis key, so
replicas share results and repeats are free.

Round 3 (Codex): the first version shipped its own get()/put() surface
while the gateway's main path calls get()/get_or_create() returning
CachedAudio — the S3 path crashed on first use. This class now speaks
the SAME interface as ContentHashCache (get -> CachedAudio | None,
get_or_create -> (CachedAudio, hit)) plus the S3-only put/presign the
delivery helper feature-detects. Bucket/prefix default to the EXISTING
medzen-audio-cache bucket under tts/ — the exact resource the live
medzen-tts-role grant names — instead of a nonexistent new bucket.

Lifecycle: infra/b6v2_tts_audio.tf attaches an expiration rule to the
tts/ prefix — cached audio is a delivery convenience, never an archive.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Callable

from .cache import CachedAudio, CacheRefusal


@dataclass(frozen=True)
class StoredAudio:
    audio_url: str
    media_type: str
    model_version: str
    s3_key: str


class S3AudioCache:
    def __init__(self, *, bucket: str | None = None,
                 prefix: str = "tts/",
                 kms_key_arn: str | None = None,
                 url_ttl_seconds: int = 900,
                 client=None):
        self.bucket = bucket or os.environ.get(
            "MEDZEN_TTS_AUDIO_BUCKET", "medzen-audio-cache")
        self.prefix = prefix
        self.kms_key_arn = kms_key_arn or os.environ.get(
            "MEDZEN_TTS_AUDIO_KMS_ARN")
        if not self.kms_key_arn:
            raise RuntimeError("S3AudioCache requires a KMS key — "
                               "unencrypted audio storage is refused")
        self.url_ttl_seconds = int(url_ttl_seconds)
        if client is None:
            import boto3
            client = boto3.client(
                "s3", region_name=os.environ.get("AWS_REGION",
                                                  "eu-central-1"))
        self._s3 = client

    def _key(self, synthesis_key_sha256: str) -> str:
        return f"{self.prefix}{synthesis_key_sha256}.mp3"

    def put(self, *, synthesis_key_sha256: str, audio_bytes: bytes,
            media_type: str, model_version: str) -> StoredAudio:
        key = self._key(synthesis_key_sha256)
        self._s3.put_object(
            Bucket=self.bucket, Key=key, Body=audio_bytes,
            ContentType=media_type,
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self.kms_key_arn,
            Metadata={
                "model-version": model_version,
                "audio-sha256": hashlib.sha256(audio_bytes).hexdigest(),
            })
        return StoredAudio(
            audio_url=self.presign(synthesis_key_sha256),
            media_type=media_type, model_version=model_version,
            s3_key=key)

    def get(self, synthesis_key_sha256: str) -> CachedAudio | None:
        key = self._key(synthesis_key_sha256)
        try:
            head = self._s3.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:                              # noqa: BLE001
            # B6v2 (Codex serving review): ONLY a definite not-found is a
            # cache MISS. Any other S3 failure (perms, throttling,
            # outage) must fail CLOSED — silently treating it as a miss
            # would re-synthesize and re-bill on every hiccup and hide
            # real breakage. NOTE: HeadObject without s3:ListBucket
            # returns 403 for missing keys — the role must carry the
            # prefix-scoped ListBucket grant for 404 semantics.
            status = None
            http = None
            if isinstance(getattr(exc, "response", None), dict):
                status = exc.response.get("Error", {}).get("Code")
                http = exc.response.get("ResponseMetadata", {}).get(
                    "HTTPStatusCode")
            if status in ("404", "NoSuchKey", "NotFound") or http == 404 \
                    or isinstance(exc, KeyError):
                return None
            raise
        metadata = head.get("Metadata", {})
        audio_sha256 = metadata.get("audio-sha256", "")
        model_version = metadata.get("model-version", "")
        if not audio_sha256 or not model_version:
            # an object without its integrity metadata cannot be served;
            # treating it as a miss re-synthesizes and heals the entry
            return None
        # S3 hits deliver by presigned URL — the bytes stay in S3, so the
        # shared CachedAudio shape carries an empty payload on this path.
        return CachedAudio(
            synthesis_key_sha256=synthesis_key_sha256,
            audio=b"",
            audio_sha256=audio_sha256,
            media_type=head.get("ContentType", "audio/mpeg"),
            model_version=model_version,
        )

    def get_or_create(
        self, key: str, factory: Callable[[], CachedAudio]
    ) -> tuple[CachedAudio, bool]:
        existing = self.get(key)
        if existing is not None:
            return existing, True
        created = factory()
        # same admission contract as ContentHashCache — an entry that
        # does not match its key or its own checksum never enters
        if created.synthesis_key_sha256 != key:
            raise CacheRefusal("cache entry does not match its synthesis key")
        if not created.audio:
            raise CacheRefusal("empty audio cannot enter the cache")
        if hashlib.sha256(created.audio).hexdigest() != created.audio_sha256:
            raise CacheRefusal("cached audio checksum does not match")
        self.put(
            synthesis_key_sha256=key,
            audio_bytes=created.audio,
            media_type=created.media_type,
            model_version=created.model_version,
        )
        return created, False

    def presign(self, synthesis_key_sha256: str) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket,
                    "Key": self._key(synthesis_key_sha256)},
            ExpiresIn=self.url_ttl_seconds)
