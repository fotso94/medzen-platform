"""Versioned, fail-closed language conditioning for the offline pilot."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .harness import EvaluationRefusal


ASSET = Path("/opt/medzen/assets/language-conditioning-v1.json")
LOCAL_ASSET = Path(__file__).resolve().parents[1] / "assets" / "language-conditioning-v1.json"
META_ID_RE = re.compile(r"^[a-z]{3}_[A-Z][a-z]{3}$")
WHISPER_ID_RE = re.compile(r"^[a-z]{2}$")
EXPECTED_PROVIDERS = {"dataset_iso", "meta_llm", "whisper"}


def load_conditioning(path: Path | None = None) -> dict[str, dict[str, str | None]]:
    source = path or (ASSET if ASSET.is_file() else LOCAL_ASSET)
    try:
        value: Any = json.loads(source.read_bytes())
    except Exception as exc:
        raise EvaluationRefusal("conditioning approval is absent or malformed") from exc
    if value.get("schema_version") != 1 or "proxy language identifiers are prohibited" not in value.get("policy", ""):
        raise EvaluationRefusal("conditioning approval policy differs")
    languages = value.get("languages")
    if not isinstance(languages, dict) or not languages:
        raise EvaluationRefusal("conditioning approval language map is empty")
    normalized: dict[str, dict[str, str | None]] = {}
    for alias, entry in sorted(languages.items()):
        if not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", alias):
            raise EvaluationRefusal("conditioning alias is malformed")
        if not isinstance(entry, dict) or set(entry) != EXPECTED_PROVIDERS:
            raise EvaluationRefusal(f"{alias}: conditioning fields differ")
        dataset_iso = entry["dataset_iso"]
        meta_id = entry["meta_llm"]
        whisper_id = entry["whisper"]
        if not isinstance(dataset_iso, str) or re.fullmatch(r"[a-z]{3}", dataset_iso) is None:
            raise EvaluationRefusal(f"{alias}: dataset ISO identity is malformed")
        if meta_id is not None and (not isinstance(meta_id, str) or META_ID_RE.fullmatch(meta_id) is None):
            raise EvaluationRefusal(f"{alias}: Meta language identifier is malformed")
        if whisper_id is not None and (not isinstance(whisper_id, str) or WHISPER_ID_RE.fullmatch(whisper_id) is None):
            raise EvaluationRefusal(f"{alias}: Whisper language identifier is malformed")
        normalized[alias] = {
            "dataset_iso": dataset_iso,
            "meta_llm": meta_id,
            "whisper": whisper_id,
        }
    return normalized


def language_id(candidate: str, alias: str, mapping: dict[str, dict[str, str | None]]) -> str | None:
    try:
        entry = mapping[alias]
    except KeyError as exc:
        raise EvaluationRefusal(f"{alias}: no reviewed conditioning decision") from exc
    if candidate == "whisper-large-v3":
        return entry["whisper"]
    if candidate == "omniASR_LLM_1B_v2":
        return entry["meta_llm"]
    if candidate == "omniASR_CTC_1B_v2":
        return None
    raise EvaluationRefusal(f"unknown candidate: {candidate}")
