"""Opt-in training augmentation (step 4 of the 2026-09-24 improvement plan).

MEDZEN_SPEED_PERTURB resamples each training clip by a factor chosen from a
hash of (seed, micro-batch, position); MEDZEN_TRAIN_MASKING attaches fairseq2's
StandardWav2Vec2Masker for training only (the 1b_v2 arch ships with masking off).
Unset, both must leave the trainer exactly as it was: same parsed fingerprint
(the golden captured from the unmodified trainer) and the same batch tensors.
Parse tests run everywhere; tensor tests need torch (+ torchaudio / fairseq2 for
the real resampler and masker) and run in the trainer image build.
"""
from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from pipeline.omniasr_train import (
    TrainerRefusal,
    check_masking_feasible,
    parse_config,
    wav2vec2_frames,
    parse_speed_perturb,
    parse_train_masking,
    run_fingerprint,
)

BASE_ENV = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "v9",
            "MEDZEN_LANGUAGES": "yemba", "MEDZEN_SEED": "7"}
PROV = {"manifest_version": "gb11", "eligible_rows": 82769}
GOLDEN_BASE_ENV = "ac20f2ce8334969c03cb8f831ac45fe355135f44bd8b19d506b3e5a743679cb2"
PROBE_ENV = {
    "MEDZEN_AUDIO_CAP_HOURS": "1", "MEDZEN_BATCH_SIZE": "2", "MEDZEN_CHECKPOINT_EVERY": "100",
    "MEDZEN_GRAD_ACCUM": "8", "MEDZEN_KD_ENABLE": "0", "MEDZEN_LANGUAGES": "medumba",
    "MEDZEN_LORA_ALPHA": "32", "MEDZEN_LORA_DROPOUT": "0.05", "MEDZEN_LORA_RANK": "16",
    "MEDZEN_LR": "1e-4", "MEDZEN_MANIFEST_VERSION": "byvgen1", "MEDZEN_MAX_STEPS": "3000",
    "MEDZEN_SEED": "20260904", "MEDZEN_STUDENT_INIT_MODE": "base", "MEDZEN_TEMPERATURE": "0",
    "MEDZEN_TRAIN_MODE": "lora", "MEDZEN_VARIANT": "ctc",
}
KEYS = {"speed_perturb", "train_masking"}
_needs_torch = pytest.mark.skipif(importlib.util.find_spec("torch") is None,
                                  reason="torch is a training-host dependency")


def _diff(a, b):
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


@pytest.mark.parametrize("extra", [{}, {"MEDZEN_SPEED_PERTURB": ""}, {"MEDZEN_SPEED_PERTURB": "1.0"},
                                   {"MEDZEN_SPEED_PERTURB": " 1 , 1.0 "}, {"MEDZEN_TRAIN_MASKING": " "}])
def test_default_spellings_keep_the_pre_knob_fingerprint(extra):
    config = parse_config(dict(BASE_ENV, **extra))
    assert config.speed_perturb == () and config.train_masking == ()
    assert not KEYS & set(config.fingerprint_payload())
    assert run_fingerprint(config, PROV) == GOLDEN_BASE_ENV


def test_speed_factors_are_canonical():
    assert parse_speed_perturb("1.1,0.9,1.0,0.9") == (0.9, 1.0, 1.1)
    assert parse_speed_perturb("0.95, 1.05") == (0.95, 1.05)


@pytest.mark.parametrize("value", ["0.5,1.0", "1.0,1.3", "fast", "0.9,,x", ",", "0", "0.9123,1.1", "0.901"])
def test_bad_speed_factors_fail_closed(value):
    with pytest.raises(TrainerRefusal, match="MEDZEN_SPEED_PERTURB"):
        parse_config(dict(BASE_ENV, MEDZEN_SPEED_PERTURB=value))


def test_masking_parses_to_masker_arguments():
    assert parse_train_masking("0.065,10,0.004,64") == (0.065, 10, 0.004, 64)
    assert parse_train_masking("0.065,10,0.0,64") == (0.065, 10, 0.0, 64)


@pytest.mark.parametrize("value", ["0.065,10,0.004", "0.065,10,0.004,64,1", "1.0,10,0,64",
                                   "0,10,0,64", "0.0,10,0.2,64", "0.1,0,0.1,64", "x,10,0.1,64",
                                   "-0.1,10,0.1,64"])
def test_bad_masking_fails_closed(value):
    with pytest.raises(TrainerRefusal, match="MEDZEN_TRAIN_MASKING"):
        parse_config(dict(BASE_ENV, MEDZEN_TRAIN_MASKING=value))


@pytest.mark.parametrize("knob,key", [({"MEDZEN_SPEED_PERTURB": "0.9,1.0,1.1"}, "speed_perturb"),
                                      ({"MEDZEN_TRAIN_MASKING": "0.65,10,0.25,64"}, "train_masking")])
def test_each_knob_binds_only_its_key(knob, key):
    on = parse_config(dict(PROBE_ENV, **knob))
    assert _diff(parse_config(PROBE_ENV).fingerprint_payload(), on.fingerprint_payload()) == {key}


@pytest.mark.parametrize("knob", [{"MEDZEN_SPEED_PERTURB": "0.9,1.1"},
                                  {"MEDZEN_TRAIN_MASKING": "0.65,10,0.25,64"}])
def test_augmentation_is_refused_with_kd(knob):
    with pytest.raises(TrainerRefusal, match="MEDZEN_KD_ENABLE"):
        parse_config(dict(PROBE_ENV, MEDZEN_KD_ENABLE="1", MEDZEN_KD_PRESERVATION_LANGUAGES="medumba", **knob))


def test_masking_is_refused_in_full_mode_but_speed_is_allowed():
    full = dict(BASE_ENV, MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5", MEDZEN_WARMUP_STEPS="1",
                MEDZEN_LR_SCHEDULE="constant")
    with pytest.raises(TrainerRefusal, match="MEDZEN_TRAIN_MASKING"):
        parse_config(dict(full, MEDZEN_TRAIN_MASKING="0.65,10,0.25,64"))
    assert parse_config(dict(full, MEDZEN_SPEED_PERTURB="0.9,1.1")).speed_perturb == (0.9, 1.1)


# ------------------------------------------------------------ masking feasibility (fairseq2 compute_row_mask)

def test_frames_follow_the_wav2vec2_front_end():
    assert wav2vec2_frames(16000) == 49 and wav2vec2_frames(399) == 0 and wav2vec2_frames(400) == 1


def test_fairseq2_style_masking_is_feasible_on_two_second_clips():
    out = check_masking_feasible((0.65, 10, 0.25, 64), 2.0, (), model_dim=1280)
    assert out["shortest_clip_frames"] == 99


@pytest.mark.parametrize("masking,min_s,speed,dim,axis", [
    ((0.065, 10, 0.0, 64), 2.0, (), 1280, "temporal"),       # wav2vec2-paper start prob on 2 s clips
    ((0.5, 10, 0.1, 64), 3.0, (), 1280, "spatial"),          # int(0.1/64*1279) = 1 < 2
    ((0.5, 10, 0.004, 64), 3.0, (), 1280, "spatial"),        # int(...) = 0
    ((0.5, 10, 0.3, 1280), 3.0, (), 1280, "spatial"),        # span not shorter than the width
    ((0.5, 500, 0.0, 64), 3.0, (), 1280, "temporal"),        # span longer than any clip
    ((0.2, 10, 0.0, 64), 2.1, (1.25,), 1280, "temporal"),    # feasible at 1.0, not at speed 1.25
])
def test_infeasible_masking_is_refused_before_training(masking, min_s, speed, dim, axis):
    with pytest.raises(TrainerRefusal, match=axis):
        check_masking_feasible(masking, min_s, speed, model_dim=dim)
    if speed:
        check_masking_feasible(masking, min_s, (), model_dim=dim)


def test_speed_factor_schedule_is_pinned_and_balanced():
    from pipeline.omniasr_data import speed_factor
    factors = (0.9, 1.0, 1.1)
    got = [speed_factor(20260904, i, p, factors) for i in range(8) for p in range(2)]
    assert got == [0.9, 1.0, 1.0, 1.1, 1.1, 1.0, 0.9, 0.9, 1.0, 1.1, 1.1, 0.9, 0.9, 1.1, 1.0, 0.9]
    draws = [speed_factor(20260904, i, p, factors) for i in range(24000) for p in range(2)]
    for f in factors:
        assert abs(draws.count(f) / len(draws) - 1 / 3) < 0.01


# ------------------------------------------------------------ batch source (torch)

def _batch_source(monkeypatch, tmp_path, speed, resample):
    import numpy as np
    import soundfile as sf
    from pipeline import omniasr_data

    fake_ta = types.ModuleType("torchaudio")
    fake_ta.functional = types.SimpleNamespace(resample=resample)
    monkeypatch.setitem(sys.modules, "torchaudio", fake_ta)
    class BatchLayout:  # stand-in everywhere: the test reads the lengths it was given
        def __init__(self, shape, seq_lens, device=None):
            self.shape, self.seq_lens = shape, seq_lens
    fake_nn = types.ModuleType("fairseq2.nn"); fake_nn.BatchLayout = BatchLayout
    monkeypatch.setitem(sys.modules, "fairseq2.nn", fake_nn)
    if "fairseq2" not in sys.modules:
        monkeypatch.setitem(sys.modules, "fairseq2", types.ModuleType("fairseq2"))
    rows = []
    for i in range(4):
        path = tmp_path / f"c{i}.wav"
        sf.write(path, (np.sin(np.arange(1600 * (i + 1)) / 7.0) * 0.1).astype("float32"), 16000)
        rows.append({"path": str(path), "text_normalized": "ab", "_lang": "medumba"})
    monkeypatch.setattr(omniasr_data, "fetch_audio", lambda cli, row, cache: row["path"])
    monkeypatch.setattr(omniasr_data, "authoritative_language", lambda row: "medumba")

    class Tok:
        def create_encoder(self):
            import torch
            return lambda text: torch.tensor([5, 6])
    config = types.SimpleNamespace(batch_size=2, seed=20260904, input_convention="raw", speed_perturb=speed)
    return omniasr_data.make_batch_source(rows, Tok(), config, None, tmp_path)


@_needs_torch
def test_default_batch_source_never_touches_the_resampler(monkeypatch, tmp_path, capsys):
    def boom(*a, **k):
        raise AssertionError("resampler called with speed perturbation off")
    batches = _batch_source(monkeypatch, tmp_path, (), boom)
    b = batches(0)
    assert b["seqs_layout"].seq_lens == [1600, 3200]
    assert "SPEED_PERTURB_APPLIED" not in capsys.readouterr().out


@_needs_torch
def test_speed_factor_choice_is_deterministic_and_changes_lengths(monkeypatch, tmp_path, capsys):
    import torch
    calls = []

    def resample(wave, orig_freq, new_freq):
        calls.append((orig_freq, new_freq))
        n = int(round(wave.shape[0] * new_freq / orig_freq))
        return torch.zeros(n, dtype=wave.dtype)
    batches = _batch_source(monkeypatch, tmp_path, (0.9, 1.0, 1.1), resample)
    first = [batches(i)["seqs_layout"].seq_lens for i in range(8)]
    again = [batches(i)["seqs_layout"].seq_lens for i in range(8)]
    assert first == again, "factor choice must not depend on global RNG or call order"
    assert "SPEED_PERTURB_APPLIED" in capsys.readouterr().out
    assert calls and all(new == 16000 and orig in (14400, 17600) for orig, new in calls)
    lengths = {n for lens in first for n in lens}
    assert lengths - {1600, 3200, 4800, 6400}, "some clip must have been resampled"


@_needs_torch
def test_speed_perturbation_runs_before_normalisation(monkeypatch, tmp_path):
    import torch
    from pipeline.omniasr_calibrate import _preprocess_wave
    from pipeline.omniasr_data import speed_factor

    def resample(wave, orig_freq, new_freq):  # deterministic stand-in: stretch by repetition
        n = int(round(wave.shape[0] * new_freq / orig_freq))
        return torch.linspace(-1, 1, n) * wave.abs().max() + wave.mean()
    source = _batch_source(monkeypatch, tmp_path, (0.9, 1.1), resample)
    import soundfile as sf
    rows = [str(tmp_path / f"c{i}.wav") for i in range(2)]
    import types as _t
    batches = source
    # rebuild with the normalized convention on the same rows
    from pipeline import omniasr_data
    cfg = _t.SimpleNamespace(batch_size=2, seed=20260904, input_convention="normalized", speed_perturb=(0.9, 1.1))

    class Tok:
        def create_encoder(self):
            return lambda text: torch.tensor([5, 6])
    mix = [{"path": r, "text_normalized": "ab", "_lang": "medumba"} for r in rows]
    b = omniasr_data.make_batch_source(mix, Tok(), cfg, None, tmp_path)(0)
    for pos, path in enumerate(rows):
        audio, sr = sf.read(path, dtype="float32")
        f = speed_factor(20260904, 0, pos, (0.9, 1.1))
        expected = _preprocess_wave(resample(torch.from_numpy(audio), int(round(sr * f)), sr).numpy(), sr)
        n = b["seqs_layout"].seq_lens[pos]
        assert n == expected.shape[0]
        assert torch.equal(b["seqs"][pos, :n], expected.to(torch.bfloat16))


@pytest.mark.skipif(importlib.util.find_spec("fairseq2") is None, reason="fairseq2 is a trainer-image dependency")
def test_real_masker_forward_runs_on_realistic_frames():
    import torch
    from fairseq2.nn import BatchLayout
    from pipeline.omniasr_train import attach_train_masker
    from torch import nn

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_module("masker", None)
            self.final_proj = nn.Linear(1280, 8)
    model = Model()
    masking = (0.65, 10, 0.25, 64)
    check_masking_feasible(masking, 2.0, (), model_dim=1280)
    attach_train_masker(model, masking, torch.device("cpu"))
    lens = [wav2vec2_frames(2.0 * 16000), wav2vec2_frames(8.0 * 16000)]
    seqs = torch.ones(2, max(lens), 1280, dtype=torch.bfloat16)
    masked, _ = model.masker(seqs.clone(), BatchLayout((2, max(lens)), seq_lens=lens))
    assert (masked == 0).any() and (masked == 1).any()


@pytest.mark.skipif(importlib.util.find_spec("torchaudio") is None, reason="torchaudio is a trainer-image dependency")
def test_real_resampler_scales_duration_by_one_over_factor():
    import torch
    import torchaudio
    wave = torch.randn(16000)
    slow = torchaudio.functional.resample(wave, orig_freq=14400, new_freq=16000).shape[0]
    fast = torchaudio.functional.resample(wave, orig_freq=17600, new_freq=16000).shape[0]
    assert abs(slow - 16000 / 0.9) <= 1 and abs(fast - 16000 / 1.1) <= 1


@pytest.mark.skipif(importlib.util.find_spec("fairseq2") is None, reason="fairseq2 is a trainer-image dependency")
def test_masker_attaches_zero_frozen_and_only_masks_in_training():
    import torch
    from torch import nn
    from pipeline.omniasr_train import attach_train_masker

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_module("masker", None)
            self.final_proj = nn.Linear(32, 8)
    model = Model()
    audit = attach_train_masker(model, (0.5, 2, 0.3, 4), torch.device("cpu"))
    assert audit["model_dim"] == 32 and audit["mask_embed"] == "zeros-frozen"
    assert not model.masker.temporal_mask_embed.requires_grad
    assert torch.count_nonzero(model.masker.temporal_mask_embed) == 0
    assert not [n for n, p in model.named_parameters() if p.requires_grad and n.startswith("masker")]
    with pytest.raises(TrainerRefusal, match="already carries a masker"):
        attach_train_masker(model, (0.5, 2, 0.3, 4), torch.device("cpu"))
    model.masker = None
    assert not [k for k in model.state_dict() if k.startswith("masker")]


@pytest.mark.skipif(importlib.util.find_spec("fairseq2") is None, reason="fairseq2 is a trainer-image dependency")
def test_main_with_masking_detaches_the_masker_before_export(monkeypatch, tmp_path):
    import contextlib
    import io
    import json
    import os
    import torch
    from torch import nn
    from pipeline import omniasr_data, omniasr_train

    class Tiny(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_module("masker", None)
            self.encoder = nn.ModuleDict({"layers": nn.ModuleList([nn.ModuleDict({"self_attn": nn.ModuleDict(
                {"q_proj": nn.Linear(16, 16), "v_proj": nn.Linear(16, 16)})}) for _ in range(2)])})
            self.frontend = nn.Linear(1, 16)
            self.final_proj = nn.Linear(16, 8)

        def forward(self, seqs, seqs_layout, targets=None, targets_layout=None):
            x = self.frontend(seqs.to(torch.bfloat16).unsqueeze(-1))
            for layer in self.encoder["layers"]:
                x = x + layer["self_attn"]["q_proj"](x) + layer["self_attn"]["v_proj"](x)
            logits = self.final_proj(x)
            return logits.float().log_softmax(-1).gather(-1, targets.unsqueeze(-1)).neg().sum()

    def batch_source(*_a, **_k):
        g = torch.Generator().manual_seed(123)
        data = [torch.randn(2, 6, generator=g) for _ in range(8)]
        tg = [torch.randint(0, 8, (2, 6), generator=g) for _ in range(8)]
        return lambda i: {"seqs": data[i % 8], "seqs_layout": None, "targets": tg[i % 8],
                          "targets_layout": None, "languages": ["medumba", "medumba"]}

    def load(_config):
        torch.manual_seed(999)
        return Tiny().to(torch.bfloat16), object(), torch.device("cpu")
    monkeypatch.setattr(omniasr_train, "build_gated_mix",
                        lambda config, client=None: ([{"language": "medumba", "duration_s": 2.0}], {"source": "fixture"}))
    monkeypatch.setattr(omniasr_train, "check_disk_envelope", lambda config, mix, cache_root=None: {"headroom": "ok"})
    monkeypatch.setattr(omniasr_train, "stage_model_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(omniasr_train, "s3", lambda: None)
    monkeypatch.setattr(omniasr_train, "_load_model_and_tokenizer", load)
    monkeypatch.setattr(omniasr_data, "make_batch_source", batch_source)
    for key in [k for k in os.environ if k.startswith("MEDZEN_")]:
        monkeypatch.delenv(key)
    env = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "byvgen1", "MEDZEN_LANGUAGES": "medumba",
           "MEDZEN_SEED": "20260904", "MEDZEN_TRAIN_MODE": "lora", "MEDZEN_LR": "1e-4", "MEDZEN_LORA_RANK": "4",
           "MEDZEN_LORA_ALPHA": "8", "MEDZEN_MAX_STEPS": "4", "MEDZEN_BATCH_SIZE": "2", "MEDZEN_GRAD_ACCUM": "2",
           "MEDZEN_CHECKPOINT_EVERY": "2", "MEDZEN_KD_ENABLE": "0", "MEDZEN_TRAIN_MASKING": "0.5,2,0.0,4",
           "MEDZEN_OUTPUT_DIR": str(tmp_path / "out"), "MEDZEN_CHECKPOINT_DIR": str(tmp_path / "ckpt"),
           "MEDZEN_AUDIO_CACHE": str(tmp_path / "cache")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert omniasr_train.main() == 0
    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.startswith("{")]
    assert [x for x in lines if x["status"] == "TRAIN_MASKING_FEASIBLE"]
    assert [x for x in lines if x["status"] == "TRAIN_MASKING_APPLIED"]
    manifest = json.loads((tmp_path / "out/export/manifest.json").read_text())
    assert manifest["training_run_identity"]["wrap_audit"]["train_masking"] == [0.5, 2, 0.0, 4]
    exported = torch.load(tmp_path / "out/export/model.pt", map_location="cpu")
    assert not [k for k in exported if k.startswith("masker")]
