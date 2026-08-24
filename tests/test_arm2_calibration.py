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
_CONTRACT = (_REPO / "platform/manifests/"
             "B5-UNIVERSAL-ARM2-FTCAL-EXECUTION-CONTRACT-2026-001.json")

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


import hashlib as _hashlib

_CONTRACT_SHA = _hashlib.sha256(_CONTRACT.read_bytes()).hexdigest()


def _identity() -> dict:
    return {
        "run_fingerprint": "f" * 64,
        "training_job_name": _JOB_NAME,
        "export_manifest_sha256": "a" * 64,
        "export_model_sha256": "b" * 64,
        "dev_manifest_shas": dict(_DEV_SHAS),
        "scorer": CANONICAL_SCORER,
        "packet_sha256": "e" * 64,
        "execution_contract_sha256": _CONTRACT_SHA,
        "verifier_script_sha256": "9" * 64,
    }


def test_wrapper_scorer_matches_the_verifier_canonical():
    """Codex review #20 F5 follow-up: the wrapper's stamped scorer and the
    verifier's canonical scorer must stay byte-identical, or every real run
    fails the scorer check."""
    from pipeline.omniasr_calibrate import SCORER_ID
    assert SCORER_ID == CANONICAL_SCORER


def _good_artifact_base(steps: int = 30) -> dict:
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


def _first_checksum(language: str) -> str:
    decl = json.loads(_PACKET.read_bytes())["result_verifier"]["dev_manifests"]
    first = (_REPO / decl[language]["path"]).read_text().splitlines()[0]
    return json.loads(first)["audio_checksum_sha256"]


def _good_artifact(steps: int = 30) -> dict:  # noqa: F811 (parity-aware)
    art = _good_artifact_base(steps)
    art["parity"] = {
        "upstream_equal": True,
        "upstream": ("omnilingual_asr.models.inference.pipeline."
                     "ASRInferencePipeline@145a12a6"),
        "rows_checked": {"lingala": 1, "swahili": 1},
        "rows": {lang: [{"audio_checksum_sha256": _first_checksum(lang),
                         "hyp_sha256": _hashlib.sha256(lang.encode()).hexdigest()}]
                 for lang in ("lingala", "swahili")},
        "scorer": CANONICAL_SCORER,
    }
    return art


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
    # Codex #25 finding 2: parity is mandatory, fail-never-skip
    (lambda a: a.pop("parity"), "parity block is absent"),
    (lambda a: a["parity"].update(upstream_equal=False), "upstream_equal"),
    (lambda a: a["parity"].update(upstream="something-else"), "omnilingual"),
    (lambda a: a["parity"]["rows_checked"].pop("swahili"),
     "parity checked no rows"),
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
    # the launcher-injected identity keys are validated via the RENDERED env
    # elsewhere; validate_arm2_semantics reads the committed env directly
    env = dict(packet["environment"])
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
    (lambda b, e: e.pop("MEDZEN_DEV_SENTINEL_MANIFEST_FILES"),
     "MEDZEN_DEV_SENTINEL_MANIFEST_FILES"),
    # Codex #22 blocker 2: the execution-contract binding refuses when absent,
    # drifted, block-divergent, or when the committed env pre-defines the
    # launcher-injected identity keys
    (lambda b, e: b.pop("execution_contract"), "execution_contract"),
    (lambda b, e: b["execution_contract"].update(sha256="0" * 64), "drifted"),
    (lambda b, e: b["distillation"].update(teacher_note="patched"),
     "differs from the launch packet"),
    (lambda b, e: b["environment"].update(MEDZEN_TRAINING_JOB_NAME="x"),
     "must not pre-define"),
    (lambda b, e: e.update(MEDZEN_DEV_SENTINEL_MANIFEST_FILES="lingala=x.jsonl"),
     "slice for every"),
    (lambda b, e: b.pop("distillation"), "no top-level `distillation`"),
    (lambda b, e: e.update(MEDZEN_KD_ENABLE="0"), "disagree about whether"),
    # Codex review #21 F6: the exact reproduction — ["PASS"] must refuse
    (lambda b, e: b.update(acceptance_criteria=["PASS"]),
     "canonical machine-derived"),
    # Codex review #21 F4: undeclared / drifted dev data refuses
    # spec mutations now diverge from the baked contract FIRST (Codex #22
    # blocker 2): the contract-equality gate refuses before the per-field
    # dev-manifest checks (which load_verifier_spec + the provenance test
    # still cover directly)
    (lambda b, e: b["result_verifier"].pop("dev_manifests"),
     "differs from the launch packet"),
    (lambda b, e: b["result_verifier"]["dev_manifests"]["lingala"].update(
        sha256="0" * 64), "differs from the launch packet"),
    (lambda b, e: b["result_verifier"]["dev_manifests"]["lingala"].update(
        rows=7), "differs from the launch packet"),
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
        packet_sha256="e" * 64, execution_contract_sha256="7" * 64,
        verifier_script_sha256="9" * 64)
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


def _launchable_packet():
    """The committed DRAFT with a well-formed (fake) digest so render_request
    succeeds — receipt/live tests verify against the launcher's OWN render."""
    packet = json.loads(_PACKET.read_bytes())
    uri = ("558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
           "medzen-trainer-omniasr@sha256:" + "0" * 64)
    packet["image_uri_with_digest"] = uri
    packet["image_oci_index_digest"] = uri
    return packet


def _good_receipt():
    """A receipt derived from the launcher's OWN rendered request (Codex #23:
    the verifier compares the COMPLETE canonical request, so the fixture must
    be built from the same source of truth)."""
    from b5_sagemaker_job import render_request
    packet = _launchable_packet()
    request = render_request(packet)
    receipt = {
        "TrainingJobName": request["TrainingJobName"],
        "TrainingJobStatus": "Completed",
        "RoleArn": request["RoleArn"],
        "EnableNetworkIsolation": request["EnableNetworkIsolation"],
        "AlgorithmSpecification": json.loads(
            json.dumps(request["AlgorithmSpecification"])),
        "Environment": dict(request["Environment"]),
        "VpcConfig": json.loads(json.dumps(request["VpcConfig"])),
        "OutputDataConfig": dict(request["OutputDataConfig"]),
        "ResourceConfig": dict(request["ResourceConfig"]),
        "CheckpointConfig": dict(request["CheckpointConfig"]),
        "StoppingCondition": dict(request["StoppingCondition"]),
        "EnableManagedSpotTraining": request["EnableManagedSpotTraining"],
        "EnableInterContainerTrafficEncryption":
            request["EnableInterContainerTrafficEncryption"],
        "ProfilerConfig": dict(request["ProfilerConfig"]),
        "RemoteDebugConfig": dict(request["RemoteDebugConfig"]),
        "Tags": json.loads(json.dumps(request["Tags"])),
    }
    return packet, receipt


def test_training_receipt_verifies_and_each_drift_fails():
    """Codex #21 F3 + #23 critical: the COMPLETE canonical request is
    machine-checked — including the exact adversarial reproductions (wrong
    ContainerEntrypoint, Pipe input mode, injected VolumeKmsKeyId, wrong
    checkpoint LocalPath, Tags drift)."""
    from verify_arm2_calibration import (_canonical_sha256,
                                         verify_training_receipt)
    packet, receipt = _good_receipt()
    sha = _canonical_sha256(packet)
    assert verify_training_receipt(
        receipt, packet, expected_job_name=_JOB_NAME,
        packet_canonical_sha=sha) == []
    drifts = [
        (lambda r: r.update(TrainingJobStatus="InProgress"), "Completed"),
        (lambda r: r.update(TrainingJobName="medzen-b5-other"),
         "TrainingJobName"),
        (lambda r: r["AlgorithmSpecification"].update(
            TrainingImage="x@sha256:" + "1" * 64), "TrainingImage"),
        (lambda r: r["AlgorithmSpecification"].update(
            ContainerArguments=["-m", "pipeline.omniasr_train"]),
         "ContainerArguments"),
        # Codex #23 reproductions — previously ALL passed:
        (lambda r: r["AlgorithmSpecification"].update(
            ContainerEntrypoint=["/bin/sh", "-c"]), "ContainerEntrypoint"),
        (lambda r: r["AlgorithmSpecification"].update(
            TrainingInputMode="Pipe"), "TrainingInputMode"),
        (lambda r: r["ResourceConfig"].update(
            VolumeKmsKeyId="arn:aws:kms:evil"), "never set"),
        (lambda r: r["CheckpointConfig"].update(LocalPath="/tmp/elsewhere"),
         "LocalPath"),
        (lambda r: r["Tags"].__setitem__(
            0, {"Key": r["Tags"][0]["Key"], "Value": "forged"}), "Tags"),
        (lambda r: r.pop("Tags"), "Tags absent"),
        (lambda r: r["Environment"].update(MEDZEN_KD_ALPHA="0.9"),
         "Environment differs"),
        (lambda r: r["Environment"].pop("MEDZEN_EXECUTION_CONTRACT_SHA256"),
         "Environment differs"),
        (lambda r: r["OutputDataConfig"].update(KmsKeyId="arn:aws:kms:x"),
         "KmsKeyId"),
        (lambda r: r["OutputDataConfig"].update(
            S3OutputPath="s3://elsewhere/x"), "S3OutputPath"),
        (lambda r: r["ResourceConfig"].update(InstanceType="ml.p4d.24xlarge"),
         "InstanceType"),
        (lambda r: r["ResourceConfig"].update(InstanceCount=2), "InstanceCount"),
        (lambda r: r["ResourceConfig"].update(VolumeSizeInGB=50),
         "VolumeSizeInGB"),
        (lambda r: r["CheckpointConfig"].update(S3Uri="s3://elsewhere/ckpt"),
         "CheckpointConfig"),
        (lambda r: r.update(InputDataConfig=[{"ChannelName": "smuggled"}]),
         "InputDataConfig"),
        (lambda r: r["StoppingCondition"].update(MaxRuntimeInSeconds=99999),
         "MaxRuntimeInSeconds"),
        (lambda r: r.update(EnableManagedSpotTraining=True),
         "EnableManagedSpotTraining"),
        (lambda r: r.update(RoleArn="arn:aws:iam::558069890522:role/other"),
         "RoleArn"),
        (lambda r: r.update(EnableNetworkIsolation=True),
         "EnableNetworkIsolation"),
        (lambda r: r["VpcConfig"].update(Subnets=["subnet-evil"]), "Subnets"),
        (lambda r: r["VpcConfig"].update(SecurityGroupIds=["sg-evil"]),
         "SecurityGroupIds"),
        # Codex #24 finding 1 reproductions — ALL previously passed:
        (lambda r: r.update(RemoteDebugConfig={"EnableRemoteDebug": True}),
         "RemoteDebugConfig"),
        (lambda r: r.update(HyperParameters={"smuggled": "1"}),
         "HyperParameters"),
        (lambda r: r.update(RetryStrategy={"MaximumRetryAttempts": 5}),
         "RetryStrategy"),
        (lambda r: r.update(DebugHookConfig={
            "S3OutputPath": "s3://exfil/hook"}), "DebugHookConfig"),
        (lambda r: r.update(InfraCheckConfig={"EnableInfraCheck": True}),
         "InfraCheckConfig"),
        (lambda r: r.update(TensorBoardOutputConfig={
            "S3OutputPath": "s3://exfil/tb"}), "TensorBoardOutputConfig"),
        (lambda r: r.update(ProfilerConfig={
            "S3OutputPath": "s3://exfil/prof"}), "ProfilerConfig"),
        (lambda r: r.update(ProfilerRuleConfigurations=[{"RuleConfigName": "x"}]),
         "ProfilerRuleConfigurations"),
        (lambda r: r.update(ExperimentConfig={"ExperimentName": "x"}),
         "ExperimentConfig"),
        (lambda r: r.update(EnableInterContainerTrafficEncryption=True),
         "EnableInterContainerTrafficEncryption"),
        # Codex #25: MaximumRetryAttempts=1 changes behavior — NOT inert
        (lambda r: r.update(RetryStrategy={"MaximumRetryAttempts": 1}),
         "RetryStrategy"),
        (lambda r: r.update(MlflowConfig={
            "MlflowResourceArn": "arn:aws:sagemaker:mlflow"}), "MlflowConfig"),
        (lambda r: r.update(ModelPackageConfig={"ModelPackageGroupName": "x"}),
         "ModelPackageConfig"),
        (lambda r: r.update(ServerlessJobConfig={"Enabled": True}),
         "ServerlessJobConfig"),
        (lambda r: r["AlgorithmSpecification"].update(TrainingImageConfig={
            "TrainingRepositoryAccessMode": "Vpc"}), "TrainingImageConfig"),
        (lambda r: r["AlgorithmSpecification"].update(
            MetricDefinitions=[{"Name": "x", "Regex": ".*"}]),
         "MetricDefinitions"),
        (lambda r: r["ResourceConfig"].update(KeepAlivePeriodInSeconds=3600),
         "KeepAlivePeriodInSeconds"),
        (lambda r: r["ResourceConfig"].update(
            TrainingPlanArn="arn:aws:sagemaker:plan"), "TrainingPlanArn"),
    ]
    for mutate, needle in drifts:
        _, bad = _good_receipt()
        mutate(bad)
        failures = verify_training_receipt(
            bad, packet, expected_job_name=_JOB_NAME, packet_canonical_sha=sha)
        assert failures, f"expected a failure for {needle}"
        assert any(needle in f for f in failures), (needle, failures)


def test_only_live_mode_is_authoritative(tmp_path):
    """Codex review #22 blocker 1: local files are caller-suppliable, so ONLY
    --live (the verifier fetches everything itself) may claim authoritative;
    local modes must self-label non-authoritative or refuse."""
    from verify_arm2_calibration import main as verifier_main
    # no --live and no metrics: point the caller at --live
    with pytest.raises(SystemExit, match="--live"):
        verifier_main(["--packet", str(_PACKET)])
    # metrics alone (no smoke, incomplete local set) refuses and names --live
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps(_good_artifact()).encode())
    with pytest.raises(SystemExit, match="local cross-check needs"):
        verifier_main(["--metrics", str(metrics_path),
                       "--packet", str(_PACKET)])


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


def _perfect_dev_receipts():
    """Per-row receipts where hyp == normalized reference (corpus WER 0.0),
    built from the COMMITTED slices — the shape a real perfect run would
    produce, recomputable by the verifier."""
    from pipeline.normalizers import for_language
    decl = json.loads(_PACKET.read_bytes())["result_verifier"]["dev_manifests"]
    results, wer = {}, {}
    for lang in ("lingala", "swahili"):
        norm = for_language(lang)
        rows = []
        for line in (_REPO / decl[lang]["path"]).read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            reference = norm(row["text_normalized"])
            rows.append({"audio_checksum_sha256": row["audio_checksum_sha256"],
                         "hyp_normalized": reference,
                         "edit_distance": 0,
                         "ref_words": len(reference.split())})
        results[lang] = {"rows": rows}
        wer[lang] = 0.0
    return results, wer


def test_run_verifier_smoke_passes_on_a_clean_artifact(tmp_path):
    """The wrapper's in-image smoke: a clean artifact (with recomputable
    per-row receipts) against the BAKED execution contract returns 0."""
    from pipeline.omniasr_calibrate import run_verifier
    art = _good_artifact()
    results, wer = _perfect_dev_receipts()
    art["dev_sentinel_results"] = results
    art["dev_sentinel_wer"] = wer
    # self-binds: the verifier's own bytes + the contract's own sha
    art["identity"]["verifier_script_sha256"] = _hashlib.sha256(
        (_REPO / "scripts/verify_arm2_calibration.py").read_bytes()).hexdigest()
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps(art).encode())
    assert run_verifier(metrics_path, _CONTRACT, bind_packet_sha=False) == 0


def test_contract_binds_and_matches_packet():
    """Codex #22 blocker 2: the committed contract's sha equals the packet's
    declaration and the shared blocks are byte-equal — and the contract holds
    no self-reference (no image digest, no cost, no execution_contract)."""
    packet = json.loads(_PACKET.read_bytes())
    contract = json.loads(_CONTRACT.read_bytes())
    assert packet["execution_contract"]["path"] == \
        str(_CONTRACT.relative_to(_REPO).as_posix())
    assert packet["execution_contract"]["sha256"] == _CONTRACT_SHA
    for block in ("environment", "distillation", "result_verifier", "job_id"):
        assert contract[block] == packet[block], block
    for absent in ("image_uri_with_digest", "execution_contract",
                   "cost_ceiling_usd", "worst_case_usd"):
        assert absent not in contract, absent
    # and the committed env carries no launcher-injected key
    for injected in ("MEDZEN_CALIBRATION_PACKET",
                     "MEDZEN_CALIBRATION_PACKET_SHA256",
                     "MEDZEN_TRAINING_JOB_NAME", "MEDZEN_EXECUTION_CONTRACT",
                     "MEDZEN_EXECUTION_CONTRACT_SHA256"):
        assert injected not in packet["environment"], injected


def test_wrapper_contract_gate_refuses_a_swapped_contract():
    from pipeline.omniasr_calibrate import (CalibrationRefusal,
                                            verify_contract_binding)
    body = _CONTRACT.read_bytes()
    assert verify_contract_binding(body, _CONTRACT_SHA) == _CONTRACT_SHA
    with pytest.raises(CalibrationRefusal, match="unreviewed contract"):
        verify_contract_binding(body + b" ", _CONTRACT_SHA)
    with pytest.raises(CalibrationRefusal, match="unreviewed contract"):
        verify_contract_binding(body, "0" * 64)


def test_identity_binds_the_execution_contract_sha():
    art = _good_artifact()
    ok = verify_calibration(art, _spec(), expected_contract_sha=_CONTRACT_SHA)
    assert ok == []
    art["identity"]["execution_contract_sha256"] = "0" * 64
    bad = verify_calibration(art, _spec(), expected_contract_sha=_CONTRACT_SHA)
    assert any("execution_contract_sha256" in f for f in bad)


def test_dev_row_receipts_recompute_and_reject_tampering():
    """Codex #22: the scalar WER must be RECOMPUTABLE — per-row receipts are
    verified against the committed slices, and any tamper fails."""
    from verify_arm2_calibration import verify_dev_row_receipts
    read = lambda rel: (_REPO / rel).read_bytes()  # noqa: E731
    art = _good_artifact()
    results, wer = _perfect_dev_receipts()
    art["dev_sentinel_results"] = results
    art["dev_sentinel_wer"] = wer
    assert verify_dev_row_receipts(art, _spec(), read_manifest=read) == []
    # missing block
    bare = _good_artifact()
    assert any("per-row receipts" in f for f in
               verify_dev_row_receipts(bare, _spec(), read_manifest=read))
    # tampered edit distance does not reproduce
    tampered = json.loads(json.dumps(art))
    tampered["dev_sentinel_results"]["lingala"]["rows"][0]["edit_distance"] = 3
    assert any("do not reproduce" in f for f in
               verify_dev_row_receipts(tampered, _spec(), read_manifest=read))
    # dropped row breaks coverage
    short = json.loads(json.dumps(art))
    short["dev_sentinel_results"]["swahili"]["rows"].pop()
    assert any("cover the manifest exactly" in f for f in
               verify_dev_row_receipts(short, _spec(), read_manifest=read))
    # scalar that disagrees with the receipts
    lying = json.loads(json.dumps(art))
    lying["dev_sentinel_wer"]["lingala"] = 0.42
    assert any("does not equal the WER recomputed" in f for f in
               verify_dev_row_receipts(lying, _spec(), read_manifest=read))


def _make_bundle(tmp_path, *, model_bytes=b"MODEL-BYTES", tamper_model=False,
                 drop_member=None):
    """Craft a model.tar.gz shaped like a real calibration output bundle."""
    import io
    import tarfile
    from verify_arm2_calibration import _canonical_sha256
    packet = _launchable_packet()
    model_sha = _hashlib.sha256(model_bytes).hexdigest()
    manifest = {"record": "OMNIASR_MERGED_CHECKPOINT_MANIFEST",
                "model_sha256": model_sha}
    manifest_bytes = json.dumps(manifest, sort_keys=True,
                                separators=(",", ":")).encode() + b"\n"
    art = _good_artifact()
    results, wer = _perfect_dev_receipts()
    art["dev_sentinel_results"] = results
    art["dev_sentinel_wer"] = wer
    art["identity"].update(
        packet_sha256=_canonical_sha256(packet),
        verifier_script_sha256=_hashlib.sha256(
            (_REPO / "scripts/verify_arm2_calibration.py").read_bytes()
        ).hexdigest(),
        export_manifest_sha256=_hashlib.sha256(manifest_bytes).hexdigest(),
        export_model_sha256=model_sha)
    metrics_bytes = json.dumps(art, sort_keys=True).encode()
    stored_model = (model_bytes + b"-TAMPERED") if tamper_model else model_bytes
    members = {"calibration-metrics.json": metrics_bytes,
               "export/manifest.json": manifest_bytes,
               "export/model.pt": stored_model}
    if drop_member:
        members.pop(drop_member)
    tar_path = tmp_path / "model.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return packet, tar_path


def test_live_bundle_verifies_and_rejects_each_forgery(tmp_path):
    """Codex #22 blocker 1: the live core hashes everything itself from the
    fetched bundle; KMS/prefix/model tampering all fail."""
    from verify_arm2_calibration import (safe_extract_bundle,
                                         verify_live_bundle)
    packet, tar_path = _make_bundle(tmp_path)
    extracted = safe_extract_bundle(tar_path, tmp_path / "bundle")
    vsha = _hashlib.sha256(
        (_REPO / "scripts/verify_arm2_calibration.py").read_bytes()).hexdigest()
    # Codex #23: the EXACT SageMaker artifact path
    # <S3OutputPath>/<TrainingJobName>/output/model.tar.gz
    exact_uri = ("s3://medzen-speech/research/b5-training/"
                 "b5-universal-arm2-ftcal-2026-001/output/"
                 f"{_JOB_NAME}/output/model.tar.gz")
    good_meta = {"uri": exact_uri,
                 "SSEKMSKeyId": packet["kms_key_arn"], "VersionId": "v1",
                 "ETag": "etag", "ContentLength": 1}
    _, receipt = _good_receipt()
    failures, facts = verify_live_bundle(
        packet=packet, receipt=receipt, extracted=extracted,
        s3_meta=good_meta, verifier_script_sha=vsha, repo_root=_REPO)
    assert failures == [], failures
    assert facts["s3_version_id"] == "v1"
    # wrong KMS key on the fetched object
    bad_kms = dict(good_meta, SSEKMSKeyId="arn:aws:kms:other")
    failures, _ = verify_live_bundle(
        packet=packet, receipt=receipt, extracted=extracted,
        s3_meta=bad_kms, verifier_script_sha=vsha, repo_root=_REPO)
    assert any("SSEKMSKeyId" in f for f in failures)
    # artifact outside the derived output prefix
    bad_uri = dict(good_meta, uri="s3://elsewhere/model.tar.gz")
    failures, _ = verify_live_bundle(
        packet=packet, receipt=receipt, extracted=extracted,
        s3_meta=bad_uri, verifier_script_sha=vsha, repo_root=_REPO)
    assert any("exact expected artifact" in f for f in failures)
    # Codex #23 reproduction: `output-evil/...` beat the old startswith()
    evil_uri = dict(good_meta, uri=good_meta["uri"].replace(
        "/output/", "/output-evil/", 1))
    failures, _ = verify_live_bundle(
        packet=packet, receipt=receipt, extracted=extracted,
        s3_meta=evil_uri, verifier_script_sha=vsha, repo_root=_REPO)
    assert any("exact expected artifact" in f for f in failures)
    # Codex #23 reproduction: an unpinned (no-VersionId) fetch must fail
    no_version = dict(good_meta, VersionId=None)
    failures, _ = verify_live_bundle(
        packet=packet, receipt=receipt, extracted=extracted,
        s3_meta=no_version, verifier_script_sha=vsha, repo_root=_REPO)
    assert any("VersionId" in f for f in failures)
    # tampered model bytes: the verifier's own hash disagrees with the manifest
    (tmp_path / "t2").mkdir()
    packet2, tar2 = _make_bundle(tmp_path / "t2", tamper_model=True)
    extracted2 = safe_extract_bundle(tar2, tmp_path / "t2" / "bundle")
    failures, _ = verify_live_bundle(
        packet=packet2, receipt=receipt, extracted=extracted2,
        s3_meta=good_meta, verifier_script_sha=vsha, repo_root=_REPO)
    assert any("torn export" in f for f in failures)


def test_safe_extract_refuses_missing_and_unsafe_members(tmp_path):
    import io
    import tarfile
    from verify_arm2_calibration import safe_extract_bundle
    _, tar_missing = _make_bundle(tmp_path, drop_member="export/model.pt")
    with pytest.raises(SystemExit, match="lacks"):
        safe_extract_bundle(tar_missing, tmp_path / "b1")
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as archive:
        for name in ("calibration-metrics.json", "export/manifest.json"):
            info = tarfile.TarInfo(name)
            info.size = 2
            archive.addfile(info, io.BytesIO(b"{}"))
        info = tarfile.TarInfo("../../export/model.pt")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(SystemExit):
        safe_extract_bundle(evil, tmp_path / "b2")


def test_execution_window_version_selection():
    """Codex #23 high: exactly ONE artifact version created inside the job's
    AWS-recorded execution window is acceptable; zero or many refuse."""
    from verify_arm2_calibration import select_execution_window_version
    start, end = "2026-08-24T10:00:00+00:00", "2026-08-24T11:00:00+00:00"
    inside = {"VersionId": "good", "LastModified": "2026-08-24T11:05:00+00:00"}
    before = {"VersionId": "old", "LastModified": "2026-08-24T09:00:00+00:00"}
    after = {"VersionId": "posthoc",
             "LastModified": "2026-08-24T12:00:00+00:00"}  # past slack
    assert select_execution_window_version(
        [before, inside, after], start, end)["VersionId"] == "good"
    with pytest.raises(SystemExit, match="exactly ONE"):
        select_execution_window_version([before, after], start, end)
    with pytest.raises(SystemExit, match="exactly ONE"):
        second = dict(inside, VersionId="dupe")
        select_execution_window_version([inside, second], start, end)


def test_tar_member_size_caps_refuse_disk_exhaustion(tmp_path):
    """Codex #23: a declared-oversize member refuses before extraction."""
    import io
    import tarfile
    from verify_arm2_calibration import (BUNDLE_MEMBER_MAX_BYTES,
                                         safe_extract_bundle)
    oversize = BUNDLE_MEMBER_MAX_BYTES["export/manifest.json"] + 1
    tar_path = tmp_path / "big.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        for name, data in (("calibration-metrics.json", b"{}"),
                           ("export/model.pt", b"m"),
                           ("export/manifest.json", b"x" * oversize)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    with pytest.raises(SystemExit, match="byte cap"):
        safe_extract_bundle(tar_path, tmp_path / "out")


class _ZeroReader:
    """Streams `size` zero bytes without allocating them (tar test helper)."""

    def __init__(self, size: int):
        self.remaining = size

    def read(self, n: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        take = self.remaining if n in (-1, None) else min(n, self.remaining)
        self.remaining -= take
        return b"\0" * take


def test_archive_member_count_and_stream_caps(tmp_path):
    """Codex #24 finding 2: too many members refuses, and the download
    streamer aborts past its cap even when ContentLength lied."""
    import io
    import tarfile
    from verify_arm2_calibration import (ARCHIVE_MAX_MEMBERS,
                                         safe_extract_bundle, stream_with_cap)
    crowded = tmp_path / "crowded.tar.gz"
    with tarfile.open(crowded, "w:gz") as archive:
        for index in range(ARCHIVE_MAX_MEMBERS + 1):
            info = tarfile.TarInfo(f"junk-{index}")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(SystemExit, match="members"):
        safe_extract_bundle(crowded, tmp_path / "out")
    # streaming cap: the body claims nothing but streams past the cap
    with pytest.raises(SystemExit, match="mid-stream"):
        stream_with_cap(_ZeroReader(2048), tmp_path / "capped.bin", 1024,
                        label="test download")
    # under the cap streams fine and reports the byte count
    assert stream_with_cap(_ZeroReader(512), tmp_path / "ok.bin", 1024,
                           label="test download") == 512


def test_ctc_greedy_truncates_to_valid_frames():
    """Codex #23 medium: the decode must truncate to the model's RETURNED
    output length — a junk token in the padded tail must not vote."""
    torch = pytest.importorskip("torch")
    from pipeline.omniasr_calibrate import _ctc_greedy_text

    calls = []

    def decoder(ids):
        calls.append([int(x) for x in ids])
        return " ".join(str(int(x)) for x in ids)

    # 4 frames; frame 3 (index 3) is PADDING carrying a loud junk token 7
    logits = torch.full((4, 8), -10.0)
    logits[0, 2] = 10.0   # token 2
    logits[1, 2] = 10.0   # repeat -> collapses
    logits[2, 0] = 10.0   # blank -> dropped
    logits[3, 7] = 10.0   # junk in the padded tail
    full = _ctc_greedy_text(logits, decoder, blank_idx=0)
    truncated = _ctc_greedy_text(logits, decoder, blank_idx=0, valid_frames=3)
    assert "7" in full and "7" not in truncated
    assert truncated == "2"


def test_scorer_source_matches_upstream_decode_contract():
    """Structural parity with the pinned OmniASR pipeline (Codex #23): the
    scorer must create its decoder with skip_special_tokens=True and truncate
    with the returned output layout's seq_lens. The full behavioral parity
    test runs in-image (fairseq2 present)."""
    source = (_REPO / "pipeline/omniasr_calibrate.py").read_text()
    assert "create_decoder(skip_special_tokens=True)" in source
    assert "out_layout" in source and "seq_lens" in source


def test_in_image_scorer_layout_decode_contract(monkeypatch):
    """LAYOUT/DECODE CONTRACT test (Codex #24 finding 3: this is NOT full
    upstream parity — the model, tokenizer and audio are fakes). It proves the
    scorer's mechanics with a real fairseq2 BatchLayout: truncation to the
    returned seq_lens and a skip_special_tokens=True decoder, over the real
    committed slice. TRUE parity against the pinned upstream decoder is
    test_real_model_decode_parity_against_upstream below (model+audio gated,
    run in-image before calibration)."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("fairseq2")
    pytest.importorskip("soundfile")
    import numpy as np

    import pipeline.omniasr_calibrate as calibrate
    import pipeline.omniasr_data as omniasr_data
    import pipeline.train_asr as train_asr

    class _Tokenizer:
        class vocab_info:
            pad_idx = 0

        def create_decoder(self, *, skip_special_tokens):
            assert skip_special_tokens is True

            def decode(ids):
                return " ".join(f"tok{int(x)}" for x in ids)
            return decode

    class _Model:
        def __call__(self, wave, layout):
            from fairseq2.nn import BatchLayout
            frames = torch.full((1, 4, 8), -10.0)
            frames[0, 0, 2] = 10.0
            frames[0, 1, 2] = 10.0
            frames[0, 2, 0] = 10.0        # blank
            frames[0, 3, 7] = 10.0        # junk in the padded tail
            out = BatchLayout((1, 4), seq_lens=[3], device=frames.device)
            return frames, out

    monkeypatch.setattr(omniasr_data, "fetch_audio",
                        lambda cli, row, cache: Path("/dev/null"))
    import soundfile
    monkeypatch.setattr(
        soundfile, "read",
        lambda *_a, **_k: (np.zeros(16000, dtype="float32"), 16000))
    monkeypatch.setattr(train_asr, "s3", lambda: None)

    wer, shas, results = calibrate._score_dev_sentinels(
        object(), _Model(), _Tokenizer(), None,
        {"lingala": "platform/manifests/dev-sentinels/lingala.jsonl"})
    hyp = results["lingala"]["rows"][0]["hyp_normalized"]
    assert "tok7" not in hyp and "tok2" in hyp        # tail junk truncated
    assert shas["lingala"] == _DEV_SHAS["lingala"]    # real slice, real sha
    assert len(results["lingala"]["rows"]) == 60


def test_real_model_decode_parity_against_upstream():
    """TRUE decode parity (Codex #24 finding 3): the scorer's CTC-greedy path
    must transcribe REAL audio identically (post-normalization) to the pinned
    upstream ASRInferencePipeline on the REAL model. Needs the GPU image with
    staged weights + fetched audio, so it is gated on MEDZEN_PARITY_AUDIO_DIR
    (a directory of .wav files) — the reviewer runs it in-image before the
    calibration is accepted:
      MEDZEN_PARITY_AUDIO_DIR=/tmp/parity-audio python -m pytest \
        tests/test_arm2_calibration.py::test_real_model_decode_parity_against_upstream -q
    """
    import os
    torch = pytest.importorskip("torch")
    pytest.importorskip("fairseq2")
    omni = pytest.importorskip("omnilingual_asr")
    audio_dir = os.environ.get("MEDZEN_PARITY_AUDIO_DIR", "")
    if not audio_dir or not Path(audio_dir).is_dir():
        pytest.skip("set MEDZEN_PARITY_AUDIO_DIR to a directory of wav files "
                    "(in-image, with staged weights) to run true parity")
    import soundfile as sf
    from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline

    from pipeline.normalizers import for_language
    from pipeline.omniasr_calibrate import _ctc_greedy_text
    from pipeline.omniasr_train import TrainerConfig, _load_model_and_tokenizer

    wavs = sorted(Path(audio_dir).glob("*.wav"))[:4]
    assert wavs, "no wav files in MEDZEN_PARITY_AUDIO_DIR"
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    upstream = ASRInferencePipeline(model_card="medzen_omniASR_CTC_1B_v2",
                                    device=device, dtype=torch.bfloat16)

    class _Cfg:
        model_card = "medzen_omniASR_CTC_1B_v2"
    model, tokenizer, dev = _load_model_and_tokenizer(_Cfg())
    model.eval()          # Codex #25: no dropout in the parity comparison
    decoder = tokenizer.create_decoder(skip_special_tokens=True)
    blank = int(getattr(getattr(tokenizer, "vocab_info", None), "pad_idx", 0)
                or 0)
    norm = for_language("lingala")
    from fairseq2.nn import BatchLayout
    from pipeline.omniasr_calibrate import _preprocess_wave
    for wav in wavs:
        audio, _sr = sf.read(wav, dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        wave = _preprocess_wave(audio, _sr).to(torch.bfloat16)\
            .unsqueeze(0).to(dev)
        layout = BatchLayout(tuple(wave.shape), seq_lens=[wave.shape[1]],
                             device=wave.device)
        with torch.no_grad():
            logits, out_layout = model(wave, layout)
        ours = norm(_ctc_greedy_text(
            logits[0], decoder, blank,
            valid_frames=int(out_layout.seq_lens[0])))
        theirs = norm(str(upstream.transcribe(
            [str(wav)], lang=None, batch_size=1)[0]))
        assert ours == theirs, (wav.name, ours, theirs)


def test_wrongref_canary_runs_in_the_publisher_environment():
    """Codex #23 medium: the negative canary must share the publisher's
    protected environment so ONLY job_workflow_ref differs — a sub-mismatch
    denial would prove nothing about the workflow-identity restriction."""
    text = (_REPO / ".github/workflows/arm2-image-canary-wrongref-exec.yml"
            ).read_text()
    assert "environment: trainer-image-publish" in text


def test_botocore_model_every_field_is_governed():
    """Codex #25 finding 1: a hand-list trails AWS. Every member of the
    pinned botocore CreateTrainingJob∩DescribeTrainingJob model must be
    RENDERED, UNRENDERED-INERT, or CREATE-ONLY-GOVERNED — a botocore upgrade
    that introduces a new field FAILS this test until it is governed."""
    botocore = pytest.importorskip("botocore.session")
    from verify_arm2_calibration import (CREATE_ONLY_GOVERNED,
                                         RENDERED_TOP_KEYS,
                                         UNRENDERED_INERT_KEYS)
    model = botocore.get_session().get_service_model("sagemaker")
    create = set(model.operation_model("CreateTrainingJob").input_shape.members)
    describe = set(
        model.operation_model("DescribeTrainingJob").output_shape.members)
    governed = RENDERED_TOP_KEYS | UNRENDERED_INERT_KEYS
    ungoverned = (create & describe) - governed
    assert not ungoverned, (
        f"NEW/ungoverned CreateTrainingJob fields: {sorted(ungoverned)} — "
        "extend RENDERED_TOP_KEYS or UNRENDERED_INERT_KEYS deliberately")
    create_only = create - describe
    assert create_only <= CREATE_ONLY_GOVERNED, (
        f"create-only fields not governed: "
        f"{sorted(create_only - CREATE_ONLY_GOVERNED)}")


def _as_camel(obj):
    if isinstance(obj, dict):
        return {k[:1].lower() + k[1:]: _as_camel(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_as_camel(v) for v in obj]
    return obj


_JOB_ARN = ("arn:aws:sagemaker:eu-central-1:558069890522:training-job/"
            + _JOB_NAME)
_SERVICE_ADDED = {
    "disableEFA": False, "withWarmPoolValidationError": False,
    "trainingJobArn": _JOB_ARN}


def _real_shaped_params(request):
    """A real MedZen creation record's requestParameters: the rendered request
    (camelCase) PLUS the empirically-observed inert SageMaker defaults, both
    top-level and NESTED (confirmed against a live event)."""
    params = _as_camel(request)
    params.update(_SERVICE_ADDED)
    params["algorithmSpecification"]["enableSageMakerMetricsTimeSeries"] = False
    params["resourceConfig"]["useReservedCapacity"] = False
    return params


def test_creation_request_comparison_is_two_sided():
    """Codex #26 finding 2: every RENDERED field must be present+equal, and the
    ONLY tolerated extras are the empirically-observed inert defaults — a
    genuine record (with those defaults) passes, a partial record fails, and
    smuggled/altered fields fail."""
    from b5_sagemaker_job import render_request
    from verify_arm2_calibration import verify_creation_request_parameters
    request = render_request(_launchable_packet())

    # a REAL-shaped record (rendered + service defaults, top-level AND nested)
    good = _real_shaped_params(request)
    assert verify_creation_request_parameters(
        good, request, expected_job_arn=_JOB_ARN) == []

    # partial record (only the job name) must FAIL now (was a bypass)
    partial = {"trainingJobName": _JOB_NAME}
    assert any("ABSENT" in f for f in verify_creation_request_parameters(
        partial, request, expected_job_arn=_JOB_ARN))

    # create-only smuggling and unknown/altered fields FAIL
    for mutate, needle in [
        (lambda p: p.update(sessionChainingConfig={
            "enableSessionTagChaining": True}), "sessionChainingConfig"),
        (lambda p: p.update(futureConfig={"x": 1}), "futureConfig"),
        (lambda p: p.__setitem__("disableEFA", True), "disableEFA"),
        (lambda p: p.update(trainingJobArn="arn:aws:sagemaker:evil"),
         "trainingJobArn"),
        (lambda p: p["algorithmSpecification"].__setitem__(
            "containerEntrypoint", ["/bin/sh"]), "ContainerEntrypoint"),
        (lambda p: p["algorithmSpecification"].pop("containerEntrypoint"),
         "ABSENT"),
        (lambda p: p["resourceConfig"].__setitem__("useReservedCapacity", True),
         "useReservedCapacity"),
    ]:
        bad = _real_shaped_params(request)
        mutate(bad)
        failures = verify_creation_request_parameters(
            bad, request, expected_job_arn=_JOB_ARN)
        assert any(needle in f for f in failures), (needle, failures)
    # an empty record proves nothing
    assert verify_creation_request_parameters({}, request,
                                              expected_job_arn=_JOB_ARN)


def test_real_cloudtrail_fixture_shape_is_accepted():
    """The sanitized REAL CloudTrail event (Arm-1) must have exactly the
    service-added field shape the two-sided comparator tolerates — proving the
    comparator was built against reality, not a guess."""
    from verify_arm2_calibration import (SERVICE_ADDED_INERT_FALSE, _camel)
    event = json.loads((_REPO / "tests/fixtures/aws/"
                        "cloudtrail-createtrainingjob-arm1-real.json").read_bytes())
    params = event["requestParameters"]
    # every top-level extra beyond a rendered-shape key is a tolerated default
    tolerated = SERVICE_ADDED_INERT_FALSE | {"trainingJobArn"}
    rendered_like = {"algorithmSpecification", "checkpointConfig",
                     "enableInterContainerTrafficEncryption",
                     "enableManagedSpotTraining", "enableNetworkIsolation",
                     "environment", "outputDataConfig", "resourceConfig",
                     "roleArn", "stoppingCondition", "tags", "trainingJobName",
                     "vpcConfig"}
    for key in params:
        assert _camel(key) in rendered_like | tolerated, key
    # the nested defaults are exactly the ones the comparator allows
    assert params["algorithmSpecification"].get(
        "enableSageMakerMetricsTimeSeries") is False
    assert params["resourceConfig"].get("useReservedCapacity") is False


def test_creation_event_envelope_binds_principal_account_and_success():
    """Codex #26 finding 2: the envelope proves WHICH role launched the job."""
    from b5_sagemaker_job import render_request
    from verify_arm2_calibration import (CALIBRATION_LAUNCH_ROLE_ARN,
                                         verify_creation_event)
    request = render_request(_launchable_packet())
    event = {
        "eventName": "CreateTrainingJob",
        "eventSource": "sagemaker.amazonaws.com",
        "awsRegion": "eu-central-1",
        "recipientAccountId": "558069890522",
        "userIdentity": {"sessionContext": {"sessionIssuer": {
            "arn": CALIBRATION_LAUNCH_ROLE_ARN}}},
        "responseElements": {"trainingJobArn": _JOB_ARN},
        "requestParameters": _real_shaped_params(request),
    }
    assert verify_creation_event(
        event, request, expected_job_name=_JOB_NAME,
        expected_job_arn=_JOB_ARN,
        expected_principal_role_arn=CALIBRATION_LAUNCH_ROLE_ARN) == []
    for mutate, needle in [
        (lambda e: e["userIdentity"]["sessionContext"]["sessionIssuer"].update(
            arn="arn:aws:iam::558069890522:role/medzen-arm-launch-role"),
         "expected calibration launch role"),
        (lambda e: e.update(recipientAccountId="111111111111"), "recipientAccountId"),
        (lambda e: e.update(awsRegion="us-east-1"), "awsRegion"),
        (lambda e: e.update(errorCode="AccessDenied"), "did not succeed"),
        (lambda e: e["responseElements"].update(
            trainingJobArn="arn:aws:sagemaker:evil"), "trainingJobArn"),
        (lambda e: e["requestParameters"].update(trainingJobName="other"),
         "trainingJobName"),
    ]:
        import copy as _c
        bad = _c.deepcopy(event)
        mutate(bad)
        failures = verify_creation_event(
            bad, request, expected_job_name=_JOB_NAME,
            expected_job_arn=_JOB_ARN,
            expected_principal_role_arn=CALIBRATION_LAUNCH_ROLE_ARN)
        assert any(needle in f for f in failures), (needle, failures)


def test_fetch_creation_event_queries_by_name_and_selects_success():
    """Codex #26 finding 1+4: query by EventName in the window, filter by
    trainingJobName, keep only the SUCCESSFUL event (failed-then-retried)."""
    import datetime
    from verify_arm2_calibration import fetch_creation_event

    def ev(name, error=None, arn="a"):
        detail = {"eventName": "CreateTrainingJob",
                  "requestParameters": {"trainingJobName": name},
                  "responseElements": {"trainingJobArn": arn}}
        if error:
            detail["errorCode"] = error
        return {"CloudTrailEvent": json.dumps(detail)}

    class _Trail:
        def lookup_events(self, **kwargs):
            # a different job, a FAILED create for ours, then the SUCCESS
            return {"Events": [
                ev("medzen-b5-other"),
                ev(_JOB_NAME, error="ResourceLimitExceeded"),
                ev(_JOB_NAME, arn=_JOB_ARN),
            ]}

    class _Session:
        def client(self, name):
            assert name == "cloudtrail"
            return _Trail()

    now = datetime.datetime(2026, 8, 24, 12, 0, tzinfo=datetime.timezone.utc)
    got = fetch_creation_event(_Session(), _JOB_NAME, now)
    assert got["responseElements"]["trainingJobArn"] == _JOB_ARN
    assert "errorCode" not in got

    # zero successful -> refuse
    class _TrailFail(_Trail):
        def lookup_events(self, **kwargs):
            return {"Events": [ev(_JOB_NAME, error="Throttling")]}
    with pytest.raises(SystemExit, match="exactly ONE SUCCESSFUL"):
        fetch_creation_event(type("S", (), {"client": lambda s, n: _TrailFail()})(),
                             _JOB_NAME, now)


def test_preprocess_wave_matches_upstream_layer_norm():
    """Codex #26 finding 3: population variance (layer_norm), not sample var."""
    torch = pytest.importorskip("torch")
    import torch.nn.functional as functional
    from pipeline.omniasr_calibrate import _preprocess_wave
    raw = (torch.randn(777) * 3.7 + 2.5).numpy()   # short input widens the gap
    wave = _preprocess_wave(raw, 16000)
    ref = functional.layer_norm(torch.as_tensor(raw, dtype=torch.float32),
                                (len(raw),), eps=1e-5)
    assert torch.allclose(wave, ref, atol=1e-6)
    # population variance -> ~1.0 with unbiased=False, and the biased/unbiased
    # gap is what round-25 got wrong
    assert abs(float(wave.var(unbiased=False)) - 1.0) < 1e-3


def test_disk_preflight_accounts_for_full_extraction():
    """Codex #26 finding 5: needed = archive + aggregate extraction + margin,
    NOT archive*2 (extracted bytes exceed the compressed size)."""
    from verify_arm2_calibration import (ARCHIVE_MAX_AGGREGATE_BYTES,
                                         DISK_SAFETY_MARGIN_BYTES,
                                         required_free_bytes)
    declared = 2_600_000_000
    assert required_free_bytes(declared) == (
        declared + ARCHIVE_MAX_AGGREGATE_BYTES + DISK_SAFETY_MARGIN_BYTES)
    # strictly larger than the old archive*2 estimate whenever aggregate>archive
    assert required_free_bytes(declared) > declared * 2


def test_parity_receipt_binds_exact_identities_and_rows():
    """Codex #26 finding 4: exact upstream + scorer identity (not a substring),
    and the parity-proven rows are bound with hypothesis hashes."""
    art = _good_artifact()
    assert verify_calibration(art, _spec()) == []
    for mutate, needle in [
        (lambda a: a["parity"].update(upstream="fake-omnilingual-pipeline"),
         "!= the pinned"),
        (lambda a: a["parity"].update(scorer="wrong"), "canonical scorer"),
        (lambda a: a["parity"]["rows"]["lingala"][0].update(hyp_sha256="x"),
         "hypothesis hash"),
        (lambda a: a["parity"]["rows"].__setitem__("lingala", []),
         "count != rows_checked"),
    ]:
        bad = json.loads(json.dumps(art))
        mutate(bad)
        assert any(needle in f for f in verify_calibration(bad, _spec())), needle


def test_calibration_role_is_scoped_and_carries_the_deny():
    """Codex #26 finding 6: the calibration role can create ONLY the arm-2
    job, carries NoRemoteDebugEver, reads only the arm-2 artifact, and is
    valid Access-Analyzer JSON (no non-schema keys)."""
    doc = json.loads((_REPO / "platform/iam/"
                      "medzen-arm2-calibration-role.json").read_bytes())
    assert set(doc) <= {"Version", "Statement", "Id"}
    body = json.dumps(doc)
    assert "medzen-b5-b5-universal-arm2-ftcal-2026-001" in body
    assert "medzen-b5-b5-universal-arm1-2026-005" not in body   # not arm-1
    assert any(s.get("Sid") == "NoRemoteDebugEver" for s in doc["Statement"])
    create = [s for s in doc["Statement"]
              if s.get("Sid", "").startswith("CreateOnly")][0]
    assert create["Condition"]["StringEquals"][
        "aws:RequestTag/medzen-tier"] == "calibration"
    assert "research/b5-training/b5-universal-arm2-ftcal-2026-001/output/*" in body
    assert "research/b5-training/*" not in body                 # no broad prefix


def test_patch_metrics_records_the_parity_receipt(tmp_path):
    from pipeline.omniasr_calibrate import patch_metrics
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps({"schema": "x"}).encode())
    merged = patch_metrics(
        metrics_path, serve={"readyz": True}, dev_sentinel_wer={},
        identity={}, parity={"upstream_equal": True, "rows_checked": {}})
    assert merged["parity"]["upstream_equal"] is True


def test_runbook_requires_the_deny_on_the_calibration_role():
    """Codex #25 finding 4: the future calibration-scoped role must carry the
    NoRemoteDebugEver deny verbatim; the arm-launch role only authorizes the
    historical Arm-1 job."""
    text = (_REPO / "platform/iam/LOCAL-BOUNDARY-RUNBOOK.md").read_text()
    assert "calibration-scoped role MUST carry" in text
    assert "NoRemoteDebugEver" in text
    arm = json.loads(
        (_REPO / "platform/iam/medzen-arm-launch-role.json").read_bytes())
    assert any(s.get("Sid") == "NoRemoteDebugEver" for s in arm["Statement"])


def test_archive_caps_are_sized_to_the_real_model():
    """Codex #25 finding 3: caps must reflect the ~2.6 GB reality, not 8-9 GB
    ceilings."""
    from verify_arm2_calibration import (ARCHIVE_MAX_AGGREGATE_BYTES,
                                         ARCHIVE_MAX_BYTES,
                                         BUNDLE_MEMBER_MAX_BYTES)
    assert ARCHIVE_MAX_BYTES <= 4 * 1024 ** 3
    assert BUNDLE_MEMBER_MAX_BYTES["export/model.pt"] <= 4 * 1024 ** 3
    assert ARCHIVE_MAX_AGGREGATE_BYTES <= 5 * 1024 ** 3
    # and still comfortably fits the real ~2.6 GB model
    assert BUNDLE_MEMBER_MAX_BYTES["export/model.pt"] >= 3 * 1024 ** 3


def test_dockerfile_ships_the_contract_not_the_launch_packet():
    """Codex #22 blocker 2: the FINAL image must bake the execution contract,
    never the launch packet (which binds the image's own digest)."""
    text = (_REPO / "pipeline/Dockerfile.trainer-omniasr").read_text()
    final_stage = text.split("# Shipped venv keeps", 1)[1]
    assert "EXECUTION-CONTRACT-2026-001.json" in final_stage
    assert "SAGEMAKER-BINDINGS-2026-001.DRAFT.json" not in final_stage
