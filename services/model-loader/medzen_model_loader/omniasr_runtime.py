"""B6v2 OmniASR serving runtime (Codex serving review: a loader that
does not load).

loader_v2 verifies WHICH artifact may serve; this module is the runtime
that actually loads the fairseq2 OmniASR checkpoint and transcribes. It
is a thin, testable seam over the trainer's own inference stack: the
heavy fairseq2 import is lazy so the module and its contract tests run
without the GPU image, and the runtime REFUSES to answer until a
verified artifact is loaded.

Wiring: model-loader/Dockerfile installs fairseq2 (dark until b6v2
serving is activated); the ASR runtime service constructs
OmniASRRuntime(manifest, artifact_path) at startup via loader_v2 and
routes /internal/v1/transcriptions to .transcribe().
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .loader_v2 import load_artifact_v2


class RuntimeNotReady(RuntimeError):
    pass


class OmniASRRuntime:
    def __init__(self, manifest: Mapping[str, Any], artifact_path: Path,
                 *, loader=None):
        # digest-verify + identity-check BEFORE anything deserializes
        self.identity = (loader or load_artifact_v2)(manifest, artifact_path)
        self.artifact_path = Path(artifact_path)
        self.model_version = self.identity["version"]
        self.languages = self.identity["languages"]
        self._model = None          # lazily materialized

    def _ensure_model(self):
        if self._model is not None:
            return
        # LAZY heavy import — keeps contract tests GPU-free
        import torch  # noqa: F401
        from fairseq2.models.hub import load_model  # type: ignore
        state = torch.load(self.artifact_path, map_location="cpu",
                           weights_only=False)
        model = load_model("medzen_omniASR_CTC_1B_v2", device="cpu",
                           dtype=torch.float32)
        model.load_state_dict(state, strict=False)
        model.eval()
        self._model = model

    def transcribe(self, audio: bytes, *, language: str) -> dict[str, Any]:
        if language not in self.languages:
            raise RuntimeNotReady(
                f"{language!r} is not served by this artifact "
                f"({self.model_version})")
        self._ensure_model()
        # the trainer's decode path produces text; kept behind the lazy
        # import so this contract module needs no GPU. The real decode is
        # exercised in the trainer image's in-container tests.
        from pipeline.omniasr_infer import decode_ctc  # type: ignore
        text = decode_ctc(self._model, audio)
        return {"text": text, "model_version": self.model_version,
                "language": language}
