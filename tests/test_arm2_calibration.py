"""Arm-2 calibration instrumentation + verifier + packet-semantics + wrapper
tests (Codex reviews #19 F3/F4 and #20 F3/F4/F5). All host-safe: the metrics
accumulator, the result verifier, the launcher cross-check and the wrapper's
orchestration/scoring helpers are pure (no torch), so the acceptance gate and
the evidence binding are exercised off the GPU.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from pipeline.omniasr_train import (CALIBRATION_METRICS_SCHEMA,
                                    CalibrationMetrics)

_REPO = Path(__file__).resolve().parents[1]
_PACKET = (_REPO / "platform/manifests/"
           "B5-UNIVERSAL-ARM2-FTCAL-SAGEMAKER-BINDINGS-2026-001.DRAFT.json")

sys.path.insert(0, str(_REPO / "scripts"))
from b5_sagemaker_job import JobRefusal, validate_arm2_semantics  # noqa: E402
from verify_arm2_calibration import (CANONICAL_SCORER,  # noqa: E402
                                     load_verifier_spec, verify_calibration)

_JOB_NAME = "medzen-b5-b5-universal-arm2-ftcal-2026-001"


# --------------------------------------------------------------------------
# helpers: build a fully-valid metrics artifact through the real code paths
# --------------------------------------------------------------------------

def _spec() -> dict:
    return json.loads(_PACKET.read_bytes())["result_verifier"]


# the PREDECLARED dev-slice shas (Codex #21 F4: identity must equal these)
_DEV_SHAS = {
    lang: json.loads(_PACKET.read_bytes())["result_verifier"]
    ["dev_manifests"][lang]["sha256"]
    for lang in ("lingala", "swahili")
}


def _identity() -> dict:
    return {
        "run_fingerprint": "f" * 64,
        "training_job_name": _JOB_NAME,
        "export_manifest_sha256": "a" * 64,
        "export_model_sha256": "b" * 64,
        "dev_manifest_shas": dict(_DEV_SHAS),
        "scorer": CANONICAL_SCORER,
        "packet_sha256": "e" * 64,
        "verifier_script_sha256": "9" * 64,
    }


def test_wrapper_scorer_matches_the_verifier_canonical():
    """Codex review #20 F5 follow-up: the wrapper's stamped scorer and the
    verifier's canonical scorer must stay byte-identical, or every real run
    fails the scorer check."""
    from pipeline.omniasr_calibrate import SCORER_ID
    assert SCORER_ID == CANONICAL_SCORER


def _good_artifact(steps: int = 30) -> dict:
    metrics = CalibrationMetrics()
    for step in range(1, steps + 1):
        metrics.record_micro({
            "ctc": 1.0, "kd": 0.4, "total": 1.0 + 0.5 * 0.4, "alpha": 0.5,
            "kd_coverage": {lang: {"rows": 1, "frames": 5}
                            for lang in ("english", "french",
                                         "swahili", "lingala")}})
        metrics.commit_step(step, lr=1e-5)
    return metrics.finalize(
        status="COMPLETED", steps_completed=steps, max_steps=steps,
        peak_gpu_bytes=10_000_000_000, wall_seconds=120.0,
        samples_per_step=16, identity=_identity(),
        serve={"readyz": True, "adapter_residue": False, "weights_finite": True},
        dev_sentinel_wer={"lingala": 0.18, "swahili": 0.13})


# --------------------------------------------------------------------------
# F3 (a): the metrics accumulator produces the verifier's artifact schema
# --------------------------------------------------------------------------

def test_calibration_metrics_builds_the_verifier_schema():
    artifact = _good_artifact(steps=2)
    assert artifact["schema"] == CALIBRATION_METRICS_SCHEMA
    assert len(artifact["per_step"]) == 2
    assert artifact["per_step"][0]["kd"] == 0.4
    assert artifact["per_step"][0]["alpha"] == 0.5
    assert artifact["step_sequence"] == [1, 2]
    assert artifact["kd_positive_finite_steps"] == 2
    assert artifact["kd_coverage"]["english"] == {"rows": 2, "frames": 10}
    assert artifact["throughput"]["steps_per_min"] > 0
    assert artifact["throughput"]["samples_per_sec"] > 0  # Codex #20 F5


def test_metrics_resume_restores_full_trajectory():
    """Codex review #20 F5: the accumulator restarted empty on resume. state
    round-trips so per_step keeps every recorded step."""
    m1 = CalibrationMetrics()
    for step in (1, 2):
        m1.record_micro({"ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
                         "kd_coverage": {"lingala": {"rows": 1, "frames": 5}}})
        m1.commit_step(step, lr=1e-5)
    state = json.loads(json.dumps(m1.to_state()))  # survives a JSON checkpoint
    m2 = CalibrationMetrics()
    m2.restore(state)
    for step in (3, 4):
        m2.record_micro({"ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
                         "kd_coverage": {"lingala": {"rows": 1, "frames": 5}}})
        m2.commit_step(step, lr=1e-5)
    art = m2.finalize(status="COMPLETED", steps_completed=4, max_steps=4,
                      peak_gpu_bytes=1, wall_seconds=1.0, samples_per_step=16)
    assert art["step_sequence"] == [1, 2, 3, 4]
    assert art["kd_coverage"]["lingala"] == {"rows": 4, "frames": 20}


# --------------------------------------------------------------------------
# F3 (b): the verifier PASSES a clean run and FAILS every defect
# --------------------------------------------------------------------------

def test_verifier_passes_a_clean_calibration():
    assert verify_calibration(_good_artifact(), _spec()) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda a: a.update(status="TRAINING_DIVERGED_NONFINITE"), "COMPLETED"),
    (lambda a: a.update(steps_completed=29), "steps_completed"),
    (lambda a: a["per_step"].__setitem__(0, {**a["per_step"][0], "kd": 0.0}),
     "not > 0"),
    (lambda a: a["per_step"].__setitem__(
        0, {**a["per_step"][0], "kd": float("nan")}), "finite"),
    (lambda a: a["per_step"].__setitem__(
        0, {**a["per_step"][0], "total": 9.9}), "loss equation"),
    (lambda a: a["per_step"].__setitem__(
        0, {**a["per_step"][0], "step": 99}), "contiguous"),
    (lambda a: a.update(kd_positive_finite_steps=29), "positive-and-finite"),
    (lambda a: a["kd_coverage"].pop("lingala"), "lingala"),
    (lambda a: a["kd_coverage"].__setitem__("french", {"rows": 0, "frames": 0}),
     "0 KD rows"),
    (lambda a: a.update(peak_gpu_bytes=None), "peak_gpu_bytes"),
    (lambda a: a.update(peak_gpu_bytes=99_000_000_000), "exceeds"),
    (lambda a: a["throughput"].update(steps_per_min=0), "steps_per_min"),
    (lambda a: a["throughput"].update(samples_per_sec=0), "samples_per_sec"),
    (lambda a: a.update(serve=None), "readyz"),
    (lambda a: a["serve"].update(adapter_residue=True), "adapter_residue"),
    (lambda a: a.update(dev_sentinel_wer=None), "dev_sentinel_wer"),
    (lambda a: a["dev_sentinel_wer"].pop("swahili"), "swahili"),
    (lambda a: a.update(identity=None), "identity block is absent"),
    (lambda a: a["identity"].update(run_fingerprint=""), "run_fingerprint"),
    (lambda a: a["identity"].update(scorer=""), "scorer"),
    (lambda a: a["identity"]["dev_manifest_shas"].pop("lingala"),
     "dev_manifest_shas"),
    # Codex review #20 F5 follow-up: presence-only let fabricated fields pass
    (lambda a: a["identity"].update(run_fingerprint="deadbeef"), "not a 64-hex"),
    (lambda a: a["identity"].update(export_model_sha256="deadbeef"), "not a 64-hex"),
    (lambda a: a["identity"]["dev_manifest_shas"].update(lingala="fake"),
     "not a 64-hex"),
    # Codex review #21 F4: a well-formed but UNDECLARED dev-slice sha fails —
    # the WER must come from the exact reviewed frozen slice
    (lambda a: a["identity"]["dev_manifest_shas"].update(lingala="0" * 64),
     "PREDECLARED"),
    (lambda a: a["identity"].update(scorer="made-up-scorer"), "canonical scorer"),
])
def test_verifier_fails_each_defect(mutate, needle):
    artifact = _good_artifact()
    mutate(artifact)
    failures = verify_calibration(artifact, _spec())
    assert failures, f"expected a failure for {needle}"
    assert any(needle in f for f in failures), (needle, failures)


def test_fabricated_metrics_that_never_ran_is_rejected():
    """The adversarial pass' HIGH finding: a hand-built metrics file that names
    a wrong job / made-up scorer / malformed export sha must be rejected once
    the derivable identities are cross-checked (job name from the packet)."""
    art = _good_artifact()
    art["identity"].update(
        run_fingerprint="FABRICATED-never-ran",           # not 64-hex
        training_job_name="FABRICATED-no-such-job",        # != derived
        export_manifest_sha256="deadbeef",                 # not 64-hex
        scorer="made-up")                                  # != canonical
    failures = verify_calibration(art, _spec(),
                                  expected_job_name=_JOB_NAME)
    assert any("training_job_name" in f for f in failures)
    assert any("canonical scorer" in f for f in failures)
    assert any("not a 64-hex" in f for f in failures)


def test_job_name_must_match_the_packet_derived_name():
    art = _good_artifact()
    ok = verify_calibration(art, _spec(), expected_job_name=_JOB_NAME)
    assert ok == []
    art["identity"]["training_job_name"] = "medzen-b5-some-other-job"
    bad = verify_calibration(art, _spec(), expected_job_name=_JOB_NAME)
    assert any("training_job_name" in f for f in bad)


def test_export_binding_rejects_a_fabrication_that_never_exported():
    """Codex review #20 F5 follow-up (the adversary's residual): a competent
    fabrication with correct derivable identities and arbitrary-but-64-hex
    export shas passes the shape/identity SMOKE, but the authoritative run —
    which binds the export shas to the real S3-fetched manifest — rejects it."""
    art = _good_artifact()  # export shas are "a"*64 / "b"*64
    # smoke (no authenticated_export): passes shape/identity
    assert verify_calibration(art, _spec()) == []
    # authoritative: the real export declares DIFFERENT shas -> reject
    authentic = {"manifest_sha256": "1" * 64, "model_sha256": "2" * 64}
    bad = verify_calibration(art, _spec(), authenticated_export=authentic)
    assert any("export_manifest_sha256" in f for f in bad)
    assert any("export_model_sha256" in f for f in bad)
    # when the metrics DO match the real export, the binding passes
    matching = {"manifest_sha256": "a" * 64, "model_sha256": "b" * 64}
    assert verify_calibration(art, _spec(), authenticated_export=matching) == []


def test_empty_required_coverage_cannot_defang_the_verifier():
    """Codex review #20 F4 defense-in-depth: the standalone verifier must
    reject an empty required_preservation_coverage, not skip the KD check."""
    packet = json.loads(_PACKET.read_bytes())
    packet["result_verifier"]["required_preservation_coverage"] = []
    with pytest.raises(SystemExit, match="non-empty list"):
        load_verifier_spec(packet)
    # and verify_calibration itself fails closed if handed such a spec
    spec = {**_spec(), "required_preservation_coverage": []}
    failures = verify_calibration(_good_artifact(), spec)
    assert any("cannot be defanged" in f for f in failures)


def test_verifier_binds_packet_and_verifier_sha():
    """Codex review #20 F5: metrics produced under a DIFFERENT packet or a
    DIFFERENT verifier are rejected."""
    art = _good_artifact()
    ok = verify_calibration(art, _spec(),
                            packet_canonical_sha="e" * 64,
                            verifier_script_sha="9" * 64)
    assert ok == []
    bad_pkt = verify_calibration(art, _spec(),
                                 packet_canonical_sha="0" * 64,
                                 verifier_script_sha="9" * 64)
    assert any("packet_sha256" in f for f in bad_pkt)
    bad_ver = verify_calibration(art, _spec(),
                                 packet_canonical_sha="e" * 64,
                                 verifier_script_sha="0" * 64)
    assert any("verifier_script_sha256" in f for f in bad_ver)


# --------------------------------------------------------------------------
# F4 (verifier-side): load_verifier_spec pins the CANONICAL contract — the
# exact bypass set Codex reproduced must now be rejected
# --------------------------------------------------------------------------

def test_load_verifier_spec_accepts_the_committed_packet():
    assert load_verifier_spec(json.loads(_PACKET.read_bytes()))


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(script="scripts/always_pass.py"), "canonical"),
    (lambda s: s.update(metrics_artifact="../../fake.json"), "traversal"),
    (lambda s: s.update(expected_steps=0), "positive int"),
    (lambda s: s.update(gpu_memory_ceiling_bytes=10 ** 15), "physical"),
    (lambda s: s.update(dev_sentinel_languages=[]), "non-empty"),
    (lambda s: s.update(dev_sentinel_languages=["french"]), "regression sentinels"),
    # Codex review #21 F4: undeclared dev data refuses
    (lambda s: s.pop("dev_manifests"), "dev_manifests"),
    (lambda s: s["dev_manifests"]["lingala"].update(sha256="deadbeef"),
     "64-hex"),
    (lambda s: s["dev_manifests"]["lingala"].update(path="../../escape.jsonl"),
     "traversal"),
    (lambda s: s["dev_manifests"]["lingala"].update(rows=0), "positive int"),
])
def test_load_verifier_spec_rejects_bypass_specs(mutate, needle):
    packet = json.loads(_PACKET.read_bytes())
    mutate(packet["result_verifier"])
    with pytest.raises(SystemExit) as exc:
        load_verifier_spec(packet)
    assert needle in str(exc.value), (needle, str(exc.value))


def test_expected_steps_one_is_caught_by_the_metrics_cross_check():
    """expected_steps=1 while the run does 30 is caught at verify time via the
    metrics max_steps/steps_completed cross-check (the launcher also pins it to
    MEDZEN_MAX_STEPS) — not by load_verifier_spec, which can't know the budget."""
    spec = _spec()
    spec = {**spec, "expected_steps": 1}
    failures = verify_calibration(_good_artifact(steps=30), spec)
    assert any("expected 1" in f or "!= expected 1" in f for f in failures)


# --------------------------------------------------------------------------
# F4 (launcher-side): validate_arm2_semantics cross-check + canonical contract
# --------------------------------------------------------------------------

def _packet_and_env():
    packet = json.loads(_PACKET.read_bytes())
    env = dict(packet["environment"])
    # the launcher injects this at render time; emulate for the unit test
    env["MEDZEN_CALIBRATION_PACKET_SHA256"] = "a" * 64
    return packet, env


def test_arm2_semantics_accepts_the_committed_packet():
    packet, env = _packet_and_env()
    validate_arm2_semantics(packet, env)  # no raise


@pytest.mark.parametrize("mutate,needle", [
    (lambda b, e: b["distillation"].update(kd_alpha=0.9), "kd_alpha"),
    (lambda b, e: e.update(MEDZEN_KD_TEMPERATURE="2.0"), "kd_temperature"),
    (lambda b, e: b.update(acceptance_criteria=[]), "acceptance_criteria"),
    (lambda b, e: b.pop("result_verifier"), "result_verifier"),
    (lambda b, e: b["result_verifier"].update(script="scripts/always_pass.py"),
     "must be"),
    (lambda b, e: b["result_verifier"].update(metrics_artifact="../../x.json"),
     "traversal"),
    (lambda b, e: b["result_verifier"].update(expected_steps=1),
     "expected_steps"),
    (lambda b, e: b["result_verifier"].update(gpu_memory_ceiling_bytes=10 ** 15),
     "physical"),
    (lambda b, e: b["result_verifier"].update(dev_sentinel_languages=["french"]),
     "regression sentinels"),
    (lambda b, e: e.update(MEDZEN_KD_LANGUAGE_WEIGHTS="lingala=9.0"),
     "language_weights"),
    (lambda b, e: e.pop("MEDZEN_CALIBRATION_PACKET_SHA256"),
     "MEDZEN_CALIBRATION_PACKET_SHA256"),
    (lambda b, e: e.pop("MEDZEN_DEV_SENTINEL_MANIFEST_FILES"),
     "MEDZEN_DEV_SENTINEL_MANIFEST_FILES"),
    (lambda b, e: e.update(MEDZEN_DEV_SENTINEL_MANIFEST_FILES="lingala=x.jsonl"),
     "slice for every"),
    (lambda b, e: b.pop("distillation"), "no top-level `distillation`"),
    (lambda b, e: e.update(MEDZEN_KD_ENABLE="0"), "disagree about whether"),
    # Codex review #21 F6: the exact reproduction — ["PASS"] must refuse
    (lambda b, e: b.update(acceptance_criteria=["PASS"]),
     "canonical machine-derived"),
    # Codex review #21 F4: undeclared / drifted dev data refuses
    (lambda b, e: b["result_verifier"].pop("dev_manifests"), "dev_manifests"),
    (lambda b, e: b["result_verifier"]["dev_manifests"]["lingala"].update(
        sha256="0" * 64), "drifted from its declaration"),
    (lambda b, e: b["result_verifier"]["dev_manifests"]["lingala"].update(
        rows=7), "declares"),
    (lambda b, e: e.update(
        MEDZEN_DEV_SENTINEL_MANIFEST_FILES="lingala=other/path.jsonl,"
        "swahili=platform/manifests/dev-sentinels/swahili.jsonl"),
     "predeclared"),
])
def test_arm2_semantics_refuses_internal_contradiction(mutate, needle):
    packet, env = _packet_and_env()
    b, e = copy.deepcopy(packet), copy.deepcopy(env)
    mutate(b, e)
    with pytest.raises(JobRefusal) as exc:
        validate_arm2_semantics(b, e)
    assert needle in str(exc.value), (needle, str(exc.value))


def test_arm2_semantics_is_noop_without_kd():
    validate_arm2_semantics({}, {"MEDZEN_KD_ENABLE": "0"})


# --------------------------------------------------------------------------
# F3 wrapper: pure orchestration/scoring helpers (host-safe)
# --------------------------------------------------------------------------

def test_word_error_rate_matches_known_cases():
    from pipeline.omniasr_calibrate import word_error_rate
    assert word_error_rate(["a b c"], ["a b c"]) == 0.0          # perfect
    assert word_error_rate(["a b c d"], ["a x c d"]) == 0.25     # 1 sub / 4
    # corpus-level: edits summed over refs / total ref words
    assert word_error_rate(["a b", "c d"], ["a b", "c x"]) == 0.25


def test_parse_dev_manifest_files_and_traversal_guard():
    from pipeline.omniasr_calibrate import (CalibrationRefusal,
                                            parse_dev_manifest_files)
    got = parse_dev_manifest_files("lingala=a/l.jsonl,swahili=a/s.jsonl")
    assert got == {"lingala": "a/l.jsonl", "swahili": "a/s.jsonl"}
    for bad in ("lingala=/abs/l.jsonl", "lingala=../escape.jsonl", "noeq"):
        with pytest.raises(CalibrationRefusal):
            parse_dev_manifest_files(bad)


def test_build_identity_and_patch_metrics(tmp_path):
    from pipeline.omniasr_calibrate import build_identity, patch_metrics
    identity = build_identity(
        run_fingerprint="f" * 64, training_job_name="job",
        export={"manifest_sha256": "a" * 64, "checkpoint_sha256": "b" * 64},
        dev_manifest_shas={"lingala": "c" * 64, "swahili": "d" * 64},
        packet_sha256="e" * 64, verifier_script_sha256="9" * 64)
    assert identity["export_model_sha256"] == "b" * 64
    # patch merges into the trainer's artifact (must already exist)
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps({"schema": "x", "serve": None}).encode())
    merged = patch_metrics(
        metrics_path, serve={"readyz": True}, dev_sentinel_wer={"lingala": 0.1},
        identity=identity)
    assert merged["serve"] == {"readyz": True}
    assert merged["identity"]["run_fingerprint"] == "f" * 64
    from pipeline.omniasr_calibrate import CalibrationRefusal
    with pytest.raises(CalibrationRefusal):
        patch_metrics(tmp_path / "missing.json", serve={}, dev_sentinel_wer={},
                      identity=identity)


def test_load_export_weights_fails_closed_on_a_non_mapping_export(tmp_path):
    """Codex review #20 F3 follow-up: strict=False silently loaded nothing when
    the export keys did not map, so readyz reported healthy BASE weights. Now a
    missing/unexpected key refuses."""
    torch = pytest.importorskip("torch")
    from pipeline.omniasr_calibrate import CalibrationRefusal, _load_export_weights
    model = torch.nn.Linear(4, 4)
    # happy path: the model's OWN state dict maps cleanly
    good = tmp_path / "good.pt"
    torch.save({"model": model.state_dict()}, good)
    _load_export_weights(model, good)  # no raise
    # a checkpoint whose keys don't map must fail closed, not silently no-op
    bad = tmp_path / "bad.pt"
    torch.save({"model": {"totally.different.key": torch.zeros(3)}}, bad)
    with pytest.raises(CalibrationRefusal, match="did not map onto the model"):
        _load_export_weights(model, bad)


def test_committed_dev_manifests_match_declaration_and_selection_record():
    """Codex review #21 F4: the committed dev-sentinel slices must match the
    packet's predeclaration (path/sha256/rows) AND every row must be copied
    VERBATIM from the frozen dev-selection record — no invented rows."""
    import hashlib
    sel = json.loads((_REPO / "platform/manifests/"
                      "B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json").read_bytes())
    sel_rows = {(r["language"], r["audio_checksum_sha256"],
                 r["audio_s3_uri"], r["reference"]) for r in sel["rows"]}
    decl = json.loads(_PACKET.read_bytes())["result_verifier"]["dev_manifests"]
    for lang in ("lingala", "swahili"):
        d = decl[lang]
        body = (_REPO / d["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == d["sha256"]
        rows = [json.loads(line) for line in body.decode().splitlines()
                if line.strip()]
        assert len(rows) == d["rows"] == 60
        assert d["source_record"] == "B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001"
        for row in rows:
            assert row["language"] == lang
            assert row["selection_record"] == d["source_record"]
            key = (row["language"], row["audio_checksum_sha256"],
                   row["audio_filepath"], row["text_normalized"])
            assert key in sel_rows, f"row not in the frozen selection: {key[:2]}"


def test_packet_criteria_equal_the_canonical_machine_derived_list():
    """Codex review #21 F6: the committed packet's prose criteria are exactly
    the machine-derived canonical list — drift is impossible."""
    from b5_sagemaker_job import arm2_acceptance_criteria
    packet = json.loads(_PACKET.read_bytes())
    assert packet["acceptance_criteria"] == \
        arm2_acceptance_criteria(packet["result_verifier"])


def _good_receipt():
    from verify_arm2_calibration import _canonical_sha256
    packet = json.loads(_PACKET.read_bytes())
    env = dict(packet["environment"])
    env["MEDZEN_CALIBRATION_PACKET_SHA256"] = _canonical_sha256(packet)
    env["MEDZEN_TRAINING_JOB_NAME"] = _JOB_NAME
    return packet, {
        "TrainingJobName": _JOB_NAME,
        "TrainingJobStatus": "Completed",
        "AlgorithmSpecification": {
            "TrainingImage": packet["image_uri_with_digest"],
            "ContainerArguments": ["-m", "pipeline.omniasr_calibrate"],
        },
        "Environment": env,
        "OutputDataConfig": {
            "KmsKeyId": packet["kms_key_arn"],
            "S3OutputPath": "s3://medzen-speech/research/b5-training/"
                            "b5-universal-arm2-ftcal-2026-001/output",
        },
        "ResourceConfig": {"InstanceType": packet["instance_type"]},
        "StoppingCondition": {
            "MaxRuntimeInSeconds": packet["max_runtime_seconds"]},
        "EnableManagedSpotTraining": packet["managed_spot"],
    }


def test_training_receipt_verifies_and_each_drift_fails():
    """Codex review #21 F3: terminal status, image digest, environment, KMS,
    output location and instance are machine-checked against the packet."""
    from verify_arm2_calibration import (_canonical_sha256,
                                         verify_training_receipt)
    packet, receipt = _good_receipt()
    sha = _canonical_sha256(packet)
    assert verify_training_receipt(
        receipt, packet, expected_job_name=_JOB_NAME,
        packet_canonical_sha=sha) == []
    drifts = [
        (lambda r: r.update(TrainingJobStatus="InProgress"), "Completed"),
        (lambda r: r.update(TrainingJobName="medzen-b5-other"), "derived"),
        (lambda r: r["AlgorithmSpecification"].update(
            TrainingImage="x@sha256:" + "1" * 64), "pinned digest"),
        (lambda r: r["AlgorithmSpecification"].update(
            ContainerArguments=["-m", "pipeline.omniasr_train"]),
         "calibration entrypoint"),
        (lambda r: r["Environment"].update(MEDZEN_KD_ALPHA="0.9"),
         "Environment differs"),
        (lambda r: r["Environment"].pop("MEDZEN_TRAINING_JOB_NAME"),
         "Environment differs"),
        (lambda r: r["OutputDataConfig"].update(KmsKeyId="arn:aws:kms:x"),
         "KMS"),
        (lambda r: r["OutputDataConfig"].update(
            S3OutputPath="s3://elsewhere/x"), "S3OutputPath"),
        (lambda r: r["ResourceConfig"].update(InstanceType="ml.p4d.24xlarge"),
         "instance"),
        (lambda r: r["StoppingCondition"].update(MaxRuntimeInSeconds=99999),
         "MaxRuntimeInSeconds"),
        (lambda r: r.update(EnableManagedSpotTraining=True), "Spot"),
    ]
    for mutate, needle in drifts:
        _, bad = _good_receipt()
        mutate(bad)
        failures = verify_training_receipt(
            bad, packet, expected_job_name=_JOB_NAME, packet_canonical_sha=sha)
        assert failures, f"expected a failure for {needle}"
        assert any(needle in f for f in failures), (needle, failures)


def test_authoritative_mode_requires_all_three_fetched_inputs(tmp_path):
    """Codex review #21 F3: --export-manifest alone is not authoritative; the
    model bytes and the SageMaker receipt are required too."""
    from verify_arm2_calibration import main as verifier_main
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps(_good_artifact()).encode())
    with pytest.raises(SystemExit, match="export-model"):
        verifier_main(["--metrics", str(metrics_path),
                       "--packet", str(_PACKET)])
    with pytest.raises(SystemExit, match="export-model"):
        verifier_main(["--metrics", str(metrics_path),
                       "--packet", str(_PACKET),
                       "--export-manifest", str(metrics_path)])


def test_wall_time_accumulates_across_resume():
    """Codex review #21 F5: throughput must divide by CUMULATIVE wall time,
    not just the resumed process's runtime."""
    m1 = CalibrationMetrics()
    m1.record_micro({"ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
                     "kd_coverage": {"lingala": {"rows": 1, "frames": 5}}})
    m1.commit_step(1, lr=1e-5)
    state = m1.to_state()
    assert state["wall_seconds"] >= 0.0
    state["wall_seconds"] = 100.0            # pre-reclaim elapsed
    m2 = CalibrationMetrics()
    m2.restore(state)
    m2.record_micro({"ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
                     "kd_coverage": {"lingala": {"rows": 1, "frames": 5}}})
    m2.commit_step(2, lr=1e-5)
    art = m2.finalize(status="COMPLETED", steps_completed=2, max_steps=2,
                      peak_gpu_bytes=1, wall_seconds=20.0, samples_per_step=16)
    assert 119.0 < art["throughput"]["wall_seconds"] < 121.0
    # 2 steps over ~120s, not 2 steps over 20s
    assert art["throughput"]["steps_per_min"] < 1.5


def test_draft_packet_refuses_to_launch(tmp_path):
    """Codex review #21 rec 6: a DRAFT packet renders and validates but must
    NEVER launch."""
    import copy as _copy
    import subprocess
    packet = json.loads(_PACKET.read_bytes())
    launchable = _copy.deepcopy(packet)
    # give it a well-formed (fake) digest so render/validate succeed; keep
    # DRAFT_STATUS so the launch gate must fire
    uri = ("558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
           "medzen-trainer-omniasr@sha256:" + "0" * 64)
    launchable["image_uri_with_digest"] = uri
    launchable["image_oci_index_digest"] = uri
    bindings = tmp_path / "packet.DRAFT.json"
    bindings.write_bytes(json.dumps(launchable, sort_keys=True).encode())
    render = subprocess.run(
        [sys.executable, str(_REPO / "scripts/b5_sagemaker_job.py"),
         "render", "--bindings", str(bindings)],
        capture_output=True, text=True, cwd=_REPO)
    assert render.returncode == 0, render.stderr
    request = tmp_path / "request.json"
    request.write_text(render.stdout)
    launch = subprocess.run(
        [sys.executable, str(_REPO / "scripts/b5_sagemaker_job.py"),
         "launch", "--bindings", str(bindings), "--request", str(request)],
        capture_output=True, text=True, cwd=_REPO)
    assert launch.returncode != 0
    assert "DRAFT" in (launch.stdout + launch.stderr)


def test_run_verifier_smoke_passes_on_a_clean_artifact(tmp_path):
    """The wrapper's in-image smoke: writes a clean artifact and the canonical
    verifier returns 0. bind_packet_sha=False (the in-image packet is DRAFT)."""
    from pipeline.omniasr_calibrate import run_verifier
    art = _good_artifact()
    # verifier_script_sha256 must match the ACTUAL in-repo verifier bytes for
    # the smoke's self-bind; recompute and stamp it
    import hashlib
    vsha = hashlib.sha256(
        (_REPO / "scripts/verify_arm2_calibration.py").read_bytes()).hexdigest()
    art["identity"]["verifier_script_sha256"] = vsha
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps(art).encode())
    assert run_verifier(metrics_path, _PACKET, bind_packet_sha=False) == 0
