from __future__ import annotations

import math
import sys
from array import array
from dataclasses import dataclass
from typing import Protocol, Sequence


SAMPLE_RATE_HZ = 16_000


class VADRefusal(RuntimeError):
    """A PCM frame or VAD result is malformed."""


@dataclass(frozen=True)
class VADResult:
    is_speech: bool
    probability: float


class VoiceActivityDetector(Protocol):
    name: str

    def detect(self, pcm_s16le: bytes) -> VADResult: ...


def normalized_samples(pcm_s16le: bytes) -> tuple[float, ...]:
    if not pcm_s16le or len(pcm_s16le) % 2:
        raise VADRefusal("PCM frame must contain complete signed 16-bit samples")
    samples = array("h")
    samples.frombytes(pcm_s16le)
    if sys.byteorder != "little":
        samples.byteswap()
    return tuple(sample / 32768.0 for sample in samples)


class LocalEnergyVAD:
    """Deterministic fixture detector; it is not represented as Silero."""

    name = "local_energy_fixture_v1"

    def __init__(self, threshold: float = 0.01):
        if not 0 < threshold < 1:
            raise ValueError("VAD threshold must be between zero and one")
        self.threshold = threshold

    def detect(self, pcm_s16le: bytes) -> VADResult:
        samples = normalized_samples(pcm_s16le)
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        probability = min(1.0, rms / self.threshold)
        return VADResult(is_speech=rms >= self.threshold, probability=probability)


class SileroRunner(Protocol):
    def __call__(self, samples: Sequence[float], sample_rate_hz: int) -> float: ...


class SileroVADAdapter:
    """Dependency-injected Silero boundary with no download or torch import."""

    name = "silero_injected_adapter_v1"

    def __init__(self, runner: SileroRunner, threshold: float = 0.5):
        if not 0 < threshold < 1:
            raise ValueError("Silero threshold must be between zero and one")
        self.runner = runner
        self.threshold = threshold

    def detect(self, pcm_s16le: bytes) -> VADResult:
        samples = normalized_samples(pcm_s16le)
        probability = self.runner(samples, SAMPLE_RATE_HZ)
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
        ):
            raise VADRefusal("Silero runner returned an invalid probability")
        value = float(probability)
        return VADResult(is_speech=value >= self.threshold, probability=value)
