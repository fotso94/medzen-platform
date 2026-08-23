"""B6v2 OmniASR serving backend (Codex round 3).

Round 2 shipped `medzen_model_loader.omniasr_runtime`, which imported a
nonexistent decode path (`pipeline.omniasr_infer.decode_ctc`), called
torch.load(weights_only=False) on the artifact, and claimed a Dockerfile
wiring that did not exist — a loader that does not load, in the image
whose contract forbids inference. That module is deleted.

This backend lives where inference lives — the ASR runtime — and reuses
the SAME stack that actually evaluated the trained model at the T6 gate:
omnilingual_asr's ASRInferencePipeline on the fairseq2 asset card, cuda +
bfloat16 (services/asr-eval-runtime/medzen_asr_eval/backends.py,
MetaBackend). Trust follows the existing v0 architecture: the
verification-only model-loader init container digest-verifies the
artifact via loader_v2 and writes a ready marker; this backend refuses to
serve until that marker attests a verified v2 artifact staged at the
asset card's checkpoint path.

Ships DARK: the default backend remains FasterWhisperBackend; this one
activates only under MEDZEN_ASR_BACKEND=omniasr in the GPU serving image
(which installs omnilingual_asr/fairseq2 — the CPU image does not).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from .backend import BackendRefusal, Transcript

V2_MODEL_VERSION_RE = re.compile(r"^omniasr_ctc_1b:[0-9a-f]{12}$")
NORMALIZATION_VERSION = "b6v2-unicode-nfc-whitespace-v1"


def load_v2_ready_marker(model_dir: Path) -> dict[str, Any]:
    marker_path = model_dir / ".medzen-ready-v2.json"
    try:
        marker = json.loads(marker_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackendRefusal(
            "verified v2 model-loader ready marker is absent") from exc
    if marker.get("schema_version") != 3:
        raise BackendRefusal("v2 model-loader marker schema is not supported")
    if marker.get("artifact_verified") is not True:
        raise BackendRefusal("model-loader has not verified the v2 artifact")
    if marker.get("classification") not in (
        "NONPROD_REAL_PROVIDER_V2", "PRODUCTION"
    ):
        raise BackendRefusal("v2 marker classification is unknown")
    version = marker.get("model_version")
    digest = str(marker.get("artifact_sha256") or "")
    if (not isinstance(version, str)
            or V2_MODEL_VERSION_RE.fullmatch(version) is None
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or version != f"omniasr_ctc_1b:{digest[:12]}"):
        raise BackendRefusal(
            "v2 marker identity is malformed — the version IS the digest")
    if not isinstance(marker.get("manifest_sha256"), str) or re.fullmatch(
        r"[0-9a-f]{64}", marker["manifest_sha256"]
    ) is None:
        raise BackendRefusal("v2 marker manifest identity is malformed")
    languages = marker.get("language_ids")
    if not isinstance(languages, dict) or not languages or not all(
        isinstance(k, str) and isinstance(v, str) and v
        for k, v in languages.items()
    ):
        raise BackendRefusal(
            "v2 marker must map every served language to its omnilingual id")
    checkpoint = Path(str(marker.get("checkpoint_path") or ""))
    if not checkpoint.is_file():
        raise BackendRefusal(
            "verified artifact is not staged at the asset card's "
            f"checkpoint path ({checkpoint})")
    if marker.get("asset_card") != "medzen_omniASR_CTC_1B_v2":
        raise BackendRefusal(
            "v2 serving is bound to the reviewed CTC asset card only")
    return marker


class OmniASRBackend:
    def __init__(self, model_dir: Path):
        self.ready = False
        self.marker = load_v2_ready_marker(model_dir)
        try:
            import torch
            from omnilingual_asr.models.inference.pipeline import (
                ASRInferencePipeline,
            )
        except ImportError as exc:
            raise BackendRefusal(
                "omnilingual_asr is absent — the OmniASR backend runs only "
                "in the GPU serving image") from exc
        device = os.environ.get("MEDZEN_INFERENCE_DEVICE", "cuda")
        # the T6-proven combination: the eval runtime's MetaBackend runs
        # this exact card on cuda + bfloat16
        self._pipeline = ASRInferencePipeline(
            model_card=self.marker["asset_card"],
            device=device,
            dtype=torch.bfloat16,
        )
        self._language_ids: dict[str, str] = dict(self.marker["language_ids"])
        # round 4 (Codex): the runtime reports the VERIFIED classification
        # from the marker — never a hardcoded label
        self.classification = self.marker["classification"]
        self.production_approved = self.classification == "PRODUCTION"
        self.model_versions = {
            "asr": self.marker["model_version"],
            "registry_snapshot": (
                "omniasr-nonprod:" + self.marker["manifest_sha256"]),
            "llm": None,
            "rag": None,
            "tts": None,
        }
        self.ready = True

    def transcribe(self, audio_path: Path, language_hint: str | None) -> Transcript:
        omni_lang = None
        if language_hint:
            omni_lang = self._language_ids.get(language_hint)
            if omni_lang is None:
                raise BackendRefusal(
                    f"{language_hint!r} is not served by this artifact "
                    f"({self.marker['model_version']})")
        languages = None if omni_lang is None else [omni_lang]
        # round 4 (Codex): report the REAL audio duration (billing and
        # latency accounting depend on it); the pipeline itself does not
        # expose one. av is already in the runtime image.
        duration_seconds = _audio_duration_seconds(audio_path)
        values = self._pipeline.transcribe(
            [str(audio_path)], lang=languages, batch_size=1)
        if len(values) != 1 or not isinstance(values[0], str):
            raise BackendRefusal(
                "OmniASR pipeline returned a malformed prediction")
        verbatim = values[0].strip()
        normalized = " ".join(unicodedata.normalize("NFC", verbatim).split())
        return Transcript(
            language=language_hint or "und",
            # CTC conditioning is deterministic on the supplied language;
            # the pipeline exposes no calibrated probability
            language_probability=1.0 if language_hint else 0.0,
            verbatim=verbatim,
            normalized=normalized,
            normalization_version=NORMALIZATION_VERSION,
            duration_seconds=duration_seconds,
        )


def _audio_duration_seconds(audio_path: Path) -> float:
    try:
        import av
        with av.open(str(audio_path)) as container:
            if container.duration:
                return round(container.duration / 1_000_000, 3)
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                return round(float(stream.duration * stream.time_base), 3)
    except Exception as exc:                                  # noqa: BLE001
        raise BackendRefusal(
            "audio duration could not be determined") from exc
    raise BackendRefusal("audio container reports no duration")
