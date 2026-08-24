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

def test_preservation_mask_selects_only_sentinels():
    tags = ["English", "pidgin", "lingala", "kinyarwanda", "swahili"]
    mask = preservation_mask(tags, ["english", "french", "swahili", "lingala"])
    assert mask == [1.0, 0.0, 1.0, 0.0, 1.0]


def test_kd_is_off_by_default_and_leaves_config_neutral():
    config = _kd_config()
    assert config.kd_enable is False
    assert config.kd_alpha == 0.0 and config.kd_preservation_languages == ()


def test_kd_enable_validates_alpha_temperature_and_languages():
    good = _kd_config(MEDZEN_KD_ENABLE="1")
    assert good.kd_enable and good.kd_teacher_mode == "base"
    assert set(good.kd_preservation_languages) == {"english", "french", "swahili", "lingala"}
    with pytest.raises(Exception, match="ALPHA"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_ALPHA="1.5")
    with pytest.raises(Exception, match="TEMPERATURE|finite"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_TEMPERATURE="0")
    with pytest.raises(Exception, match="not in the training"):
        _kd_config(MEDZEN_KD_ENABLE="1", MEDZEN_KD_PRESERVATION_LANGUAGES="klingon")


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
    student = torch.randn(1, 4, 6, requires_grad=True)
    teacher = torch.randn(1, 4, 6)
    mask = [1.0]  # single (preservation) row
    kd = kd_loss(student, teacher, temperature=2.0, language_mask=mask)
    assert torch.isfinite(kd) and kd.item() >= 0.0
    kd.backward()
    assert student.grad is not None and torch.isfinite(student.grad).all()
    # matches the torch-free reference per frame (masked mean == unmasked
    # mean for a single retained row)
    ref = kd_divergence_reference(student[0].tolist(), teacher[0].tolist(),
                                  temperature=2.0)
    assert math.isclose(kd.item(), ref, rel_tol=1e-4, abs_tol=1e-5)


@_needs_torch
def test_kd_mask_zeroes_non_preservation_rows():
    import torch

    from pipeline.omniasr_distill import kd_loss

    student = torch.randn(2, 3, 5)
    teacher = torch.randn(2, 3, 5)
    only_first = kd_loss(student, teacher, temperature=1.0, language_mask=[1.0, 0.0])
    only_first_alone = kd_loss(student[:1], teacher[:1], temperature=1.0,
                               language_mask=[1.0])
    assert math.isclose(only_first.item(), only_first_alone.item(), rel_tol=1e-5)


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


@_needs_torch
def test_nonfinite_kd_diverges_without_persisting_a_checkpoint(tmp_path):
    import torch

    from pipeline.omniasr_train import run_training_loop
    from tests.test_omniasr_train import make_config  # reuse the loop fixture

    model = torch.nn.Linear(4, 4)
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    config = make_config(MEDZEN_MAX_STEPS="3", MEDZEN_GRAD_ACCUM="1",
                         MEDZEN_CHECKPOINT_DIR=str(tmp_path))
    # a KD term that overflows to non-finite must trip the existing guard
    outcome = run_training_loop(
        model=model, optimizer=optimizer,
        batches=lambda i: torch.randn(2, 4),
        batch_loss=lambda m, b: m(b).pow(2).mean() + float("inf"),
        config=config, fingerprint="f" * 64,
        save_state=lambda p, s: None, load_state=lambda p: 0)
    assert outcome["status"] == "TRAINING_DIVERGED_NONFINITE"
    assert not list(tmp_path.glob("step-*.pt")), "no checkpoint on divergence"
