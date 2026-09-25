"""Opt-in LoRA adapter placement and precision (Medumba capacity follow-up, 2026-09-25).

MEDZEN_LORA_TARGETS picks which encoder projections get adapters and
MEDZEN_LORA_TRAINABLE_DTYPE=float32 keeps a full-precision master copy of the
adapters. Unset, both must leave the trainer exactly as it was: the same parsed
fingerprint (goldens captured from the unmodified trainer) and the same wrap call.

Why float32 exists: the probe checkpoints showed that a bfloat16 adapter cannot
absorb an Adam step smaller than half its own spacing, so about 60% of the A
matrix never moved between step 100 and step 2,400 (MEDZEN-BF16-UPDATE-LOSS-2026-001).
The torch tests below reproduce that rounding and show the float32 copy absorbs
the same step. Config and source-contract tests run everywhere; tensor tests need
torch and run in the trainer image build.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from pipeline.omniasr_train import (
    DEFAULT_LORA_CHECKPOINT_BYTES,
    LORA_DEFAULT_TARGETS,
    LORA_TARGET_ALLOWLIST,
    TrainerRefusal,
    lora_checkpoint_bytes,
    lora_targets_per_suffix,
    parse_config,
    parse_lora_targets,
    run_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "v9",
            "MEDZEN_LANGUAGES": "yemba", "MEDZEN_SEED": "7"}
PROV = {"manifest_version": "gb11", "eligible_rows": 82769}
# captured from the trainer BEFORE these knobs existed (same golden as
# tests/test_train_input_convention.py::GOLDEN_FINGERPRINTS["base_env"])
GOLDEN_BASE_ENV = "ac20f2ce8334969c03cb8f831ac45fe355135f44bd8b19d506b3e5a743679cb2"
# the committed Medumba capacity-probe packet environment (CM-BYVFIT-PROBE-...-001)
PROBE_ENV = {
    "MEDZEN_AUDIO_CAP_HOURS": "1", "MEDZEN_BATCH_SIZE": "2", "MEDZEN_CHECKPOINT_EVERY": "100",
    "MEDZEN_GRAD_ACCUM": "8", "MEDZEN_KD_ENABLE": "0", "MEDZEN_LANGUAGES": "medumba",
    "MEDZEN_LORA_ALPHA": "32", "MEDZEN_LORA_DROPOUT": "0.05", "MEDZEN_LORA_RANK": "16",
    "MEDZEN_LR": "1e-4", "MEDZEN_MANIFEST_VERSION": "byvfit1", "MEDZEN_MAX_STEPS": "1200",
    "MEDZEN_SEED": "20260904", "MEDZEN_STUDENT_INIT_MODE": "base", "MEDZEN_TEMPERATURE": "0",
    "MEDZEN_TRAIN_MODE": "lora", "MEDZEN_VARIANT": "ctc",
}
KNOB_KEYS = {"lora_target_suffixes", "lora_trainable_dtype"}
_needs_torch = pytest.mark.skipif(importlib.util.find_spec("torch") is None,
                                  reason="torch is a training-host dependency")


def _diff(a: dict, b: dict) -> set[str]:
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


# ---------------------------------------------------------------- defaults

@pytest.mark.parametrize("extra", [
    {},
    {"MEDZEN_LORA_TARGETS": ""},
    {"MEDZEN_LORA_TARGETS": "  "},
    {"MEDZEN_LORA_TRAINABLE_DTYPE": ""},
    {"MEDZEN_LORA_TARGETS": "self_attn.v_proj, self_attn.q_proj"},  # explicit default pair
])
def test_default_spellings_keep_the_pre_knob_fingerprint(extra):
    config = parse_config(dict(BASE_ENV, **extra))
    assert config.lora_target_suffixes == () and config.lora_trainable_dtype == ""
    assert not KNOB_KEYS & set(config.fingerprint_payload())
    assert run_fingerprint(config, PROV) == GOLDEN_BASE_ENV


def test_probe_packet_environment_parses_to_the_default_adapters():
    config = parse_config(PROBE_ENV)
    assert config.lora_target_suffixes == () and config.lora_trainable_dtype == ""
    assert not KNOB_KEYS & set(config.fingerprint_payload())


# ---------------------------------------------------------------- targets

def test_targets_are_canonical_and_deduplicated():
    assert parse_lora_targets("ffn.output_proj,self_attn.q_proj,self_attn.q_proj") == (
        "self_attn.q_proj", "ffn.output_proj")
    assert parse_lora_targets(",".join(reversed(LORA_TARGET_ALLOWLIST))) == LORA_TARGET_ALLOWLIST
    assert parse_lora_targets(" self_attn.q_proj , self_attn.v_proj ") == ()
    assert LORA_DEFAULT_TARGETS == ("self_attn.q_proj", "self_attn.v_proj")


@pytest.mark.parametrize("value", [
    ",", " , ", "output_proj", "q_proj", "final_proj", "encoder.layers.0.self_attn.q_proj",
    "self_attn.q_proj,final_proj", "SELF_ATTN.Q_PROJ",
])
def test_unknown_or_empty_targets_fail_closed(value):
    with pytest.raises(TrainerRefusal, match="MEDZEN_LORA_TARGETS"):
        parse_config(dict(BASE_ENV, MEDZEN_LORA_TARGETS=value))


def test_targets_bind_only_their_key_into_the_fingerprint():
    wide = parse_config(dict(PROBE_ENV, MEDZEN_LORA_TARGETS=",".join(LORA_TARGET_ALLOWLIST)))
    assert wide.fingerprint_payload()["lora_target_suffixes"] == list(LORA_TARGET_ALLOWLIST)
    assert _diff(parse_config(PROBE_ENV).fingerprint_payload(),
                 wide.fingerprint_payload()) == {"lora_target_suffixes"}


# ---------------------------------------------------------------- precision

@pytest.mark.parametrize("value", ["float32", "FLOAT32", " float32 "])
def test_float32_binds_only_its_key_into_the_fingerprint(value):
    fp32 = parse_config(dict(PROBE_ENV, MEDZEN_LORA_TRAINABLE_DTYPE=value))
    assert fp32.lora_trainable_dtype == "float32"
    assert _diff(parse_config(PROBE_ENV).fingerprint_payload(),
                 fp32.fingerprint_payload()) == {"lora_trainable_dtype"}
    assert run_fingerprint(fp32, PROV) != run_fingerprint(parse_config(PROBE_ENV), PROV)


@pytest.mark.parametrize("value", ["bfloat16", "fp32", "float16", "half", "torch.float32", "1"])
def test_unknown_trainable_dtype_fails_closed(value):
    with pytest.raises(TrainerRefusal, match="MEDZEN_LORA_TRAINABLE_DTYPE"):
        parse_config(dict(BASE_ENV, MEDZEN_LORA_TRAINABLE_DTYPE=value))


@pytest.mark.parametrize("knob", [{"MEDZEN_LORA_TRAINABLE_DTYPE": "float32"},
                                  {"MEDZEN_LORA_TARGETS": "ffn.inner_proj"}])
def test_adapter_knobs_are_refused_in_full_mode(knob):
    env = dict(BASE_ENV, MEDZEN_TRAIN_MODE="full", MEDZEN_LR="1e-5",
               MEDZEN_WARMUP_STEPS="1", MEDZEN_LR_SCHEDULE="constant", **knob)
    with pytest.raises(TrainerRefusal, match="refused with MEDZEN_TRAIN_MODE='full'"):
        parse_config(env)


@pytest.mark.parametrize("knob", [{"MEDZEN_LORA_TRAINABLE_DTYPE": "float32"},
                                  {"MEDZEN_LORA_TARGETS": "ffn.inner_proj"}])
def test_adapter_knobs_are_refused_with_kd_until_memory_is_measured(knob):
    with pytest.raises(TrainerRefusal, match="MEDZEN_KD_ENABLE"):
        parse_config(dict(PROBE_ENV, MEDZEN_KD_ENABLE="1", MEDZEN_KD_PRESERVATION_LANGUAGES="medumba",
                          **knob))


def test_the_kd_fixture_itself_parses_without_the_knobs():
    config = parse_config(dict(PROBE_ENV, MEDZEN_KD_ENABLE="1", MEDZEN_KD_PRESERVATION_LANGUAGES="medumba"))
    assert config.kd_enable and config.execution_mode == "arm2_comparative"


# ---------------------------------------------------------------- disk sizing

def test_default_runs_keep_the_historical_checkpoint_budget():
    assert lora_checkpoint_bytes(parse_config(PROBE_ENV)) == DEFAULT_LORA_CHECKPOINT_BYTES == 200_000_000


def test_knob_runs_are_sized_above_their_measured_checkpoints():
    wide = ",".join(LORA_TARGET_ALLOWLIST)
    # measured single-checkpoint sizes (review 2026-09-25): 213.0 MB and 425.3 MB
    r16 = parse_config(dict(PROBE_ENV, MEDZEN_LORA_TARGETS=wide, MEDZEN_LORA_TRAINABLE_DTYPE="float32"))
    r32 = parse_config(dict(PROBE_ENV, MEDZEN_LORA_TARGETS=wide, MEDZEN_LORA_TRAINABLE_DTYPE="float32",
                            MEDZEN_LORA_RANK="32"))
    assert lora_checkpoint_bytes(r16) >= 213_000_000 * 1.2
    assert lora_checkpoint_bytes(r32) >= 425_300_000 * 1.2
    # q/v float32 at rank 16 measures 47.4 MB: the historical floor still covers it
    assert lora_checkpoint_bytes(parse_config(dict(PROBE_ENV, MEDZEN_LORA_TRAINABLE_DTYPE="float32"))) == 200_000_000


# ---------------------------------------------------------------- placement audit

def _names(suffixes, layers=48):
    return [f"encoder.layers.{i}.{s}" for i in range(layers) for s in suffixes]


def test_per_suffix_audit_accepts_one_module_per_layer_per_suffix():
    assert lora_targets_per_suffix(_names(LORA_TARGET_ALLOWLIST), LORA_TARGET_ALLOWLIST) == {
        s: 48 for s in LORA_TARGET_ALLOWLIST}


@pytest.mark.parametrize("wrapped,suffixes", [
    (_names(("self_attn.q_proj",)), ("self_attn.q_proj", "ffn.inner_proj")),         # one suffix unmatched
    (_names(("self_attn.q_proj",)) + _names(("ffn.inner_proj",), 47),
     ("self_attn.q_proj", "ffn.inner_proj")),                                          # unequal counts
    (_names(("self_attn.q_proj", "self_attn.k_proj")), ("self_attn.q_proj",)),         # extra wraps
])
def test_per_suffix_audit_refuses_a_placement_the_model_did_not_honour(wrapped, suffixes):
    with pytest.raises(TrainerRefusal, match="LoRA"):
        lora_targets_per_suffix(wrapped, suffixes)


def test_default_wrap_call_is_unchanged_and_the_knobbed_path_prints_its_marker():
    src = (ROOT / "pipeline/omniasr_train.py").read_text()
    assert ("""    elif config.train_mode == "lora":
        wrap_audit = wrap_lora(
            model, rank=config.lora_rank, alpha=config.lora_alpha,
            dropout=config.lora_dropout, scope_prefix=CTC_SCOPE_PREFIX)
""") in src
    assert '"status": "LORA_ADAPTER_CONFIG_APPLIED"' in src


# ---------------------------------------------------------------- tensors (torch)

if importlib.util.find_spec("torch") is not None:
    import torch
    from torch import nn

    from pipeline.omniasr_lora import LoRALinear, lora_state_dict, merge_lora, wrap_lora

    class Encoderish(nn.Module):
        """Two layers with the wav2vec2 projection names under 'encoder.'."""

        def __init__(self, dim=32, inner=64, dtype=torch.bfloat16):
            super().__init__()
            def lin(i, o):
                return nn.Linear(i, o, dtype=dtype)
            self.encoder = nn.ModuleDict({"layers": nn.ModuleList([
                nn.ModuleDict({
                    "self_attn": nn.ModuleDict({n: lin(dim, dim) for n in
                                                ("q_proj", "k_proj", "v_proj", "output_proj")}),
                    "ffn": nn.ModuleDict({"inner_proj": lin(dim, inner), "output_proj": lin(inner, dim)}),
                }) for _ in range(2)])})
            self.final_proj = lin(dim, 8)

        def forward(self, x):
            for layer in self.encoder["layers"]:
                a = layer["self_attn"]
                x = x + a["output_proj"](a["q_proj"](x) + a["k_proj"](x) + a["v_proj"](x))
                x = x + layer["ffn"]["output_proj"](torch.relu(layer["ffn"]["inner_proj"](x)))
            return self.final_proj(x)


@_needs_torch
def test_bfloat16_adapter_loses_a_small_step_that_the_float32_copy_keeps():
    # the defect in miniature: 0.05 is in [2^-5, 2^-4), bf16 spacing there is
    # 2^-12, so a 1e-5 step (the probe's median Adam update is ~1.3e-5) vanishes
    for dtype, moves in ((torch.bfloat16, False), (torch.float32, True)):
        p = torch.full((64,), 0.05, dtype=dtype)
        before = p.clone()
        p.add_(torch.full_like(p, -1e-5))
        assert bool((p != before).any()) is moves, dtype


@_needs_torch
def test_float32_adapters_are_identity_at_init_and_keep_bf16_activations():
    torch.manual_seed(0)
    model = Encoderish()
    x = torch.randn(3, 32, dtype=torch.bfloat16)
    before = model(x).detach().clone()
    audit = wrap_lora(model, rank=4, alpha=8.0, scope_prefix="encoder.",
                      target_suffixes=LORA_TARGET_ALLOWLIST, trainable_dtype=torch.float32)
    after = model(x)
    assert after.dtype == torch.bfloat16
    assert torch.equal(before, after), "B=0 init must leave outputs byte-identical"
    assert len(audit["wrapped_modules"]) == 12
    assert audit["trainable_dtypes"] == ["float32"]
    assert lora_targets_per_suffix(audit["wrapped_modules"], LORA_TARGET_ALLOWLIST) == {
        s: 2 for s in LORA_TARGET_ALLOWLIST}
    trainable = {n: p for n, p in model.named_parameters() if p.requires_grad}
    assert trainable and all(p.dtype == torch.float32 for p in trainable.values())
    assert all(".lora_a" in n or ".lora_b" in n for n in trainable)
    assert not model.final_proj.weight.requires_grad
    assert all(t.dtype == torch.float32 for t in lora_state_dict(model).values())


@_needs_torch
def test_gradients_reach_the_float32_copy_and_adam_moves_every_entry():
    torch.manual_seed(1)
    model = Encoderish()
    wrap_lora(model, rank=4, alpha=8.0, scope_prefix="encoder.",
              target_suffixes=("self_attn.q_proj", "self_attn.v_proj"), trainable_dtype=torch.float32)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=1e-5)
    snapshot = [p.detach().clone() for p in params]
    x = torch.randn(5, 32, dtype=torch.bfloat16)
    for _ in range(3):  # B leaves zero after step 1; A gets gradient from step 2
        opt.zero_grad()
        model(x).float().pow(2).mean().backward()
        assert all(p.grad is not None and p.grad.dtype == torch.float32 for p in params)
        opt.step()
    a_moved = [bool((p != s).all()) for p, s, (n, _) in zip(
        params, snapshot, [(n, p) for n, p in model.named_parameters() if p.requires_grad])
        if n.endswith("lora_a")]
    assert a_moved and all(a_moved), "every A entry must move under float32 masters"


@_needs_torch
def test_bfloat16_default_adapters_freeze_large_a_entries_under_the_same_step():
    torch.manual_seed(1)
    model = Encoderish()
    wrap_lora(model, rank=4, alpha=8.0, scope_prefix="encoder.")  # default: bf16, q/v
    named = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    assert all(p.dtype == torch.bfloat16 for _, p in named)
    opt = torch.optim.AdamW([p for _, p in named], lr=1e-5)
    snapshot = {n: p.detach().clone() for n, p in named}
    x = torch.randn(5, 32, dtype=torch.bfloat16)
    for _ in range(3):
        opt.zero_grad()
        model(x).float().pow(2).mean().backward()
        opt.step()
    big = [(p, snapshot[n]) for n, p in named if n.endswith("lora_a")]
    assert all(bool((s.abs() >= 2 ** -5).any()) for _, s in big), "fixture needs large entries"
    assert all(bool((p == s)[s.abs() >= 2 ** -5].all()) for p, s in big), \
        "bf16 A entries >= 2^-5 cannot absorb a 1e-5 step"
    # and the steps WERE applied: entries small enough to resolve them moved,
    # so the frozen large entries are rounding, not an idle optimizer
    small_moved = sum(int((p != s)[s.abs() < 2 ** -8].sum()) for p, s in big)
    assert small_moved > 0, "no small A entry moved: the optimizer did not step"


@_needs_torch
def test_float32_merge_rounds_once_into_the_bf16_weight():
    torch.manual_seed(2)
    wrapped = nn.Linear(16, 16, dtype=torch.bfloat16)
    module = LoRALinear(wrapped, rank=4, alpha=8.0, trainable_dtype=torch.float32)
    with torch.no_grad():
        module.lora_b.normal_(std=1e-3)
    expected = (wrapped.weight.float() + (module.lora_b @ module.lora_a) * module.scaling
                ).to(torch.bfloat16)
    model = nn.Module()
    model.proj = module
    audit = merge_lora(model)
    assert audit["merged_modules"] == ["proj"]
    assert isinstance(model.proj, nn.Linear) and model.proj.weight.dtype == torch.bfloat16
    assert torch.equal(model.proj.weight, expected)


@_needs_torch
def test_default_forward_path_is_byte_identical_to_the_pre_knob_formula():
    torch.manual_seed(3)
    wrapped = nn.Linear(16, 16, dtype=torch.bfloat16)
    module = LoRALinear(wrapped, rank=4, alpha=8.0, dropout=0.0)
    with torch.no_grad():
        module.lora_b.normal_(std=1e-2)
    x = torch.randn(4, 16, dtype=torch.bfloat16)
    legacy = wrapped(x) + (x @ module.lora_a.transpose(0, 1) @ module.lora_b.transpose(0, 1)) * module.scaling
    assert module.lora_a.dtype == torch.bfloat16
    assert torch.equal(module(x), legacy)


# ---------------------------------------------------------------- main() end to end (torch)

if importlib.util.find_spec("torch") is not None:
    import contextlib
    import io
    import json
    import os

    class _Attn(nn.Module):
        def __init__(self, d):
            super().__init__()
            self.q_proj, self.k_proj, self.v_proj, self.output_proj = (
                nn.Linear(d, d), nn.Linear(d, d), nn.Linear(d, d), nn.Linear(d, d))

        def forward(self, x):
            return self.output_proj(torch.tanh(self.q_proj(x)) * self.k_proj(x) + self.v_proj(x))

    class _FFN(nn.Module):
        def __init__(self, d, i):
            super().__init__()
            self.inner_proj, self.output_proj = nn.Linear(d, i), nn.Linear(i, d)

        def forward(self, x):
            return self.output_proj(torch.relu(self.inner_proj(x)))

    class _Layer(nn.Module):
        def __init__(self, d, i):
            super().__init__()
            self.self_attn, self.ffn = _Attn(d), _FFN(d, i)

        def forward(self, x):
            x = x + self.self_attn(x)
            return x + self.ffn(x)

    class _Enc(nn.Module):
        def __init__(self, d, i, n):
            super().__init__()
            self.layers = nn.ModuleList([_Layer(d, i) for _ in range(n)])

        def forward(self, x):
            for layer in self.layers:
                x = layer(x)
            return x

    class TinyW2V(nn.Module):
        """wav2vec2-shaped names: encoder_frontend (outside 'encoder.'), encoder.layers.N, final_proj."""

        def __init__(self, d=16, i=32, n=3, vocab=8):
            super().__init__()
            self.encoder_frontend = nn.Linear(1, d)
            self.encoder = _Enc(d, i, n)
            self.final_proj = nn.Linear(d, vocab)

        def forward(self, seqs, seqs_layout, targets=None, targets_layout=None):
            x = self.encoder_frontend(seqs.to(torch.bfloat16).unsqueeze(-1))
            logits = self.final_proj(self.encoder(x))
            return logits.float().log_softmax(-1).gather(-1, targets.unsqueeze(-1)).neg().sum()


def _run_main(monkeypatch, tmp_path, **knobs):
    from pipeline import omniasr_data, omniasr_train

    def batch_source(*_a, **_k):
        g = torch.Generator().manual_seed(123)
        data = [torch.randn(2, 6, generator=g) for _ in range(16)]
        tg = [torch.randint(0, 8, (2, 6), generator=g) for _ in range(16)]
        return lambda index: {"seqs": data[index % 16], "seqs_layout": None, "targets": tg[index % 16],
                              "targets_layout": None, "languages": ["medumba", "medumba"]}

    def load(_config):
        torch.manual_seed(999)
        return TinyW2V().to(torch.bfloat16), object(), "cpu"

    monkeypatch.setattr(omniasr_train, "build_gated_mix",
                        lambda config, client=None: ([{"language": "medumba"}], {"source": "fixture"}))
    monkeypatch.setattr(omniasr_train, "check_disk_envelope", lambda config, mix, cache_root=None: {"headroom": "ok"})
    monkeypatch.setattr(omniasr_train, "stage_model_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(omniasr_train, "s3", lambda: None)
    monkeypatch.setattr(omniasr_train, "_load_model_and_tokenizer", load)
    monkeypatch.setattr(omniasr_data, "make_batch_source", batch_source)
    env = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "byvfit1", "MEDZEN_LANGUAGES": "medumba",
           "MEDZEN_SEED": "20260904", "MEDZEN_TRAIN_MODE": "lora", "MEDZEN_LR": "1e-4",
           "MEDZEN_LORA_RANK": "4", "MEDZEN_LORA_ALPHA": "8", "MEDZEN_LORA_DROPOUT": "0.05",
           "MEDZEN_MAX_STEPS": "4", "MEDZEN_BATCH_SIZE": "2", "MEDZEN_GRAD_ACCUM": "2",
           "MEDZEN_CHECKPOINT_EVERY": "2", "MEDZEN_KD_ENABLE": "0",
           "MEDZEN_OUTPUT_DIR": str(tmp_path / "out"), "MEDZEN_CHECKPOINT_DIR": str(tmp_path / "ckpt"),
           "MEDZEN_AUDIO_CACHE": str(tmp_path / "cache"), **knobs}
    for key in [k for k in os.environ if k.startswith("MEDZEN_")]:
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = omniasr_train.main()
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
    manifest = json.loads((tmp_path / "out/export/manifest.json").read_text())
    return rc, lines, manifest


@_needs_torch
def test_main_with_knobs_prints_what_it_built_and_records_it_in_the_export(monkeypatch, tmp_path):
    rc, lines, manifest = _run_main(monkeypatch, tmp_path, MEDZEN_LORA_TRAINABLE_DTYPE="float32",
                                    MEDZEN_LORA_TARGETS=",".join(LORA_TARGET_ALLOWLIST))
    assert rc == 0
    markers = [line for line in lines if line["status"] == "LORA_ADAPTER_CONFIG_APPLIED"]
    assert len(markers) == 1
    marker = markers[0]
    assert marker["trainable_dtypes"] == ["float32"]
    assert marker["per_suffix_modules"] == {s: 3 for s in LORA_TARGET_ALLOWLIST}
    audit = manifest["training_run_identity"]["wrap_audit"]
    assert audit["trainable_dtypes"] == ["float32"]
    assert audit["target_suffixes"] == list(LORA_TARGET_ALLOWLIST)
    assert audit["wrapped_module_count"] == 18
    assert audit["trainable_parameters"] == marker["trainable_parameters"]


@_needs_torch
def test_main_without_knobs_prints_no_marker_and_keeps_the_manifest_shape(monkeypatch, tmp_path):
    rc, lines, manifest = _run_main(monkeypatch, tmp_path)
    assert rc == 0
    assert not [line for line in lines if line["status"] == "LORA_ADAPTER_CONFIG_APPLIED"]
    assert set(manifest["training_run_identity"]["wrap_audit"]) == {"rank", "alpha", "trainable_parameters"}
