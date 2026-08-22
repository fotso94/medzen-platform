"""B6v2 S3-backed audio cache (Codex serving review finding 4).

The v1 ContentHashCache holds synthesized audio in ONE pod's memory and
returns unresolvable `medzen+local://` URIs: a restart or a second
replica loses every result and nothing can ever be fetched. This cache
stores audio in S3 under SSE-KMS and returns AUTHENTICATED, EXPIRING
presigned GET URLs. Keys are content-addressed by the synthesis key, so
replicas share results and repeats are free.

Lifecycle: the bucket prefix carries an expiration lifecycle rule
(infra/b6v2_tts_audio.tf) — cached audio is a delivery convenience,
never an archive.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredAudio:
    audio_url: str
    media_type: str
    model_version: str
    s3_key: str


class S3AudioCache:
    def __init__(self, *, bucket: str | None = None,
                 prefix: str = "tts-audio/",
                 kms_key_arn: str | None = None,
                 url_ttl_seconds: int = 900,
                 client=None):
        self.bucket = bucket or os.environ["MEDZEN_TTS_AUDIO_BUCKET"]
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
            Metadata={"model-version": model_version})
        return StoredAudio(
            audio_url=self.presign(synthesis_key_sha256),
            media_type=media_type, model_version=model_version,
            s3_key=key)

    def get(self, synthesis_key_sha256: str) -> StoredAudio | None:
        key = self._key(synthesis_key_sha256)
        try:
            head = self._s3.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:                              # noqa: BLE001
            # B6v2 (Codex serving review): ONLY a 404/NoSuchKey is a
            # cache MISS. Any other S3 failure (perms, throttling,
            # outage) must fail CLOSED — silently treating it as a miss
            # would re-synthesize and re-bill on every hiccup and hide
            # real breakage.
            code = getattr(getattr(exc, "response", None), "get",
                           lambda *_: None)("Error") if hasattr(
                               exc, "response") else None
            status = None
            if isinstance(getattr(exc, "response", None), dict):
                status = exc.response.get("Error", {}).get("Code")
                http = exc.response.get("ResponseMetadata", {}).get(
                    "HTTPStatusCode")
            else:
                http = None
            if status in ("404", "NoSuchKey", "NotFound") or http == 404 \
                    or isinstance(exc, KeyError):
                return None
            raise
        return StoredAudio(
            audio_url=self.presign(synthesis_key_sha256),
            media_type=head.get("ContentType", "audio/mpeg"),
            model_version=head.get("Metadata", {}).get("model-version", ""),
            s3_key=key)

    def presign(self, synthesis_key_sha256: str) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket,
                    "Key": self._key(synthesis_key_sha256)},
            ExpiresIn=self.url_ttl_seconds)
