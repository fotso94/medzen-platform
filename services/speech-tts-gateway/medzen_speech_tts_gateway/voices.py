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
        "consent_evidence": "owner order 2026-08-20 (chat, verbatim): owner-supplied voice for kinyarwanda",
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


class RegistryUnavailable(RuntimeError):
    """B6v2: the voice registry could not be authoritatively loaded —
    synthesis must fail CLOSED, never fall back to built-in real
    reference ids (Codex serving review finding 5)."""


def _strict_bool(value, field: str, context: str) -> bool:
    """B6v2: bool("false") is True — string booleans silently APPROVED
    every voice. Only real JSON booleans are accepted."""
    if isinstance(value, bool):
        return value
    raise RegistryUnavailable(
        f"voice {context}: {field} must be a JSON boolean, got "
        f"{value!r} — refusing a registry that cannot be trusted")


def _parse(raw: str) -> dict[str, Voice]:
    data = json.loads(raw)
    out: dict[str, Voice] = {}
    for lang, cfg in data.items():
        key = lang.strip().lower()
        approved = _strict_bool(cfg.get("approved", False), "approved", key)
        # approval only COUNTS with consent/usage-rights evidence (B6v2)
        evidence = str(cfg.get("consent_evidence") or "").strip()
        if approved and not evidence:
            approved = False
        out[key] = Voice(
            language=key,
            reference_id=cfg["reference_id"],
            model=cfg.get("model", "s1"),
            label=cfg.get("label", key),
            approved=approved,
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
        # B6v2: FAIL CLOSED. The built-in fallback held REAL reference
        # ids and re-enabled voices the registry may have revoked. The
        # legacy fallback survives ONLY behind an explicit env flag for
        # the closed v1 synthetic proof.
        if os.environ.get("MEDZEN_TTS_ALLOW_BUILTIN_FALLBACK") == "1":
            log.warning("voice registry: SSM failed (%s); LEGACY builtin "
                        "fallback (v1-proof flag set)", exc)
            return _parse(json.dumps(_DEFAULT_REGISTRY))
        raise RegistryUnavailable(
            f"voice registry unavailable ({type(exc).__name__}) — "
            "failing closed") from exc


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


class VoiceRefusal(RuntimeError):
    """B6v2: synthesis-time governance refusals."""


def select_voice(language: str) -> Voice:
    """B6v2 selection: the ONLY sanctioned path from language to a Fish
    reference id. Unknown or unapproved voices refuse — approval means a
    strict boolean TRUE backed by consent evidence (enforced at parse)."""
    voices = registry()
    voice = voices.get(language.strip().lower())
    if voice is None:
        raise VoiceRefusal(f"no voice registered for {language!r}")
    if not voice.approved:
        raise VoiceRefusal(
            f"voice for {language!r} is not approved for synthesis "
            "(approval requires consent/usage-rights evidence)")
    return voice


def enforce_model(voice: Voice, requested_model: str | None) -> str:
    """Each voice declares its Fish model; a request may not silently
    upgrade or downgrade it (billing and quality both differ)."""
    if requested_model is not None and requested_model != voice.model:
        raise VoiceRefusal(
            f"voice {voice.language!r} is bound to Fish model "
            f"{voice.model!r}; requested {requested_model!r}")
    return voice.model
