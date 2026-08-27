"""Arm-2b host tests (owner decision 2026-08-27): warm-start-from-Arm-1 +
the Arm-1 retention-anchor second teacher.

Covers the torch-free surface: parse_config knobs + refusals, the one-way
fingerprint migration (pre-2b runs keep byte-identical payloads), the
make_batch_loss wiring guards, CalibrationMetrics dual-KD folding and the
/1-vs-/2 schema selection, and verify_arm2_calibration's dual-KD checks
(equation extension, masquerade, dead-anchor, defanged coverage). The
differentiable two-term loss itself is exercised in-image (C3)."""

from __future__ import annotations

import pytest

from pipeline.omniasr_train import (
    CALIBRATION_METRICS_SCHEMA,
    CALIBRATION_METRICS_SCHEMA_V2,
    CalibrationMetrics,
    TrainerRefusal,
    make_batch_loss,
    parse_config,
)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_arm2_calibration import (  # noqa: E402
    METRICS_SCHEMA_V2,
    verify_calibration,
)

_SHA = "c" * 64
_BASE_ENV = {
    "MEDZEN_VARIANT": "ctc",
    "MEDZEN_MANIFEST_VERSION": "v9",
    "MEDZEN_LANGUAGES": "english,french,swahili,lingala,pidgin,kinyarwanda,ewe",
    "MEDZEN_SEED": "20260825",
}


def _kd_env() -> dict[str, str]:
    env = dict(_BASE_ENV)
    env.update({
        "MEDZEN_KD_ENABLE": "1",
        "MEDZEN_KD_ALPHA": "1.0",
        "MEDZEN_KD_PRESERVATION_LANGUAGES": "english,french,swahili,lingala",
    })
    return env


def _arm2b_env() -> dict[str, str]:
    env = _kd_env()
    env.update({
        "MEDZEN_KD_TEACHER_MODE": "base+arm1_retention",
        "MEDZEN_KD_RETENTION_ALPHA": "1.0",
        "MEDZEN_KD_RETENTION_LANGUAGES": "pidgin,kinyarwanda,ewe",
        "MEDZEN_KD_RETENTION_TEACHER_S3_URI": "s3://bucket/arm1/model.tar.gz",
        "MEDZEN_KD_RETENTION_TEACHER_VERSION_ID": "Vid1",
        "MEDZEN_KD_RETENTION_TEACHER_SHA256": _SHA,
        "MEDZEN_STUDENT_INIT_MODE": "arm1",
        "MEDZEN_STUDENT_INIT_S3_URI": "s3://bucket/arm1/model.tar.gz",
        "MEDZEN_STUDENT_INIT_VERSION_ID": "Vid1",
        "MEDZEN_STUDENT_INIT_SHA256": _SHA,
    })
    return env


# --------------------------------------------------------------------------
# parse_config: the new knobs + every refusal path
# --------------------------------------------------------------------------

def test_pre_arm2b_fingerprints_carry_no_new_keys():
    """The one-way migration: plain AND single-KD payloads must not gain any
    Arm-2b key, so every pre-2b checkpoint resumes byte-identically."""
    for env in (dict(_BASE_ENV), _kd_env()):
        payload = parse_config(env).fingerprint_payload()
        leaked = [k for k in payload
                  if k.startswith(("student_init", "kd_retention"))]
        assert leaked == [], leaked


def test_arm2b_fingerprint_binds_every_new_identity():
    payload = parse_config(_arm2b_env()).fingerprint_payload()
    for key in ("student_init_mode", "student_init_s3_uri",
                "student_init_version_id", "student_init_sha256",
                "kd_retention_alpha", "kd_retention_languages",
                "kd_retention_teacher_s3_uri",
                "kd_retention_teacher_version_id",
                "kd_retention_teacher_sha256"):
        assert key in payload, key
    assert payload["kd_retention_languages"] == ["ewe", "kinyarwanda",
                                                 "pidgin"]
    assert payload["student_init_sha256"] == _SHA


def test_warm_start_and_retention_configs_parse():
    cfg = parse_config(_arm2b_env())
    assert cfg.student_init_mode == "arm1"
    assert cfg.kd_teacher_mode == "base+arm1_retention"
    assert cfg.kd_retention_alpha == 1.0
    assert cfg.kd_retention_languages == ("ewe", "kinyarwanda", "pidgin")


@pytest.mark.parametrize("mutate,why", [
    (lambda e: e.__setitem__("MEDZEN_KD_RETENTION_ALPHA", "2.0"),
     "retention alpha above the (0,1] cap"),
    (lambda e: e.__setitem__("MEDZEN_KD_RETENTION_ALPHA", "0"),
     "retention alpha zero silently disables the anchor"),
    (lambda e: e.__setitem__("MEDZEN_KD_RETENTION_ALPHA", "nan"),
     "retention alpha NaN"),
    (lambda e: e.__setitem__("MEDZEN_KD_RETENTION_LANGUAGES",
                             "pidgin,lingala"),
     "retention overlaps the preservation set"),
    (lambda e: e.__setitem__("MEDZEN_KD_RETENTION_LANGUAGES", "klingon"),
     "retention language outside the training set"),
    (lambda e: e.pop("MEDZEN_KD_RETENTION_TEACHER_SHA256"),
     "missing retention teacher sha"),
    (lambda e: e.__setitem__("MEDZEN_KD_RETENTION_TEACHER_SHA256", "beef"),
     "short retention teacher sha"),
    (lambda e: e.__setitem__("MEDZEN_KD_RETENTION_TEACHER_S3_URI",
                             "https://x/y"),
     "non-s3 retention teacher uri"),
    (lambda e: e.__setitem__("MEDZEN_STUDENT_INIT_SHA256", "zz"),
     "bad student-init sha"),
    (lambda e: e.__setitem__("MEDZEN_STUDENT_INIT_MODE", "arm7"),
     "unknown student-init mode"),
    (lambda e: e.pop("MEDZEN_STUDENT_INIT_S3_URI"),
     "missing student-init uri"),
    (lambda e: e.__setitem__("MEDZEN_KD_TEACHER_MODE", "kinyarwanda_v1"),
     "unknown teacher mode"),
    (lambda e: e.__setitem__("MEDZEN_STUDENT_INIT_VERSION_ID", "OTHER"),
     "warm-start and retention teacher must pin ONE arm1 identity"),
    (lambda e: e.__setitem__("MEDZEN_STUDENT_INIT_SHA256", "d" * 64),
     "same-identity sha drift refuses"),
])
def test_arm2b_parse_refusals(mutate, why):
    env = _arm2b_env()
    mutate(env)
    with pytest.raises(TrainerRefusal):
        parse_config(env)
    del why  # the parametrize id documents the case


def test_retention_mode_with_kd_off_refuses():
    env = dict(_BASE_ENV)
    env["MEDZEN_KD_TEACHER_MODE"] = "base+arm1_retention"
    with pytest.raises(TrainerRefusal):
        parse_config(env)


# --------------------------------------------------------------------------
# make_batch_loss wiring guards (host-testable: refusals fire before torch)
# --------------------------------------------------------------------------

def test_retention_mode_without_teacher_object_refuses():
    cfg = parse_config(_arm2b_env())
    with pytest.raises(TrainerRefusal):
        make_batch_loss(cfg, teacher=object(), retention_teacher=None)


def test_base_mode_with_stray_retention_teacher_refuses():
    cfg = parse_config(_kd_env())
    with pytest.raises(TrainerRefusal):
        make_batch_loss(cfg, teacher=object(), retention_teacher=object())


# --------------------------------------------------------------------------
# CalibrationMetrics: dual-KD folding + /1-vs-/2 schema selection
# --------------------------------------------------------------------------

_RET_LANGS = ("pidgin", "kinyarwanda", "ewe")


def _dual_micro() -> dict:
    return {
        "ctc": 1.0, "kd": 0.4, "total": 1.0 + 1.0 * 0.4 + 1.0 * 0.2,
        "alpha": 1.0,
        "kd_coverage": {lang: {"rows": 1, "frames": 5}
                        for lang in ("english", "french", "swahili",
                                     "lingala")},
        "kd_retention": 0.2, "retention_alpha": 1.0,
        "kd_retention_coverage": {lang: {"rows": 1, "frames": 4}
                                  for lang in _RET_LANGS},
    }


def _dual_artifact(steps: int = 4) -> dict:
    metrics = CalibrationMetrics()
    for step in range(1, steps + 1):
        metrics.record_micro(_dual_micro())
        metrics.commit_step(step, lr=1e-5)
    return metrics.finalize(
        status="COMPLETED", steps_completed=steps, max_steps=steps,
        peak_gpu_bytes=10_000_000_000, wall_seconds=60.0,
        samples_per_step=16, identity={"run_fingerprint": "f" * 64},
        serve={"readyz": True},
        dev_sentinel_wer={"lingala": 0.18, "pidgin": 0.22})


def test_dual_metrics_emit_v2_with_retention_decomposition():
    art = _dual_artifact()
    assert art["schema"] == CALIBRATION_METRICS_SCHEMA_V2
    assert art["kd_retention_positive_finite_steps"] == 4
    assert art["kd_retention_min"] == pytest.approx(0.2)
    assert set(art["kd_retention_coverage"]) == set(_RET_LANGS)
    for record in art["per_step"]:
        assert record["kd_retention"] == pytest.approx(0.2)
        assert record["retention_alpha"] == pytest.approx(1.0)


def test_single_kd_metrics_stay_byte_identical_v1():
    metrics = CalibrationMetrics()
    for step in range(1, 3):
        metrics.record_micro({
            "ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
            "kd_coverage": {"english": {"rows": 1, "frames": 5}}})
        metrics.commit_step(step, lr=1e-5)
    art = metrics.finalize(
        status="COMPLETED", steps_completed=2, max_steps=2,
        peak_gpu_bytes=1, wall_seconds=1.0, samples_per_step=2,
        identity=None, serve=None, dev_sentinel_wer=None)
    assert art["schema"] == CALIBRATION_METRICS_SCHEMA
    assert not any(k.startswith("kd_retention") for k in art)
    assert not any("kd_retention" in r for r in art["per_step"])


def test_mixed_single_dual_accumulation_refuses():
    metrics = CalibrationMetrics()
    metrics.record_micro(_dual_micro())
    plain = _dual_micro()
    plain.pop("kd_retention")
    plain.pop("retention_alpha")
    plain.pop("kd_retention_coverage")
    metrics.record_micro(plain)
    with pytest.raises(TrainerRefusal):
        metrics.commit_step(1, lr=1e-5)


def test_retention_coverage_survives_state_roundtrip():
    metrics = CalibrationMetrics()
    metrics.record_micro(_dual_micro())
    metrics.commit_step(1, lr=1e-5)
    state = metrics.to_state()
    fresh = CalibrationMetrics()
    fresh.restore(state)
    assert fresh.retention_coverage == metrics.retention_coverage
    legacy = CalibrationMetrics()
    legacy.restore({"per_step": [], "coverage": {}})  # pre-2b sidecar
    assert legacy.retention_coverage == {}


# --------------------------------------------------------------------------
# verify_arm2_calibration: dual-KD acceptance + every tamper direction
# --------------------------------------------------------------------------

def _v2_spec(steps: int = 4) -> dict:
    return {
        "metrics_schema": METRICS_SCHEMA_V2,
        "expected_steps": steps,
        "gpu_memory_ceiling_bytes": 20_000_000_000,
        "required_preservation_coverage": ["english", "french", "swahili",
                                           "lingala"],
        "required_retention_coverage": list(_RET_LANGS),
        "dev_sentinel_languages": ["lingala", "pidgin"],
        "metrics_artifact": "calibration-metrics.json",
        "script": "scripts/verify_arm2_calibration.py",
    }


def _retention_failures(failures: list[str]) -> list[str]:
    return [f for f in failures
            if "retention" in f or "masquerade" in f
            or "loss equation" in f or "schema" in f]


def test_v2_artifact_passes_the_dual_kd_checks():
    failures = verify_calibration(_dual_artifact(), _v2_spec())
    assert _retention_failures(failures) == [], failures


def test_warm_zero_retention_step_is_legitimate():
    """Codex Arm-2b review finding 2: a warm-started student is initially
    IDENTICAL to the Arm-1 teacher, so kd_retention == 0 on a step (with
    consistent loss arithmetic) must NOT fail."""
    art = _dual_artifact()
    art["per_step"][1]["kd_retention"] = 0.0
    art["per_step"][1]["total"] = art["per_step"][1]["ctc"] \
        + art["per_step"][1]["alpha"] * art["per_step"][1]["kd"]
    art["kd_retention_min"] = 0.0
    art["kd_retention_positive_finite_steps"] = 3
    failures = verify_calibration(art, _v2_spec())
    assert _retention_failures(failures) == [], failures


def test_all_zero_retention_run_is_a_dead_anchor():
    """...but an anchor that NEVER registers any divergence over the whole
    run is disconnected, not warm — kd_retention_max must be > 0."""
    art = _dual_artifact()
    for r in art["per_step"]:
        r["kd_retention"] = 0.0
        r["total"] = r["ctc"] + r["alpha"] * r["kd"]
    art["kd_retention_min"] = 0.0
    art["kd_retention_max"] = 0.0
    art["kd_retention_positive_finite_steps"] = 0
    failures = verify_calibration(art, _v2_spec())
    assert any("never registered any divergence" in f
               for f in failures), failures


def test_negative_retention_kl_is_broken():
    art = _dual_artifact()
    art["per_step"][2]["kd_retention"] = -0.1
    failures = verify_calibration(art, _v2_spec())
    assert any("cannot be negative" in f for f in failures), failures


def test_equation_must_include_the_retention_term():
    art = _dual_artifact()
    # forge a total that omits retention_alpha*kd_retention
    art["per_step"][0]["total"] = art["per_step"][0]["ctc"] \
        + art["per_step"][0]["alpha"] * art["per_step"][0]["kd"]
    failures = verify_calibration(art, _v2_spec())
    assert any("loss equation violated" in f for f in failures), failures


def test_v1_records_carrying_retention_fields_are_a_masquerade():
    art = _dual_artifact()
    art["schema"] = CALIBRATION_METRICS_SCHEMA  # forge /1
    spec = _v2_spec()
    spec["metrics_schema"] = CALIBRATION_METRICS_SCHEMA
    failures = verify_calibration(art, spec)
    assert any("masquerade" in f for f in failures), failures


def test_schema_must_match_the_packet_pin():
    art = _dual_artifact()
    art["schema"] = CALIBRATION_METRICS_SCHEMA  # /1 artifact under a /2 pin
    failures = verify_calibration(art, _v2_spec())
    assert any("!= the packet-pinned" in f for f in failures), failures


def test_defanged_retention_coverage_requirement_fails():
    spec = _v2_spec()
    spec["required_retention_coverage"] = []
    failures = verify_calibration(_dual_artifact(), spec)
    assert any("cannot be defanged" in f for f in failures), failures


def test_tampered_retention_summary_counter_fails():
    art = _dual_artifact()
    art["kd_retention_positive_finite_steps"] = 0
    failures = verify_calibration(art, _v2_spec())
    assert any("kd_retention_positive_finite_steps=0 != recomputed" in f
               for f in failures), failures


def test_tampered_retention_max_fails():
    art = _dual_artifact()
    art["kd_retention_max"] = 9.9
    failures = verify_calibration(art, _v2_spec())
    assert any("the summary alone is not trusted" in f
               for f in failures), failures


def test_v2_with_kd_disabled_spec_is_contradictory():
    spec = _v2_spec()
    spec["kd_enabled"] = False
    failures = verify_calibration(_dual_artifact(), spec)
    assert any("contradictory" in f for f in failures), failures


def test_missing_retention_language_coverage_fails():
    art = _dual_artifact()
    art["kd_retention_coverage"].pop("ewe")
    failures = verify_calibration(art, _v2_spec())
    assert any("no retention-KD coverage recorded for retention language "
               "'ewe'" in f for f in failures), failures


# --------------------------------------------------------------------------
# launcher (validate_arm2_semantics): Arm-2b recipe/env/spec parity
# --------------------------------------------------------------------------

import copy
import json

from b5_sagemaker_job import JobRefusal, validate_arm2_semantics  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_H3 = json.loads((_ROOT / "platform/manifests/"
                  "B5-UNIVERSAL-ARM2-H3-SAGEMAKER-BINDINGS-2026-001.json"
                  ).read_bytes())


def _dual_packet() -> dict:
    """The committed (fully valid) H3 KD packet mutated into an Arm-2b
    dual-teacher warm-started packet — the shape the calibration and grid
    packets will use."""
    p = copy.deepcopy(_H3)
    env = p["environment"]
    p["distillation"]["teacher_mode"] = "base+arm1_retention"
    env["MEDZEN_KD_TEACHER_MODE"] = "base+arm1_retention"
    p["distillation"]["retention"] = {
        "alpha": 1.0,
        "languages": ["pidgin", "kinyarwanda", "ewe"],
        "teacher": {"s3_uri": "s3://bucket/arm1/model.tar.gz",
                    "s3_version_id": "Vid1", "sha256": _SHA},
    }
    env["MEDZEN_KD_RETENTION_ALPHA"] = "1.0"
    env["MEDZEN_KD_RETENTION_LANGUAGES"] = "pidgin,kinyarwanda,ewe"
    env["MEDZEN_KD_RETENTION_TEACHER_S3_URI"] = "s3://bucket/arm1/model.tar.gz"
    env["MEDZEN_KD_RETENTION_TEACHER_VERSION_ID"] = "Vid1"
    env["MEDZEN_KD_RETENTION_TEACHER_SHA256"] = _SHA
    p["student_init"] = {"mode": "arm1",
                         "s3_uri": "s3://bucket/arm1/model.tar.gz",
                         "s3_version_id": "Vid1", "sha256": _SHA}
    env["MEDZEN_STUDENT_INIT_MODE"] = "arm1"
    env["MEDZEN_STUDENT_INIT_S3_URI"] = "s3://bucket/arm1/model.tar.gz"
    env["MEDZEN_STUDENT_INIT_VERSION_ID"] = "Vid1"
    env["MEDZEN_STUDENT_INIT_SHA256"] = _SHA
    spec = p["result_verifier"]
    spec["metrics_schema"] = "b5-arm2-calibration-metrics/2"
    spec["required_retention_coverage"] = ["pidgin", "kinyarwanda", "ewe"]
    return p


def test_dual_packet_passes_every_arm2b_gate():
    """The synthetic fixture cannot byte-match a COMMITTED execution
    contract, so full validation must fail EXACTLY there — proving every
    Arm-2b check (retention recipe/env parity, /2 pins, student_init
    parity, dev-sentinel bound) passed first. Real packets pair with real
    committed contracts and validate fully."""
    p = _dual_packet()
    with pytest.raises(JobRefusal, match="execution contract"):
        validate_arm2_semantics(p, p["environment"])


def test_h3_committed_packet_still_validates_unchanged():
    validate_arm2_semantics(_H3, _H3["environment"])  # no raise


def test_pidgin_dev_sentinel_is_admitted_for_a_retention_run():
    """pidgin (a retention language) as a dev sentinel must clear the
    subset bound and every Arm-2b gate — the synthetic fixture then stops
    at the committed-contract byte-equality like the base fixture."""
    p = _dual_packet()
    p["result_verifier"]["dev_sentinel_languages"] = ["lingala", "swahili",
                                                      "pidgin"]
    files = p["environment"]["MEDZEN_DEV_SENTINEL_MANIFEST_FILES"]
    p["environment"]["MEDZEN_DEV_SENTINEL_MANIFEST_FILES"] = (
        files + ",pidgin=platform/manifests/dev-sentinels/pidgin.jsonl")
    with pytest.raises(JobRefusal) as exc:
        validate_arm2_semantics(p, p["environment"])
    assert "subset of the preservation" not in str(exc.value)
    assert "execution contract" in str(exc.value) or \
        "dev-sentinel" in str(exc.value)


def test_pidgin_dev_sentinel_refused_without_retention():
    p = copy.deepcopy(_H3)
    p["result_verifier"]["dev_sentinel_languages"] = ["lingala", "swahili",
                                                      "pidgin"]
    with pytest.raises(JobRefusal, match="subset of the preservation"):
        validate_arm2_semantics(p, p["environment"])


@pytest.mark.parametrize("mutate,match", [
    (lambda p: p["distillation"]["retention"].__setitem__("alpha", 0.5),
     "retention.alpha"),
    (lambda p: p["distillation"]["retention"].__setitem__(
        "languages", ["pidgin"]), "retention.languages"),
    (lambda p: p["distillation"]["retention"]["teacher"].__setitem__(
        "sha256", "d" * 64), "retention.teacher.sha256"),
    (lambda p: p["distillation"].pop("retention"),
     "requires a\\s+distillation.retention"),
    (lambda p: p["student_init"].__setitem__("sha256", "d" * 64),
     "student_init.sha256"),
    (lambda p: p.pop("student_init"), "requires a top-level\\s+student_init"),
    (lambda p: p["result_verifier"].__setitem__(
        "metrics_schema", "b5-arm2-calibration-metrics/1"),
     "must pin result_verifier"),
    (lambda p: p["result_verifier"].__setitem__(
        "required_retention_coverage", ["pidgin"]),
     "must equal\\s+MEDZEN_KD_RETENTION_LANGUAGES"),
])
def test_dual_packet_tampers_refuse(mutate, match):
    p = _dual_packet()
    mutate(p)
    with pytest.raises(JobRefusal, match=match):
        validate_arm2_semantics(p, p["environment"])


def test_v2_pins_on_a_plain_kd_packet_are_contradictory():
    p = copy.deepcopy(_H3)
    p["result_verifier"]["required_retention_coverage"] = ["pidgin"]
    with pytest.raises(JobRefusal, match="contradictory"):
        validate_arm2_semantics(p, p["environment"])
    p2 = copy.deepcopy(_H3)
    p2["student_init"] = {"mode": "arm1"}
    with pytest.raises(JobRefusal, match="contradictory"):
        validate_arm2_semantics(p2, p2["environment"])


# --------------------------------------------------------------------------
# REAL dual-teacher torch tests (Codex Arm-2b review finding 3) — these run
# in the trainer image test stage (C3); the host skips them without torch.
# --------------------------------------------------------------------------

import importlib.util

_needs_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="torch loss tests execute inside the trainer image (C3)")


class _Layout:
    def __init__(self, seq_lens):
        self.seq_lens = seq_lens


def _dual_batch(rows, frames, vocab, languages, torch):
    return {
        "seqs": torch.zeros(rows, 8),
        "seqs_layout": _Layout([8] * rows),
        "targets": torch.zeros(rows, 2, dtype=torch.long),
        "targets_layout": _Layout([2] * rows),
        "languages": list(languages),
    }


@_needs_torch
def test_dual_loss_forward_backward_torch():
    """The REAL two-term objective: total == ctc + a*kd_base + ar*kd_ret
    numerically, gradient flows through BOTH KD terms, and the metrics sink
    decomposes both."""
    import torch

    from pipeline.omniasr_train import _batch_loss_kd
    from pipeline.omniasr_distill import kd_loss

    torch.manual_seed(7)
    rows, frames, vocab = 4, 5, 6
    languages = ["english", "english", "pidgin", "ewe"]
    param = torch.nn.Parameter(torch.randn(rows, frames, vocab))
    base_logits = torch.randn(rows, frames, vocab)
    ret_logits = torch.randn(rows, frames, vocab)
    lengths = [5, 4, 5, 3]

    def model(seqs, layout, *, targets, targets_layout, return_logits):
        assert return_logits
        return param.pow(2).mean(), param, _Layout(list(lengths))

    def base_teacher(seqs, layout):
        return base_logits, _Layout(list(lengths))

    def ret_teacher(seqs, layout):
        return ret_logits, _Layout(list(lengths))

    sink: dict = {}
    batch = _dual_batch(rows, frames, vocab, languages, torch)
    total = _batch_loss_kd(
        model, batch, teacher=base_teacher, alpha=1.0, temperature=1.0,
        preservation_languages=("english",), language_weights=(),
        known_languages=("english", "pidgin", "ewe"), metrics_sink=sink,
        retention_teacher=ret_teacher, retention_alpha=0.5,
        retention_languages=("pidgin", "ewe"))
    pres_w = [1.0, 1.0, 0.0, 0.0]
    ret_w = [0.0, 0.0, 1.0, 1.0]
    want_kd = kd_loss(param.detach(), base_logits, temperature=1.0,
                      row_weights=pres_w, valid_lengths=lengths)
    want_ret = kd_loss(param.detach(), ret_logits, temperature=1.0,
                       row_weights=ret_w, valid_lengths=lengths)
    # ctc_mean = model_loss / rows (the closure divides by the row count)
    want_total = (param.pow(2).mean().detach() / rows) \
        + 1.0 * want_kd + 0.5 * want_ret
    assert torch.isclose(total.detach(), want_total, rtol=1e-5, atol=1e-6)
    assert sink["kd_retention"] == pytest.approx(float(want_ret), rel=1e-5)
    assert sink["retention_alpha"] == 0.5
    assert set(sink["kd_retention_coverage"]) == {"pidgin", "ewe"}
    assert set(sink["kd_coverage"]) == {"english"}
    total.backward()
    assert param.grad is not None and torch.isfinite(param.grad).all()
    assert param.grad.abs().sum() > 0


@_needs_torch
def test_warm_identical_retention_teacher_gives_zero_kl_torch():
    """Finding 2's numeric ground truth: student logits identical to the
    retention teacher's give EXACTLY zero retention KL — the verifier must
    treat that as legitimate (warm start), which it now does."""
    import torch

    from pipeline.omniasr_distill import kd_loss

    torch.manual_seed(11)
    logits = torch.randn(2, 4, 5)
    kl = kd_loss(logits, logits.clone(), temperature=1.0,
                 row_weights=[1.0, 1.0], valid_lengths=[4, 4])
    assert float(kl) == pytest.approx(0.0, abs=1e-6)


@_needs_torch
def test_dual_loss_mask_contamination_refuses_torch():
    """Belt-and-braces: a language present in BOTH masks refuses at runtime
    even though parse_config already forbids the overlap."""
    import torch

    from pipeline.omniasr_train import _batch_loss_kd
    from pipeline.omniasr_distill import DistillationRefusal

    rows, frames, vocab = 2, 3, 4
    param = torch.nn.Parameter(torch.randn(rows, frames, vocab))

    def model(seqs, layout, *, targets, targets_layout, return_logits):
        return param.sum(), param, _Layout([3, 3])

    def teacher(seqs, layout):
        return torch.randn(rows, frames, vocab), _Layout([3, 3])

    batch = _dual_batch(rows, frames, vocab, ["english", "english"], torch)
    with pytest.raises(DistillationRefusal, match="disjointness violated"):
        _batch_loss_kd(
            model, batch, teacher=teacher, alpha=1.0, temperature=1.0,
            preservation_languages=("english",), language_weights=(),
            known_languages=("english",), metrics_sink=None,
            retention_teacher=teacher, retention_alpha=1.0,
            retention_languages=("english",))


@_needs_torch
def test_load_export_teacher_strict_and_frozen_torch(monkeypatch):
    """load_export_teacher: a full-state export loads strict over the fresh
    base instance and comes back frozen+eval; a partial export refuses."""
    import torch

    from pipeline import omniasr_distill

    def fake_load_teacher(card, device, dtype):
        model = torch.nn.Linear(3, 2)
        model.eval()
        model.requires_grad_(False)
        return model

    monkeypatch.setattr(omniasr_distill, "load_teacher", fake_load_teacher)
    donor = torch.nn.Linear(3, 2)
    teacher = omniasr_distill.load_export_teacher(
        "card", donor.state_dict(), device=None, dtype=None)
    audit = omniasr_distill.teacher_freeze_audit(teacher)
    assert audit["frozen"] is True
    assert not teacher.training
    assert torch.equal(teacher.weight, donor.weight)
    with pytest.raises(Exception):
        omniasr_distill.load_export_teacher(
            "card", {"weight": donor.weight.detach()}, device=None,
            dtype=None)
