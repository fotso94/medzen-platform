"""Language -> approved Fish voice registry (ported from the proven live
medzen-tts-dev service, reviewed 2026-08-20; that service stays untouched
as reference + fallback).

The caller sends a language; this module is the only place that knows the
provider-side reference_id. Source of truth is SSM under /medzen/registry/ (inside the
pod role's existing grant — alignment review 2026-08-20; swap a voice without a
redeploy, TTL-cached); MEDZEN_TTS_VOICES_INLINE overrides for tests; the
built-in default keeps the container bootable if SSM is unreachable.

kinyarwanda voice added 2026-08-20 BY OWNER ORDER with the owner-supplied
Fish model id — the first voice the new platform gateway ships with.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("medzen.tts.voices")


class VoiceNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class Voice:
    language: str
    reference_id: str
    model: str
    label: str
    approved: bool


_DEFAULT_REGISTRY: dict[str, dict] = {
    "kinyarwanda": {
        "reference_id": "da02ddd729004bb98133102da10c36ba",
        "model": "s1",
        "label": "Ikinyarwanda - owner-supplied voice (OWNER ORDER 2026-08-20)",
        "approved": True,
    },
    "pidgin": {
        "reference_id": "101351e6043e44888064c585143fdf6c",
        "model": "s2.1-pro-free",
        "label": "Nigerian Pidgin - marketplace voice (EVAL ONLY)",
        "approved": False,
    },
}

_SSM_PARAM = os.environ.get("MEDZEN_TTS_VOICES_SSM_PARAM", "/medzen/registry/tts/voices")
_TTL_SECONDS = float(os.environ.get("MEDZEN_TTS_VOICES_TTL_SECONDS", "300"))

_lock = threading.Lock()
_cache: dict[str, Voice] = {}
_loaded_at: float = 0.0


def _parse(raw: str) -> dict[str, Voice]:
    data = json.loads(raw)
    out: dict[str, Voice] = {}
    for lang, cfg in data.items():
        key = lang.strip().lower()
        out[key] = Voice(
            language=key,
            reference_id=cfg["reference_id"],
            model=cfg.get("model", "s1"),
            label=cfg.get("label", key),
            approved=bool(cfg.get("approved", False)),
        )
    return out


def _load() -> dict[str, Voice]:
    inline = os.environ.get("MEDZEN_TTS_VOICES_INLINE")
    if inline:
        return _parse(inline)
    try:
        import boto3
        ssm = boto3.client("ssm",
                           region_name=os.environ.get("AWS_REGION", "eu-central-1"))
        raw = ssm.get_parameter(Name=_SSM_PARAM)["Parameter"]["Value"]
        log.info("voice registry loaded from %s", _SSM_PARAM)
        return _parse(raw)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("voice registry: SSM load failed (%s); built-in default", exc)
        return _parse(json.dumps(_DEFAULT_REGISTRY))


def registry(force: bool = False) -> dict[str, Voice]:
    global _cache, _loaded_at
    with _lock:
        if force or not _cache or (time.time() - _loaded_at) > _TTL_SECONDS:
            _cache = _load()
            _loaded_at = time.time()
        return _cache


def resolve(language: str) -> Voice:
    key = (language or "").strip().lower()
    voice = registry().get(key)
    if voice is None:
        raise VoiceNotFound(
            f"no voice configured for language '{key}'; "
            f"available: {sorted(registry())}")
    return voice
