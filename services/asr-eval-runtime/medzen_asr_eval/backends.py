"""Lazy model adapters; imports and model loads occur only after all gates pass."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .harness import EvaluationRefusal, validate_mode
from .identity import CANDIDATES


class Backend(Protocol):
    def transcribe(self, audio: Path, language_id: str | None) -> str: ...


class WhisperBackend:
    def __init__(self, model_dir: Path) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(str(model_dir), device="cuda", compute_type="float16")

    def transcribe(self, audio: Path, language_id: str | None) -> str:
        segments, _ = self._model.transcribe(
            str(audio),
            language=language_id,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class MetaBackend:
    def __init__(self, model_card: str) -> None:
        import torch
        from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline

        self._pipeline = ASRInferencePipeline(
            model_card=model_card,
            device="cuda",
            dtype=torch.bfloat16,
        )

    def transcribe(self, audio: Path, language_id: str | None) -> str:
        languages = None if language_id is None else [language_id]
        values = self._pipeline.transcribe([str(audio)], lang=languages, batch_size=1)
        if len(values) != 1 or not isinstance(values[0], str):
            raise EvaluationRefusal("Meta backend returned a malformed prediction")
        return values[0].strip()


def load_backend(candidate_name: str, mode: str, language_id: str | None, model_root: Path) -> Backend:
    validate_mode(candidate_name, mode, language_id)
    candidate = CANDIDATES[candidate_name]
    if candidate.family == "whisper_ct2":
        return WhisperBackend(model_root / "whisper-large-v3-ct2")
    if candidate.family == "meta_ctc":
        return MetaBackend("medzen_omniASR_CTC_1B_v2")
    if candidate.family == "meta_llm":
        return MetaBackend("medzen_omniASR_LLM_1B_v2")
    raise EvaluationRefusal(f"unsupported candidate family: {candidate.family}")
