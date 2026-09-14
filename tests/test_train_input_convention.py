"""Opt-in train input convention (CM4 normalized-input diagnostic, 2026-09-13).

The default 'raw' path must behave exactly as before this option existed: same
parsed config fingerprint (goldens captured from the unmodified trainer) and the
same batch tensors. 'normalized' must feed each clip exactly as the evaluator does.
Tensor tests need torch and soundfile and run in the trainer image build; the
config and source-contract tests run everywhere.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from pipeline.omniasr_train import TRAIN_INPUT_CONVENTIONS, TrainerRefusal, parse_config, run_fingerprint

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "v9", "MEDZEN_LANGUAGES": "yemba", "MEDZEN_SEED": "7"}
PROV = {"manifest_version": "gb11", "eligible_rows": 82769}
# run fingerprints captured from the trainer BEFORE this option was added
GOLDEN_FINGERPRINTS = {
 "base_env": "ac20f2ce8334969c03cb8f831ac45fe355135f44bd8b19d506b3e5a743679cb2",
 "control_arm1": "b015a2a52cfbaba55b1077d26763dad7b3649e41550e6e796f8e087d69940a31",
 "control_base": "932afac6c5ccaae7b066a46a0f02b4ca78418ea0ce157698825c5f9080105cf9"
}
_needs_audio = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None or importlib.util.find_spec("soundfile") is None,
    reason="torch and soundfile are training-host dependencies")


# environments of the committed CM-PILOT-ICMP15-{BASE,ARM1} packets, embedded so the test also runs in the trainer image build,
# whose test stage does not copy those packet files
CONTROL_ENVS = {
 "ARM1": {
  "MEDZEN_AUDIO_CAP_HOURS": "15",
  "MEDZEN_BATCH_SIZE": "2",
  "MEDZEN_CHECKPOINT_EVERY": "500",
  "MEDZEN_EXCLUSIONS_REF": "s3://medzen-speech/curated/_versions/gb3/DQ-2026-006-gb3-pulaar-question-mark-deferral.json",
  "MEDZEN_EXPECT_EXCLUDED": "1579",
  "MEDZEN_GRAD_ACCUM": "8",
  "MEDZEN_KD_ENABLE": "0",
  "MEDZEN_LANGUAGES": "bafia,basaa,english,ewe,ewondo,french,gbaya,kinyarwanda,lingala,medumba,ngiemboon,ngombala,pidgin,swahili,yangben",
  "MEDZEN_LORA_ALPHA": "32",
  "MEDZEN_LORA_DROPOUT": "0.05",
  "MEDZEN_LORA_RANK": "16",
  "MEDZEN_LR": "1e-4",
  "MEDZEN_MANIFEST_VERSION": "gb11",
  "MEDZEN_MAX_STEPS": "3000",
  "MEDZEN_SEED": "20260904",
  "MEDZEN_STUDENT_INIT_MODE": "arm1",
  "MEDZEN_STUDENT_INIT_S3_URI": "s3://medzen-speech/research/b5-training/b5-universal-arm1-2026-005/output/medzen-b5-b5-universal-arm1-2026-005/output/model.tar.gz",
  "MEDZEN_STUDENT_INIT_SHA256": "c6604a689688a5314b23d53c3d45362d2b8123e9c894568b9810de2d40f7490c",
  "MEDZEN_STUDENT_INIT_VERSION_ID": "QfK3zQ_p4Ls43cF1KmIWTzPja7vLW0P4",
  "MEDZEN_TEMPERATURE": "0",
  "MEDZEN_TRAIN_MODE": "lora",
  "MEDZEN_VARIANT": "ctc"
 },
 "BASE": {
  "MEDZEN_AUDIO_CAP_HOURS": "15",
  "MEDZEN_BATCH_SIZE": "2",
  "MEDZEN_CHECKPOINT_EVERY": "500",
  "MEDZEN_EXCLUSIONS_REF": "s3://medzen-speech/curated/_versions/gb3/DQ-2026-006-gb3-pulaar-question-mark-deferral.json",
  "MEDZEN_EXPECT_EXCLUDED": "1579",
  "MEDZEN_GRAD_ACCUM": "8",
  "MEDZEN_KD_ENABLE": "0",
  "MEDZEN_LANGUAGES": "bafia,basaa,english,ewe,ewondo,french,gbaya,kinyarwanda,lingala,medumba,ngiemboon,ngombala,pidgin,swahili,yangben",
  "MEDZEN_LORA_ALPHA": "32",
  "MEDZEN_LORA_DROPOUT": "0.05",
  "MEDZEN_LORA_RANK": "16",
  "MEDZEN_LR": "1e-4",
  "MEDZEN_MANIFEST_VERSION": "gb11",
  "MEDZEN_MAX_STEPS": "3000",
  "MEDZEN_SEED": "20260904",
  "MEDZEN_STUDENT_INIT_MODE": "base",
  "MEDZEN_TEMPERATURE": "0",
  "MEDZEN_TRAIN_MODE": "lora",
  "MEDZEN_VARIANT": "ctc"
 }
}


def _packet_env(arm):
    return dict(CONTROL_ENVS[arm])


def test_conventions_are_exactly_raw_and_normalized():
    assert TRAIN_INPUT_CONVENTIONS == ("raw", "normalized")


def test_default_is_raw_and_blank_means_raw():
    assert parse_config(BASE_ENV).input_convention == "raw"
    assert parse_config(dict(BASE_ENV, MEDZEN_TRAIN_INPUT_CONVENTION="")).input_convention == "raw"
    assert parse_config(dict(BASE_ENV, MEDZEN_TRAIN_INPUT_CONVENTION=" RAW ")).input_convention == "raw"
    assert parse_config(dict(BASE_ENV, MEDZEN_TRAIN_INPUT_CONVENTION="Normalized")).input_convention == "normalized"


@pytest.mark.parametrize("value", ["normalised", "layer_norm", "true", "1", "raw,normalized"])
def test_unknown_convention_fails_closed(value):
    with pytest.raises(TrainerRefusal, match="MEDZEN_TRAIN_INPUT_CONVENTION"):
        parse_config(dict(BASE_ENV, MEDZEN_TRAIN_INPUT_CONVENTION=value))


@pytest.mark.parametrize("name,env", [("base_env", BASE_ENV), ("control_base", "BASE"), ("control_arm1", "ARM1")])
def test_raw_fingerprint_is_byte_identical_to_the_pre_option_trainer(name, env):
    env = _packet_env(env) if isinstance(env, str) else env
    for variant in (env, dict(env, MEDZEN_TRAIN_INPUT_CONVENTION="raw")):
        config = parse_config(variant)
        assert "input_convention" not in config.fingerprint_payload()
        assert run_fingerprint(config, PROV) == GOLDEN_FINGERPRINTS[name]


def test_normalized_binds_the_convention_into_the_fingerprint():
    env = _packet_env("BASE")
    normalized = parse_config(dict(env, MEDZEN_TRAIN_INPUT_CONVENTION="normalized"))
    assert normalized.fingerprint_payload()["input_convention"] == "normalized"
    assert run_fingerprint(normalized, PROV) != GOLDEN_FINGERPRINTS["control_base"]
    raw_payload = parse_config(env).fingerprint_payload()
    diff = {k for k in set(raw_payload) | set(normalized.fingerprint_payload()) if raw_payload.get(k) != normalized.fingerprint_payload().get(k)}
    assert diff == {"input_convention"}


def test_evaluator_prepares_clips_the_way_the_normalized_path_does():
    score = (ROOT / "pipeline/omniasr_score.py").read_text()
    data = (ROOT / "pipeline/omniasr_data.py").read_text()
    for needle in ('dtype="float32", always_2d=False', "audio.mean(axis=1)", "_preprocess_wave(audio, sr)"):
        assert needle in score, needle
        assert needle in data, needle


# ---- tensor behaviour -----------------------------------------------------------------

class _Layout:
    def __init__(self, shape, seq_lens, device=None):
        self.shape, self.seq_lens, self.device = shape, list(seq_lens), device


@pytest.fixture
def batch_source(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    import torch
    from pipeline import omniasr_data
    if importlib.util.find_spec("fairseq2") is None:
        fake = types.ModuleType("fairseq2"); fake_nn = types.ModuleType("fairseq2.nn"); fake_nn.BatchLayout = _Layout; fake.nn = fake_nn
        monkeypatch.setitem(sys.modules, "fairseq2", fake); monkeypatch.setitem(sys.modules, "fairseq2.nn", fake_nn)
    rng = np.random.default_rng(3)
    clips = []
    for i, (n, amp, stereo) in enumerate(((16000, 0.02, False), (9000, 0.6, True), (12000, 0.1, False), (7000, 0.004, False))):
        data = (rng.standard_normal((n, 2) if stereo else n) * amp).astype("float32")
        path = tmp_path / f"c{i}.wav"; sf.write(path, data, 16000, subtype="FLOAT")
        clips.append({"path": str(path), "text_normalized": "ab c", "_lang": "bafia"})
    monkeypatch.setattr(omniasr_data, "fetch_audio", lambda cli, row, cache: Path(row["path"]))

    class _Tok:
        def create_encoder(self):
            return lambda text: torch.tensor([ord(c) % 31 for c in text], dtype=torch.int64)

    def build(convention):
        config = types.SimpleNamespace(batch_size=2, input_convention=convention)
        return omniasr_data.make_batch_source(clips, _Tok(), config, cli=None, cache=tmp_path)

    def read(row):
        audio, sr = sf.read(row["path"], dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, sr
    return build, clips, read


@_needs_audio
def test_raw_batches_are_unchanged(batch_source):
    import torch
    build, clips, read = batch_source
    batches = build("raw")
    for index in range(2):
        out = batches(index)
        assert out["seqs"].dtype == torch.bfloat16
        for pos in range(2):
            audio, _ = read(clips[index * 2 + pos])
            n = len(audio)
            assert torch.equal(out["seqs"][pos, :n], torch.from_numpy(audio).to(torch.bfloat16))
            assert torch.count_nonzero(out["seqs"][pos, n:]) == 0
            assert out["seqs_layout"].seq_lens[pos] == n


@_needs_audio
def test_missing_attribute_defaults_to_raw(batch_source, monkeypatch):
    import torch
    build, clips, read = batch_source
    raw = build("raw")(0)["seqs"]
    from pipeline import omniasr_data
    config = types.SimpleNamespace(batch_size=2)
    legacy = omniasr_data.make_batch_source(clips, type("T", (), {"create_encoder": lambda self: (lambda t: torch.tensor([1]))})(), config, cli=None, cache=Path("."))(0)["seqs"]
    assert torch.equal(raw, legacy)


@_needs_audio
def test_normalized_batches_match_the_evaluator_exactly(batch_source, capsys):
    import torch
    from pipeline.omniasr_calibrate import _preprocess_wave
    build, clips, read = batch_source
    batches = build("normalized")
    assert '"status": "TRAIN_INPUT_CONVENTION"' in capsys.readouterr().out
    for index in range(2):
        out = batches(index)
        for pos in range(2):
            audio, sr = read(clips[index * 2 + pos])
            n = len(audio)
            expected = _preprocess_wave(audio, sr).to(torch.bfloat16)
            assert torch.equal(out["seqs"][pos, :n], expected)
            assert torch.count_nonzero(out["seqs"][pos, n:]) == 0
            # layer_norm with eps 1e-5 (population variance): mean 0 and std sqrt(var / (var + eps)),
            # which is below 1 for very quiet clips, exactly as at evaluation and serving
            segment = out["seqs"][pos, :n].float()
            var = float(torch.from_numpy(audio).double().var(unbiased=False))
            assert abs(float(segment.mean())) < 1e-2
            assert abs(float(segment.double().std(unbiased=False)) - (var / (var + 1e-5)) ** 0.5) < 1e-2


@_needs_audio
def test_normalization_happens_before_padding(batch_source):
    import torch
    import torch.nn.functional as F
    build, clips, read = batch_source
    out = build("normalized")(0)
    short_audio, _ = read(clips[1])
    padded = torch.zeros(out["seqs"].shape[1]); padded[: len(short_audio)] = torch.from_numpy(short_audio)
    wrong = F.layer_norm(padded, padded.shape, eps=1e-5)[: len(short_audio)].to(torch.bfloat16)
    assert not torch.equal(out["seqs"][1, : len(short_audio)], wrong)


def test_normalized_input_refuses_knowledge_distillation():
    # KD sends the same batch to raw-trained teachers; this diagnostic option is for plain runs only
    kd_env = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "v9", "MEDZEN_SEED": "7",
              "MEDZEN_LANGUAGES": "english,french,swahili,lingala,pidgin,kinyarwanda,ewe", "MEDZEN_KD_ENABLE": "1"}
    assert parse_config(kd_env).kd_enable is True
    assert parse_config(dict(kd_env, MEDZEN_TRAIN_INPUT_CONVENTION="raw")).kd_enable is True
    with pytest.raises(TrainerRefusal, match="MEDZEN_TRAIN_INPUT_CONVENTION=normalized is refused with KD on"):
        parse_config(dict(kd_env, MEDZEN_TRAIN_INPUT_CONVENTION="normalized"))
