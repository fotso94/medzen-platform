"""Lazy model adapters; imports and model loads occur only after all gates pass."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .harness import EvaluationRefusal, validate_mode
from .identity import CANDIDATES


@dataclass(frozen=True)
class Transcript:
    text: str
    eos_observed: bool
    cap_hit: bool
    termination_evidence: str


class Backend(Protocol):
    def transcribe(self, audio: Path, language_id: str | None) -> Transcript: ...


# Whisper's decoder max_length (448) covers the prompt AND generated tokens.
# The forced decoder prompt is 3-4 tokens (<|sot|>, language, task, optional
# no-timestamps), so max_new_tokens must stay below 448 minus that headroom
# or faster-whisper raises ValueError on the first row (attempt-26 refusal).
WHISPER_MAX_NEW_TOKENS = 440


class WhisperBackend:
    def __init__(self, model_dir: Path) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(str(model_dir), device="cuda", compute_type="float16")

    def transcribe(self, audio: Path, language_id: str | None) -> Transcript:
        segments, _ = self._model.transcribe(
            str(audio),
            language=language_id,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            vad_filter=False,
            max_new_tokens=WHISPER_MAX_NEW_TOKENS,
        )
        materialized = list(segments)
        text = " ".join(segment.text.strip() for segment in materialized).strip()
        tokens = sum(len(segment.tokens) for segment in materialized)
        return Transcript(
            text=text,
            eos_observed=tokens < WHISPER_MAX_NEW_TOKENS,
            cap_hit=tokens >= WHISPER_MAX_NEW_TOKENS,
            termination_evidence=(
                "faster-whisper completed iterator; token count compared with "
                f"max_new_tokens={WHISPER_MAX_NEW_TOKENS} (448 minus prompt headroom)"
            ),
        )


class MetaBackend:
    def __init__(self, model_card: str) -> None:
        import torch
        from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline

        self._pipeline = ASRInferencePipeline(
            model_card=model_card,
            device="cuda",
            dtype=torch.bfloat16,
        )

    def transcribe(self, audio: Path, language_id: str | None) -> Transcript:
        languages = None if language_id is None else [language_id]
        values = self._pipeline.transcribe([str(audio)], lang=languages, batch_size=1)
        if len(values) != 1 or not isinstance(values[0], str):
            raise EvaluationRefusal("Meta backend returned a malformed prediction")
        text = values[0].strip()
        return Transcript(
            text=text,
            eos_observed=True,
            cap_hit=False,
            termination_evidence="Omnilingual synchronous API completed; backend exposes no truncation flag",
        )


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
