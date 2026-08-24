"""Arm-2 preservation-aware distillation (Codex reviews #14-#17).

Arm-1 improved aggregate WER but REGRESSED Lingala — a strong regression
sentinel where any loss is disqualifying (B5-UNIVERSAL-PILOT-DESIGN-2026-001).
Arm-2 prevents that: the FROZEN base model is a distillation TEACHER for the
preservation/sentinel languages, and a KD term anchors the student's CTC
frame log-probs to the teacher's on those languages, so the student may keep
its Pidgin/Kinyarwanda/French gains WITHOUT overwriting what the base already
serves well.

Teacher eligibility is constrained by ALIGNMENT: KD-by-KL over CTC frame
log-probs requires the teacher and student to share the EXACT vocabulary
ordering and frame-subsampling grid. That holds ONLY for the CTC family
(wav2vec2_asr / omniASR-CTC-1B-v2) under unconditioned ctc_greedy — the same
card the student trains under. The base teacher is byte-identical to the
student's own staged base. Kinyarwanda v1 (a rank-32 encoder-scoped LoRA
merged onto that exact base with the identical sha-verified tokenizer) is
ALSO alignment-eligible, but Kinyarwanda v2, the LLM variant and any
language-conditioned decode path are NOT approved teachers.

HOST-SAFE: importing this module needs no torch. The numeric reference
(kd_divergence_reference), alignment, the language mask and the freeze audit
are pure and host-tested; the DIFFERENTIABLE kd_loss and load_teacher use
torch lazily and are validated only in the trainer image (C3).
"""
from __future__ import annotations

import math
from typing import Any, Sequence


class DistillationRefusal(RuntimeError):
    """Fail-closed: a KD misconfiguration or alignment mismatch refuses
    BEFORE any GPU-hours accrue rather than producing a mis-scaled loss."""


def preservation_mask(language_tags: Sequence[Any],
                      preservation_languages: Sequence[Any],
                      *, weights: dict[str, float] | None = None,
                      strict: bool = False) -> list[float]:
    """Per-row KD weight: 0.0 for non-preservation rows, else the language's
    weight (default 1.0). The KD term is applied ONLY to preservation rows so
    the student is free to move on the target languages (pidgin, kinyarwanda,
    ewe) while being held to the teacher on the sentinels. Codex review #18:
    in ``strict`` mode a MISSING or unknown language tag on a row REFUSES
    (never silently drops preservation) — the tag must be authoritative."""
    pres = {str(lang).strip().lower() for lang in preservation_languages}
    weight_map = {str(k).strip().lower(): float(v)
                  for k, v in (weights or {}).items()}
    out: list[float] = []
    for tag in language_tags:
        norm = str(tag).strip().lower()
        if strict and not norm:
            raise DistillationRefusal(
                "a batch row has no authoritative language tag — KD cannot "
                "decide preservation membership; refusing rather than "
                "silently dropping the term")
        out.append(weight_map.get(norm, 1.0) if norm in pres else 0.0)
    return out


def _validated_temperature(temperature: Any) -> float:
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise DistillationRefusal(
            f"KD temperature must be a number, got {temperature!r}")
    value = float(temperature)
    if not math.isfinite(value) or value <= 0.0:
        raise DistillationRefusal(
            f"KD temperature must be finite and > 0, got {temperature!r}")
    return value


def assert_kd_alignment(student_shape: Sequence[int],
                        teacher_shape: Sequence[int]) -> None:
    """CTC KD needs identical (…, frames, vocab) between student and teacher
    for identical input. A mismatch (different frame count or vocabulary,
    e.g. a wrong-family or conditioned teacher) is a hard refusal, raised at
    the FIRST batch before any GPU-hours accrue."""
    s = tuple(int(x) for x in student_shape)
    t = tuple(int(x) for x in teacher_shape)
    if s != t:
        raise DistillationRefusal(
            f"KD alignment mismatch: student logits {s} != teacher {t} — "
            "different frame count or vocabulary; not an aligned CTC teacher")
    if len(s) < 2 or s[-1] < 1:
        raise DistillationRefusal(f"degenerate KD logits shape {s}")


def _softmax(row: Sequence[float]) -> list[float]:
    top = max(row)
    exps = [math.exp(x - top) for x in row]
    total = sum(exps)
    return [e / total for e in exps]


def kd_divergence_reference(student_logits: Sequence[Sequence[float]],
                            teacher_logits: Sequence[Sequence[float]],
                            *, temperature: float,
                            valid_length: int | None = None) -> float:
    """Deterministic, torch-FREE reference numerics for one sequence.

    Temperature-scaled KL(teacher || student) averaged over frames, then
    scaled by T^2 (the standard Hinton distillation scaling so the gradient
    magnitude is temperature-invariant). student/teacher_logits are
    [frames][vocab] raw logits. The in-image torch kd_loss mirrors this
    exactly; the host test pins determinism, an in-image test pins equality.
    """
    temp = _validated_temperature(temperature)
    student_logits = list(student_logits)
    teacher_logits = list(teacher_logits)
    if len(student_logits) != len(teacher_logits):
        raise DistillationRefusal(
            "KD reference: student and teacher frame counts differ")
    n_valid = len(student_logits) if valid_length is None else int(valid_length)
    total = 0.0
    counted = 0
    for index, (srow, trow) in enumerate(zip(student_logits, teacher_logits)):
        if index >= n_valid:
            break                       # padded frames do not contribute
        if len(srow) != len(trow):
            raise DistillationRefusal(
                "KD reference: student and teacher vocab sizes differ")
        s_probs = _softmax([x / temp for x in srow])
        t_probs = _softmax([x / temp for x in trow])
        kl = 0.0
        for p_t, p_s in zip(t_probs, s_probs):
            if p_t > 0.0:
                kl += p_t * (math.log(p_t) - math.log(max(p_s, 1e-12)))
        total += kl
        counted += 1
    if counted == 0:
        return 0.0
    # MEAN over valid frames (Codex review #18: dividing by rows made this
    # frames-times too large) then Hinton T^2 scaling
    return (total / counted) * (temp * temp)


def kd_loss(student_logits: Any, teacher_logits: Any, *,
            temperature: float, row_weights: Sequence[float],
            valid_lengths: Sequence[int]) -> Any:
    """DIFFERENTIABLE torch KD term (in-image / C3 only). Logits are
    [rows, frames, vocab].

    Upcasts bf16 logits to fp32 for a stable softmax/KL, computes
    KL(teacher || student) per frame, and reduces as a MEAN over ONLY the
    VALID (non-padded), preservation-weighted frames — Codex review #18:
    (a) dividing by rows made the term frames-times too large; (b) padded
    frames (discarded layouts) must not contribute or the effective KD
    weight would depend on clip length. row_weights is the per-row
    preservation weight (0 = excluded); valid_lengths is the per-row count of
    real frames (from the encoder output layout, identical for student and
    teacher). Alignment and temperature are validated BEFORE any tensor math.
    """
    import torch
    import torch.nn.functional as functional

    assert_kd_alignment(tuple(student_logits.shape), tuple(teacher_logits.shape))
    if student_logits.dim() != 3:
        raise DistillationRefusal(
            f"KD expects [rows, frames, vocab] logits, got {tuple(student_logits.shape)}")
    temp = _validated_temperature(temperature)
    rows, frames = int(student_logits.shape[0]), int(student_logits.shape[1])
    if len(row_weights) != rows or len(valid_lengths) != rows:
        raise DistillationRefusal(
            "KD row_weights/valid_lengths length must equal the row count")
    student = student_logits.float() / temp
    teacher = teacher_logits.float() / temp
    log_student = functional.log_softmax(student, dim=-1)
    log_teacher = functional.log_softmax(teacher.detach(), dim=-1)
    probs_teacher = log_teacher.exp()
    per_frame_kl = (probs_teacher * (log_teacher - log_student)).sum(dim=-1)  # [rows, frames]
    device, dtype = per_frame_kl.device, per_frame_kl.dtype
    frame_index = torch.arange(frames, device=device).unsqueeze(0)           # [1, frames]
    lengths = torch.as_tensor([int(v) for v in valid_lengths],
                              device=device).unsqueeze(1)                    # [rows, 1]
    valid = (frame_index < lengths).to(dtype)                                # [rows, frames]
    weights = torch.as_tensor([float(w) for w in row_weights], dtype=dtype,
                              device=device).unsqueeze(1)                    # [rows, 1]
    selected = valid * weights                                              # [rows, frames]
    denominator = selected.sum().clamp_min(1.0)
    kd = (per_frame_kl * selected).sum() / denominator
    return kd * (temp * temp)


def load_teacher(card: str, device: Any, dtype: Any) -> Any:
    """The FROZEN base teacher (in-image / C3 only): a fresh, un-adapted base
    instance re-resolved from the SAME sha-verified card the student stages,
    set to eval() and requires_grad_(False). It is never wrapped, merged,
    optimized or checkpointed, so it costs only +1 model's weights on GPU and
    (eval() disabling dropout) does not perturb the seeded RNG trajectory —
    preserving kill/resume equivalence. Must be obtained BEFORE the student's
    full-mode unfreeze so the student's updates cannot mutate it."""
    import torch  # noqa: F401
    from fairseq2.models.hub import load_model

    teacher = load_model(card, device=device, dtype=dtype)
    teacher.eval()
    teacher.requires_grad_(False)
    return teacher


def teacher_freeze_audit(teacher: Any) -> dict[str, Any]:
    """Assert every teacher parameter has requires_grad == False. Works on a
    torch model OR a host stub whose .parameters() yields objects with a
    .requires_grad attribute — so the freeze invariant is host-testable."""
    total = 0
    trainable = 0
    for parameter in teacher.parameters():
        total += 1
        if bool(getattr(parameter, "requires_grad", False)):
            trainable += 1
    if trainable:
        raise DistillationRefusal(
            f"teacher is NOT frozen: {trainable}/{total} parameters require "
            "grad — a trainable teacher would be distilled INTO, not from")
    return {"parameters": total, "trainable": 0, "frozen": True}


class _StubParameter:
    __slots__ = ("requires_grad",)

    def __init__(self, requires_grad: bool):
        self.requires_grad = requires_grad


class _StubTeacher:
    """A torch-free teacher stand-in for host tests: exposes .parameters()
    -> objects with a mutable .requires_grad, so teacher_freeze_audit is
    exercised on the host without loading a real model."""

    def __init__(self, params: list["_StubParameter"]):
        self._params = params

    def parameters(self):
        return self._params


def make_kd_stub_teacher(*, n_params: int, requires_grad: bool) -> "_StubTeacher":
    return _StubTeacher([_StubParameter(requires_grad) for _ in range(n_params)])
