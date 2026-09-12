"""The raw-versus-normalised diagnostic option in the protected evaluator.

Training feeds raw waveforms; evaluation and serving normalise each utterance.
The diagnostic scores identical recordings both ways. These tests pin that the
default stays the frozen contract path, that 'raw' must be explicit and
self-declared in receipts, and that raw receipts can never enter nomination.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.omniasr_score import (EvaluatorRefusal, INPUT_CONVENTIONS,  # noqa: E402
                                    resolve_input_convention)

KEY = "MEDZEN_SCORE_INPUT_CONVENTION"


def _source(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_default_is_the_frozen_normalised_path():
    assert resolve_input_convention({}) == "normalized"


def test_blank_value_is_the_default():
    assert resolve_input_convention({KEY: "  "}) == "normalized"


@pytest.mark.parametrize("value", ["raw", "RAW", " raw "])
def test_raw_must_be_explicit(value):
    assert resolve_input_convention({KEY: value}) == "raw"


@pytest.mark.parametrize("value", ["normalised", "none", "z-norm", "1"])
def test_unknown_values_refuse_rather_than_fall_back(value):
    with pytest.raises(EvaluatorRefusal, match=KEY):
        resolve_input_convention({KEY: value})


def test_only_two_conventions_exist():
    assert INPUT_CONVENTIONS == ("normalized", "raw")


def test_evaluator_still_imports_all_preprocessing_from_calibrate():
    src = _source("pipeline/omniasr_score.py")
    assert "from pipeline.omniasr_calibrate import (_ctc_greedy_text," in src
    assert "_preprocess_wave, _raw_wave)" in src
    assert "def _raw_wave" not in src and "layer_norm" not in src


def test_normalised_path_is_unchanged_and_raw_is_gated():
    src = _source("pipeline/omniasr_score.py")
    assert '(_raw_wave(audio, sr) if input_convention == "raw"' in src
    assert "else _preprocess_wave(audio, sr))" in src


def test_receipts_declare_their_convention():
    assert '"input_convention": input_convention,' in _source("pipeline/omniasr_score.py")


def test_calibrate_normalisation_is_untouched():
    src = _source("pipeline/omniasr_calibrate.py")
    assert "return functional.layer_norm(wave, wave.shape, eps=1e-5)" in src


def test_nomination_scorer_refuses_diagnostic_receipts(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    nom = pytest.importorskip("arm2_nomination_scorer")
    doc = {"job_name": "medzen-b5-b5-arm2-score-arm1-rawdiag-2026-001",
           "model_sha256": "c" * 64,
           "rows": [{"audio_checksum_sha256": "a" * 64, "hyp_normalized": "x"}],
           "model_artifact": {"s3_uri": "s3://b/k", "s3_version_id": "v"},
           "split_sha256": "d" * 64, "evaluator_image_digest": "sha256:" + "e" * 64,
           "input_convention": "raw"}
    path = tmp_path / "receipts.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(nom.ScorerRefusal, match="input_convention"):
        nom.load_receipts(path, arm="arm1", expected_model_sha="c" * 64,
                          evaluator={}, cfg={"packet": {}})


def test_raw_wave_is_exactly_the_training_waveform():
    torch = pytest.importorskip("torch")
    np = pytest.importorskip("numpy")
    from pipeline.omniasr_calibrate import _preprocess_wave, _raw_wave
    audio = (np.random.default_rng(0).standard_normal(16000) * 0.096).astype("float32")
    raw = _raw_wave(audio, 16000)
    assert raw.dtype == torch.float32
    assert torch.equal(raw, torch.from_numpy(audio))
    # torch.std is the sample estimator and numpy.std the population one, so compare
    # like with like: the exact-equality check above is the real proof of identity
    assert abs(float(torch.sqrt(((raw - raw.mean()) ** 2).mean())) - float(audio.std())) < 1e-6
    assert abs(float(_preprocess_wave(audio, 16000).std()) - 1.0) < 1e-2


def test_raw_wave_refuses_anything_but_16khz():
    pytest.importorskip("torch")
    from pipeline.omniasr_calibrate import _raw_wave
    from pipeline.omniasr_train import TrainerRefusal
    with pytest.raises(TrainerRefusal, match="16 kHz"):
        _raw_wave([0.0] * 100, 8000)
