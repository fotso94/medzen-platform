from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BackendRefusal(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcript:
    language: str
    language_probability: float
    verbatim: str
    normalized: str
    normalization_version: str
    duration_seconds: float


def load_ready_marker(model_dir: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    marker_path = model_dir / ".medzen-ready.json"
    try:
        marker = json.loads(marker_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackendRefusal("verified model-loader ready marker is absent") from exc
    if marker.get("ready") is not True:
        raise BackendRefusal("model-loader marker is not ready")
    if marker.get("classification") != "PLATFORM_PROOF_ONLY":
        raise BackendRefusal("model is not classified as a platform proof")
    if marker.get("serving_label") != "v0":
        raise BackendRefusal("ASR runtime accepts v0 only in B6A")
    if marker.get("production_approved") is not False:
        raise BackendRefusal("production-approved identity is forbidden in B6A")
    if marker.get("quality_gate_outcome") != "FAIL":
        raise BackendRefusal("B6A quality-failure disclosure is absent")
    if (not isinstance(expected_manifest_sha256, str)
            or marker.get("manifest_sha256") != expected_manifest_sha256):
        raise BackendRefusal("model manifest identity differs from deployment pin")
    if (not isinstance(marker.get("artifact_tree_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", marker["artifact_tree_sha256"]) is None):
        raise BackendRefusal("model artifact tree identity is malformed")
    if marker.get("precision") != "CTranslate2_float16":
        raise BackendRefusal("B6A runtime requires CTranslate2 float16")
    if not isinstance(marker.get("smoke_inference"), dict) or marker[
            "smoke_inference"].get("passed") is not True:
        raise BackendRefusal("model-loader smoke inference is not proven")
    return marker


class FasterWhisperBackend:
    ready = True

    def __init__(self, model_dir: Path, expected_manifest_sha256: str):
        self.marker = load_ready_marker(model_dir, expected_manifest_sha256)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise BackendRefusal("faster-whisper is absent from the runtime image") from exc
        device = os.environ.get("MEDZEN_INFERENCE_DEVICE", "cuda")
        compute_type = "float16" if device == "cuda" else "int8"
        self.model = WhisperModel(
            str(model_dir), device=device, compute_type=compute_type,
            local_files_only=True)
        self.model_versions = {
            "asr": "v0",
            "registry_snapshot": (
                "b6a-non-serving:" + self.marker["manifest_sha256"]),
            "llm": None,
            "rag": None,
            "tts": None,
        }

    def transcribe(self, audio_path: Path, language_hint: str | None) -> Transcript:
        decode = self.marker["decode_configuration"]
        kwargs = {
            "task": decode["task"],
            "beam_size": decode["beam_size"],
            "best_of": decode["best_of"],
            "temperature": decode["temperature"],
            "condition_on_previous_text": decode["condition_on_previous_text"],
            "word_timestamps": decode["word_timestamps"],
            "vad_filter": False,
        }
        if language_hint:
            kwargs["language"] = language_hint
        segments, info = self.model.transcribe(str(audio_path), **kwargs)
        verbatim = "".join(segment.text for segment in segments).strip()
        normalized = " ".join(unicodedata.normalize("NFC", verbatim).split())
        return Transcript(
            language=str(getattr(info, "language", language_hint or "und")),
            language_probability=float(getattr(info, "language_probability", 0.0)),
            verbatim=verbatim,
            normalized=normalized,
            normalization_version="b6a-unicode-nfc-whitespace-v1",
            duration_seconds=float(getattr(info, "duration", 0.0)),
        )
