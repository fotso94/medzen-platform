"""Arm-2 preservation-aware distillation tests (Codex reviews #14-#17).

Host-safe majority (pure numerics reference, alignment, masking, freeze
audit, KD config refusals, fingerprint binding) runs on the engineering
host. The differentiable kd_loss / teacher-freeze-after-step / non-finite
loop tests carry the torch skip-marker and execute inside the trainer image
(work item C3) — they are authored here so the in-image pass gates the
Arm-2 image digest before it is pinned into the calibration packet.
"""
from __future__ import annotations

import importlib.util
import math

import pytest

from pipeline.omniasr_distill import (
    DistillationRefusal,
    assert_kd_alignment,
    kd_divergence_reference,
    make_kd_stub_teacher,
    preservation_mask,
    teacher_freeze_audit,
)
from pipeline.omniasr_train import parse_config, run_fingerprint

_needs_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="torch is a training/runtime-host dependency, absent on the engineering host",
)

KD_ENV = {
    "MEDZEN_VARIANT": "ctc",
    "MEDZEN_MANIFEST_VERSION": "v9",
    "MEDZEN_LANGUAGES": "english,french,swahili,lingala,pidgin,kinyarwanda,ewe",
    "MEDZEN_SEED": "7",
}


def _kd_config(**over):
    env = dict(KD_ENV)
    env.update(over)
    return parse_config(env)


# --------------------------------------------------------------------------
# (1) deterministic loss — the torch-free reference numerics
# --------------------------------------------------------------------------

def test_kd_reference_is_deterministic_and_temperature_scaled():
    student = [[2.0, 0.0, -1.0], [0.5, 0.5, 0.5]]
    teacher = [[0.0, 0.0, 3.0], [1.0, 0.0, -1.0]]
    a = kd_divergence_reference(student, teacher, temperature=2.0)
    b = kd_divergence_reference(student, teacher, temperature=2.0)
    assert a == b and a > 0.0                      # deterministic, positive KL
    # identical distributions -> zero divergence
    assert kd_divergence_reference(teacher, teacher, temperature=1.5) == 0.0
    # T^2 scaling: at higher T the softened KL*T^2 differs (not identical)
    assert kd_divergence_reference(student, teacher, temperature=4.0) != a
    # padded frames excluded: valid_length=1 counts ONLY the first frame
    one = kd_divergence_reference(student, teacher, temperature=2.0, valid_length=1)
    two = kd_divergence_reference(student, teacher, temperature=2.0, valid_length=2)
    assert one != two


def test_kd_reference_refuses_bad_temperature_and_shape():
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(DistillationRefusal, match="temperature"):
            kd_divergence_reference([[1.0, 2.0]], [[1.0, 2.0]], temperature=bad)
    with pytest.raises(DistillationRefusal, match="vocab"):
        kd_divergence_reference([[1.0, 2.0]], [[1.0, 2.0, 3.0]], temperature=1.0)
    with pytest.raises(DistillationRefusal, match="frame"):
        kd_divergence_reference([[1.0, 2.0]], [[1.0, 2.0], [3.0, 4.0]], temperature=1.0)


# --------------------------------------------------------------------------
# (2) teacher freezing — pure audit, torch-free stub
# --------------------------------------------------------------------------

def test_teacher_freeze_audit_passes_frozen_and_refuses_trainable():
    frozen = make_kd_stub_teacher(n_params=5, requires_grad=False)
    audit = teacher_freeze_audit(frozen)
    assert audit == {"parameters": 5, "trainable": 0, "frozen": True}
    thawed = make_kd_stub_teacher(n_params=5, requires_grad=False)
    thawed.parameters()[2].requires_grad = True     # one param slips trainable
    with pytest.raises(DistillationRefusal, match="[Nn][Oo][Tt] frozen"):
        teacher_freeze_audit(thawed)


# --------------------------------------------------------------------------
# (3) alignment — mismatch refuses before any GPU-hours
# --------------------------------------------------------------------------

def test_kd_alignment_matches_and_refuses_mismatch():
    assert_kd_alignment((3, 40, 200), (3, 40, 200))          # identical -> ok
    with pytest.raises(DistillationRefusal, match="alignment mismatch"):
        assert_kd_alignment((3, 40, 200), (3, 41, 200))      # frame count
    with pytest.raises(DistillationRefusal, match="alignment mismatch"):
        assert_kd_alignment((3, 40, 200), (3, 40, 201))      # vocab size
    with pytest.raises(DistillationRefusal, match="degenerate"):
        assert_kd_alignment((40,), (40,))                    # not (…, frames, vocab)


# --------------------------------------------------------------------------
# (4) preservation mask + KD config refusals (fail-closed)
# --------------------------------------------------------------------------

def test_preservation_mask_weights_and_strict_authoritative_tags():
    tags = ["English", "pidgin", "lingala", "kinyarwanda", "swahili"]
    pres = ["english", "french", "swahili", "lingala"]
    assert preservation_mask(tags, pres) == [1.0, 0.0, 1.0, 0.0, 1.0]
    # per-language weights (Codex #18: heavier sentinels, lighter anchors)
    weighted = preservation_mask(tags, pres,
                                 weights={"lingala": 2.0, "english": 0.5})
    assert weighted == [0.5, 0.0, 2.0, 0.0, 1.0]
    # strict: a missing/empty language tag REFUSES rather than dropping KD
    with pytest.raises(DistillationRefusal, match="authoritative language"):
        preservation_mask(["english", ""], pres, strict=True)


def test_kd_is_off_by_default_and_leaves_config_neutral():
    config = _kd_config()
    assert config.kd_enable is False
    assert config.kd_alpha == 0.0 and config.kd_preservation_languages == ()


def test_kd_enable_validates_alpha_temperature_and_languages():
    good = _kd_config(MEDZEN_KD_ENABLE="1")
    assert good.kd_enable and good.kd_teacher_mode == "base"
    assert set(good.kd_preservation_languages) == {"english", "french", "swahili", "lingala"}
    for bad_alpha in ("1.5", "0"):        # >1 or the silent-noop 0 both refuse
        with pytest.raises(Exception, match="ALPHA"):
            _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_ALPHA=bad_alpha)
    with pytest.raises(Exception, match="TEMPERATURE|finite"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_TEMPERATURE="0")
    with pytest.raises(Exception, match="not in the training"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_PRESERVATION_LANGUAGES="klingon")


def test_kd_enable_is_a_strict_boolean():
    assert _kd_config(MEDZEN_KD_ENABLE="TRUE").kd_enable is True   # case-insensitive
    assert _kd_config(MEDZEN_KD_ENABLE="off").kd_enable is False
    with pytest.raises(Exception, match="boolean"):
        _kd_config(MEDZEN_KD_ENABLE="maybe")


def test_kd_teacher_must_equal_student_and_pinned_card():
    with pytest.raises(Exception, match="pinned|alignment"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_TEACHER_CARD="some_other_card")


def test_kd_per_language_weights_parse_and_validate():
    cfg = _kd_config(MEDZEN_KD_ENABLE="1",
                     MEDZEN_KD_LANGUAGE_WEIGHTS="lingala=2.0,english=0.5")
    weights = dict(cfg.kd_language_weights)
    assert weights["lingala"] == 2.0 and weights["english"] == 0.5
    assert weights["french"] == 1.0                      # default uniform
    with pytest.raises(Exception, match="not a preservation language"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_LANGUAGE_WEIGHTS="pidgin=2.0")
    with pytest.raises(Exception, match="must be finite"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_LANGUAGE_WEIGHTS="lingala=0")


def test_kinyarwanda_v1_teacher_is_not_yet_wired():
    with pytest.raises(Exception, match="not wired|base-teacher-only"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_TEACHER_MODE="kw_v1")


# --------------------------------------------------------------------------
# (5) resume — KD knobs bind the run fingerprint (fail-closed on mismatch)
# --------------------------------------------------------------------------

def test_kd_knobs_bind_the_run_fingerprint():
    prov = {"mix": "x"}
    base = run_fingerprint(_kd_config(), prov)
    kd = run_fingerprint(_kd_config(MEDZEN_KD_ENABLE="1"), prov)
    assert base != kd, "a KD run must not share a fingerprint with a non-KD run"
    # alpha/temperature/preservation-set each move the fingerprint
    a2 = run_fingerprint(_kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_ALPHA="0.3"), prov)
    t2 = run_fingerprint(_kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_TEMPERATURE="2.0"), prov)
    p2 = run_fingerprint(
        _kd_config(MEDZEN_KD_ENABLE="1",
                   MEDZEN_KD_PRESERVATION_LANGUAGES="english,lingala"), prov)
    assert len({kd, a2, t2, p2}) == 4


def test_make_batch_loss_is_plain_when_kd_disabled():
    from pipeline.omniasr_train import _batch_loss, make_batch_loss
    assert make_batch_loss(_kd_config(), teacher=None) is _batch_loss


# --------------------------------------------------------------------------
# in-image (torch) — differentiable KD, freeze-after-step, non-finite loop
# --------------------------------------------------------------------------

@_needs_torch
def test_kd_loss_matches_reference_and_is_differentiable():
    import torch

    from pipeline.omniasr_distill import kd_loss

    torch.manual_seed(0)
    student = torch.randn(1, 4, 6, requires_grad=True)   # [rows, frames, vocab]
    teacher = torch.randn(1, 4, 6)
    kd = kd_loss(student, teacher, temperature=2.0,
                 row_weights=[1.0], valid_lengths=[4])
    assert torch.isfinite(kd) and kd.item() >= 0.0
    kd.backward()
    assert student.grad is not None and torch.isfinite(student.grad).all()
    # matches the torch-free reference (MEAN over the 4 valid frames) — this
    # is the assertion that caught the round-17 4x-too-large reduction
    ref = kd_divergence_reference(student[0].tolist(), teacher[0].tolist(),
                                  temperature=2.0, valid_length=4)
    assert math.isclose(kd.item(), ref, rel_tol=1e-4, abs_tol=1e-5)


@_needs_torch
def test_kd_excludes_padded_frames():
    import torch

    from pipeline.omniasr_distill import kd_loss

    torch.manual_seed(1)
    student = torch.randn(1, 5, 6)
    teacher = torch.randn(1, 5, 6)
    # only the first 3 frames are valid -> must equal the reference trimmed to 3
    kd = kd_loss(student, teacher, temperature=1.0,
                 row_weights=[1.0], valid_lengths=[3])
    ref = kd_divergence_reference(student[0].tolist(), teacher[0].tolist(),
                                  temperature=1.0, valid_length=3)
    assert math.isclose(kd.item(), ref, rel_tol=1e-4, abs_tol=1e-5)


@_needs_torch
def test_kd_weight_zero_row_and_per_language_weighting():
    import torch

    from pipeline.omniasr_distill import kd_loss

    student = torch.randn(2, 3, 5)
    teacher = torch.randn(2, 3, 5)
    only_first = kd_loss(student, teacher, temperature=1.0,
                         row_weights=[1.0, 0.0], valid_lengths=[3, 3])
    first_alone = kd_loss(student[:1], teacher[:1], temperature=1.0,
                          row_weights=[1.0], valid_lengths=[3])
    assert math.isclose(only_first.item(), first_alone.item(), rel_tol=1e-5)


@_needs_torch
def test_teacher_weights_unchanged_after_an_optimizer_step():
    import copy

    import torch

    teacher = torch.nn.Linear(4, 4)
    teacher.eval()
    teacher.requires_grad_(False)
    before = copy.deepcopy(teacher.state_dict())
    student = torch.nn.Linear(4, 4)
    opt = torch.optim.SGD(student.parameters(), lr=0.1)
    x = torch.randn(8, 4)
    loss = (student(x) - teacher(x)).pow(2).mean()
    opt.zero_grad(); loss.backward(); opt.step()
    for key, value in teacher.state_dict().items():
        assert torch.equal(value, before[key]), "teacher weights moved"
    assert all(not p.requires_grad for p in teacher.parameters())


class _FakeLayout:
    def __init__(self, seq_lens):
        self.seq_lens = seq_lens


class _FakeCTCModel:
    """Mimics the fairseq2 v0.6 Wav2Vec2AsrModel.forward contract:
    (loss, logits, layout) with return_logits=True, (logits, layout) without.
    Validates _batch_loss_kd's UNPACKING (the round-17 tuple crash) without
    loading the 1B model."""
    def __init__(self, rows, frames, vocab):
        self.rows, self.frames, self.vocab = rows, frames, vocab

    def __call__(self, seqs, layout, *, targets=None, targets_layout=None,
                 return_logits=False):
        import torch
        logits = torch.randn(self.rows, self.frames, self.vocab,
                             requires_grad=return_logits)
        lay = _FakeLayout([self.frames] * self.rows)
        if return_logits:
            return logits.sum(), logits, lay
        return logits, lay


@_needs_torch
def test_batch_loss_kd_unpacks_the_fairseq2_tuple_contract():
    import torch

    from pipeline.omniasr_train import _batch_loss_kd

    class _Seqs:
        shape = (2, 100)
    batch = {"seqs": _Seqs(), "seqs_layout": None,
             "targets": None, "targets_layout": None,
             "languages": ["english", "pidgin"]}
    student = _FakeCTCModel(2, 4, 6)

    class _Teacher(_FakeCTCModel):
        def parameters(self):
            return []
    teacher = _Teacher(2, 4, 6)
    out = _batch_loss_kd(student, batch, teacher=teacher, alpha=0.5,
                         temperature=1.0,
                         preservation_languages=("english", "french", "swahili", "lingala"),
                         language_weights=(("english", 1.0),))
    assert torch.isfinite(out)


@_needs_torch
def test_peak_gpu_memory_is_measurable_when_cuda_present():
    import pytest as _pytest
    import torch

    if not torch.cuda.is_available():
        _pytest.skip("no CUDA on this host; peak-memory is a calibration-time metric")
    from pipeline.omniasr_distill import kd_loss
    torch.cuda.reset_peak_memory_stats()
    student = torch.randn(2, 50, 100, device="cuda")
    teacher = torch.randn(2, 50, 100, device="cuda")
    kd_loss(student, teacher, temperature=1.0, row_weights=[1.0, 0.0],
            valid_lengths=[50, 50])
    assert torch.cuda.max_memory_allocated() > 0


@_needs_torch
def test_nonfinite_kd_diverges_without_persisting_a_checkpoint(tmp_path):
    import torch

    from pipeline.omniasr_train import run_training_loop
    from tests.test_omniasr_train import make_config

    model = torch.nn.Linear(4, 4)
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    config = make_config(MEDZEN_MAX_STEPS="3", MEDZEN_GRAD_ACCUM="1",
                         MEDZEN_CHECKPOINT_DIR=str(tmp_path))
    outcome = run_training_loop(
        model=model, optimizer=optimizer,
        batches=lambda i: torch.randn(2, 4),
        batch_loss=lambda m, b: m(b).pow(2).mean() + float("inf"),
        config=config, fingerprint="f" * 64,
        save_state=lambda p, s: None, load_state=lambda p: 0)
    assert outcome["status"] == "TRAINING_DIVERGED_NONFINITE"
    assert not list(tmp_path.glob("step-*.pt")), "no checkpoint on divergence"
