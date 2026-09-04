"""Strict MEDZEN_EXECUTION_MODE enum (owner-directed): plain | arm2_comparative.
Proves the required invariants: arm2_comparative always runs the wrapper and
permits KD-on candidates + the KD-off control; plain training is unchanged;
unknown/contradictory combinations fail closed; the mode is bound into the run
fingerprint (so a resume cannot switch mode); and every existing plain-training
packet renders byte-for-byte unchanged."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from b5_sagemaker_job import (  # noqa: E402
    JobRefusal, resolve_execution_mode, is_arm2_comparative,
    inject_launcher_provenance, render_request)


# ---- the resolver enum + fail-closed -------------------------------------

def test_resolver_default_and_explicit_modes():
    # absent: KD-on => comparative, KD-off => plain (byte-compat)
    assert resolve_execution_mode({}) == "plain"
    assert resolve_execution_mode({"MEDZEN_KD_ENABLE": "1"}) == "arm2_comparative"
    # explicit
    assert resolve_execution_mode(
        {"MEDZEN_EXECUTION_MODE": "plain"}) == "plain"
    assert resolve_execution_mode(
        {"MEDZEN_EXECUTION_MODE": "arm2_comparative"}) == "arm2_comparative"
    # the KD-off control: explicit comparative with KD off
    env = {"MEDZEN_EXECUTION_MODE": "arm2_comparative", "MEDZEN_KD_ENABLE": "0"}
    assert resolve_execution_mode(env) == "arm2_comparative"
    assert is_arm2_comparative(env) is True


def test_resolver_fails_closed_on_unknown_and_contradiction():
    with pytest.raises(JobRefusal, match="not one of"):
        resolve_execution_mode({"MEDZEN_EXECUTION_MODE": "comparative"})
    with pytest.raises(JobRefusal, match="not one of"):
        resolve_execution_mode({"MEDZEN_EXECUTION_MODE": "ARM2_COMPARATIVE"})
    with pytest.raises(JobRefusal, match="contradictory"):
        resolve_execution_mode(
            {"MEDZEN_EXECUTION_MODE": "plain", "MEDZEN_KD_ENABLE": "1"})


def test_does_not_overload_train_mode_or_variant():
    # a plain packet with train_mode=full / variant=ctc is still plain
    assert resolve_execution_mode(
        {"MEDZEN_TRAIN_MODE": "full", "MEDZEN_VARIANT": "ctc"}) == "plain"
    # arm2_comparative is orthogonal to both
    env = {"MEDZEN_EXECUTION_MODE": "arm2_comparative",
           "MEDZEN_TRAIN_MODE": "full", "MEDZEN_VARIANT": "ctc"}
    assert is_arm2_comparative(env)


# ---- parity: the launcher resolver == the trainer's parse_config ---------

def test_launcher_and_trainer_resolvers_agree():
    from pipeline.omniasr_train import parse_config, TrainerRefusal

    def full_env(**over):
        env = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "gb9",
               "MEDZEN_LANGUAGES": "english", "MEDZEN_SEED": "7",
               "MEDZEN_MAX_STEPS": "10", "MEDZEN_TRAIN_MODE": "lora"}
        env.update(over)
        return env
    # valid modes: both resolvers return the same string
    for over in ({}, {"MEDZEN_KD_ENABLE": "1", "MEDZEN_KD_ALPHA": "0.5",
                      "MEDZEN_KD_TEMPERATURE": "1.0",
                      "MEDZEN_KD_PRESERVATION_LANGUAGES": "english"},
                 {"MEDZEN_EXECUTION_MODE": "plain"},
                 {"MEDZEN_EXECUTION_MODE": "arm2_comparative"}):
        env = full_env(**over)
        assert parse_config(env).execution_mode == resolve_execution_mode(env)
    # contradictions: BOTH fail closed (each with its own refusal type)
    bad = full_env(MEDZEN_EXECUTION_MODE="plain", MEDZEN_KD_ENABLE="1",
                   MEDZEN_KD_ALPHA="0.5", MEDZEN_KD_TEMPERATURE="1.0",
                   MEDZEN_KD_PRESERVATION_LANGUAGES="english")
    with pytest.raises(TrainerRefusal):
        parse_config(bad)
    with pytest.raises(JobRefusal):
        resolve_execution_mode(bad)
    with pytest.raises(TrainerRefusal):
        parse_config(full_env(MEDZEN_EXECUTION_MODE="nope"))


def test_mode_is_bound_into_the_run_fingerprint():
    from pipeline.omniasr_train import parse_config
    env = {"MEDZEN_VARIANT": "ctc", "MEDZEN_MANIFEST_VERSION": "gb9",
           "MEDZEN_LANGUAGES": "english", "MEDZEN_SEED": "7",
           "MEDZEN_MAX_STEPS": "10", "MEDZEN_TRAIN_MODE": "lora"}
    from pipeline.omniasr_train import run_fingerprint
    plain = parse_config(dict(env))
    comp = parse_config(dict(env, MEDZEN_EXECUTION_MODE="arm2_comparative"))
    assert plain.execution_mode == "plain"
    assert comp.execution_mode == "arm2_comparative"
    # LEGACY MIGRATION (Codex round 33): a PLAIN run OMITS execution_mode from
    # its fingerprint payload, so its fingerprint is byte-identical to the
    # pre-enum fingerprint and old plain checkpoints still resume.
    assert "execution_mode" not in plain.fingerprint_payload()
    # comparative BINDS the mode into the fingerprint (resume cannot switch it)
    assert comp.fingerprint_payload()["execution_mode"] == "arm2_comparative"
    # the two payloads/fingerprints differ, so plain and comparative can never
    # resume each other's checkpoint
    assert plain.fingerprint_payload() != comp.fingerprint_payload()
    prov = {"complete_raw_sha256": "a" * 64}
    assert run_fingerprint(plain, prov) != run_fingerprint(comp, prov)


# ---- provenance injection follows the mode -------------------------------

def test_injection_fires_for_kd_off_comparative_control():
    bindings = {"job_id": "ctrl-1",
                "execution_contract": {"path": "x/c.json", "sha256": "a" * 64}}
    env = {"MEDZEN_EXECUTION_MODE": "arm2_comparative", "MEDZEN_KD_ENABLE": "0"}
    out = inject_launcher_provenance(env, bindings)
    assert out["MEDZEN_TRAINING_JOB_NAME"] == "medzen-b5-ctrl-1"
    assert out["MEDZEN_EXECUTION_CONTRACT"] == "/opt/medzen/x/c.json"
    # plain packets get NO injection
    plain = inject_launcher_provenance({"MEDZEN_EXECUTION_MODE": "plain"},
                                       {"job_id": "p"})
    assert "MEDZEN_TRAINING_JOB_NAME" not in plain


# ---- every existing plain-training packet is accounted for (no silent skip) -

# The EXACT renderable plain packets pinned to their golden rendered-request
# sha256 (deterministic render_request output). No broad exception, no silent
# skip: every committed plain packet is either here (renders to golden) or in
# the fail-closed set below (refuses cleanly with JobRefusal).
PLAIN_GOLDEN = {
    "B5-GB10-11LANG-WARM-SAGEMAKER-BINDINGS-2026-001.json":
        "edf8181a851d4d2cea5e3afaee192532aadf76c38a1257cdb56fff8a9b0c3d21",
    "CM-PILOT-DIAG-SAGEMAKER-BINDINGS-2026-001.json":
        "451f2419444bcb55f3c23c39f20826b894ab31c8576d314c9dc3bb2f54664736",
    "CM-PILOT-WARM-SAGEMAKER-BINDINGS-2026-001.json":
        "c6810f5802e18293f613380eb32e9a1a9aa1a72d362f03d33e158fbd76ae5e6a",
    "B5-KINYARWANDA-FTCAL-SAGEMAKER-BINDINGS-2026-004.json":
        "903a2c326af059db621e62d0f59e05651974adb335ba5d097c26b947b41891cd",
    "B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-002.json":
        "77937097fc3b154f5d7c3ba59ae249003bf43e65ba4265c67dd56c3aef0c64d4",
    "B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-003.json":
        "4c8b8b49e183882fc400ef72946c5efd64e4320032c2bb7919f719dd57d49e15",
    "B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-004.json":
        "31e078ebdb5d1ccee6562e8aadd7a669814f94ae8273b0e0d2f99c1d6933eec5",
    "B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-005.json":
        "8857742e1ac6788e9a73191f2f559c54ad01e53024f19481ae2a99ab230a7003",
    "B5-UNIVERSAL-FTCAL-SAGEMAKER-BINDINGS-2026-004.json":
        "4c0a2065a452219022318402c0f094e6fdeac665e3de25b798c782df10deeb88",
}


def _all_plain_packet_files():
    out = []
    for p in sorted((ROOT / "platform/manifests").glob(
            "*SAGEMAKER-BINDINGS*.json")):
        try:
            b = json.loads(p.read_bytes())
        except Exception:
            continue
        if not isinstance(b, dict) or "job_id" not in b:
            continue
        try:
            if resolve_execution_mode(b.get("environment") or {}) != "plain":
                continue
        except JobRefusal:
            continue
        out.append((p.name, b))
    return out


def test_every_plain_packet_is_accounted_for_no_silent_skip():
    import hashlib
    files = _all_plain_packet_files()
    names = {n for n, _ in files}
    # the golden set is a subset of the actual committed plain packets
    assert set(PLAIN_GOLDEN) <= names
    checked_golden = 0
    for name, b in files:
        try:
            req = render_request(b)
        except JobRefusal:
            # a historical packet that no longer fully renders must fail CLOSED
            # (clean JobRefusal), never crash (e.g. the KD-off UnboundLocalError)
            assert name not in PLAIN_GOLDEN, f"{name} should render but refused"
            continue
        # it rendered => it MUST be a pinned golden and match byte-for-byte
        assert name in PLAIN_GOLDEN, f"{name} newly renders — pin its golden sha"
        assert req["AlgorithmSpecification"]["ContainerArguments"] == \
            ["-m", "pipeline.omniasr_train"], f"{name} left the bare trainer"
        live_env = req["Environment"]
        for injected in ("MEDZEN_EXECUTION_MODE",
                         "MEDZEN_CALIBRATION_PACKET_SHA256",
                         "MEDZEN_TRAINING_JOB_NAME", "MEDZEN_EXECUTION_CONTRACT"):
            assert injected not in live_env, f"{name} leaked {injected}"
        assert live_env == (b.get("environment") or {}), f"{name} env changed"
        digest = hashlib.sha256(
            json.dumps(req, sort_keys=True, default=str).encode()).hexdigest()
        assert digest == PLAIN_GOLDEN[name], f"{name} rendered request drifted"
        checked_golden += 1
    assert checked_golden == len(PLAIN_GOLDEN), "every golden packet must render"


def test_new_comparative_packet_must_declare_execution_mode_explicitly():
    # a KD-on packet with a NEW job_id and NO explicit mode is refused; legacy
    # inference is limited to the frozen historical calibration job.
    from b5_sagemaker_job import validate_arm2_semantics
    env = {"MEDZEN_KD_ENABLE": "1", "MEDZEN_KD_ALPHA": "0.5"}
    with pytest.raises(JobRefusal, match="must set MEDZEN_EXECUTION_MODE"):
        validate_arm2_semantics({"job_id": "arm2-cand-h1",
                                 "distillation": {}}, env)
    # the EXACT frozen historical packet (bound by SHA) still validates fully
    # without an explicit mode — legacy inference + the coverage fallback apply
    # only to it, not to any packet merely reusing its job_id.
    frozen = json.loads((ROOT / "platform/manifests/"
                         "B5-UNIVERSAL-ARM2-FTCAL-SAGEMAKER-BINDINGS-2026-001.json"
                         ).read_bytes())
    validate_arm2_semantics(frozen, frozen["environment"])  # no raise
    # a NEW packet reusing the historical job_id but with different content is
    # NOT the frozen SHA -> still refused for the missing mode
    tampered = dict(frozen, job_id="b5-universal-arm2-ftcal-2026-001")
    tampered["acceptance_criteria"] = list(frozen["acceptance_criteria"]) + ["x"]
    with pytest.raises(JobRefusal, match="MEDZEN_EXECUTION_MODE"):
        validate_arm2_semantics(tampered, tampered["environment"])


def test_the_frozen_arm2_calibration_packet_still_routes_to_the_wrapper():
    cal = json.loads((ROOT / "platform/manifests/"
                      "B5-UNIVERSAL-ARM2-FTCAL-SAGEMAKER-BINDINGS-2026-001.json"
                      ).read_bytes())
    assert is_arm2_comparative(cal["environment"]) is True
    req = render_request(cal)
    assert req["AlgorithmSpecification"]["ContainerArguments"] == \
        ["-m", "pipeline.omniasr_calibrate"]
    # its committed canonical identity is unchanged by the mode work
    from b5_sagemaker_job import canonical_bindings_sha256
    assert canonical_bindings_sha256(cal) == (
        "3c5024edee3a9df098f1f9e3bdbccc044c963e53f761dc173dbafd1b6a4f9c7e")


# ---- Codex's reproduction: a KD-off comparative control now VALIDATES --------

def test_kd_off_comparative_control_no_longer_crashes_at_recipe_pres():
    """The exact bug Codex reproduced: a KD-off comparative arm hit
    `UnboundLocalError: recipe_pres`. With the shared preservation block it now
    gets PAST that line and fails cleanly (JobRefusal) on a later, real check."""
    from b5_sagemaker_job import (validate_arm2_semantics,
                                  ARM2_CANONICAL_VERIFIER_SCRIPT)
    pres = ["english", "french", "swahili", "lingala"]
    bindings = {
        "job_id": "arm2-control-1",
        "comparative": {"preservation_languages": pres},
        "acceptance_criteria": ["PASS"],
        "result_verifier": {
            "script": ARM2_CANONICAL_VERIFIER_SCRIPT,
            "metrics_schema": "b5-arm2-calibration-metrics/1",
            "metrics_artifact": "calibration-metrics.json",
            "expected_steps": 30,
            "gpu_memory_ceiling_bytes": 22 * 1024 ** 3,
            "required_preservation_coverage": pres,
            "dev_sentinel_languages": ["lingala", "swahili"],
            "kd_enabled": False,
        },
    }
    env = {"MEDZEN_EXECUTION_MODE": "arm2_comparative", "MEDZEN_KD_ENABLE": "0",
           "MEDZEN_MAX_STEPS": "30",
           "MEDZEN_DEV_SENTINEL_MANIFEST_FILES":
               "lingala=a.jsonl,swahili=b.jsonl"}
    # it reaches the execution_contract check (a clean JobRefusal), NOT a crash
    with pytest.raises(JobRefusal, match="execution_contract"):
        validate_arm2_semantics(bindings, env)


def test_kd_off_control_missing_kd_enabled_false_is_refused():
    from b5_sagemaker_job import (validate_arm2_semantics,
                                  ARM2_CANONICAL_VERIFIER_SCRIPT)
    pres = ["english", "french", "swahili", "lingala"]
    spec = {"script": ARM2_CANONICAL_VERIFIER_SCRIPT,
            "metrics_schema": "b5-arm2-calibration-metrics/1",
            "metrics_artifact": "calibration-metrics.json",
            "expected_steps": 30, "gpu_memory_ceiling_bytes": 22 * 1024 ** 3,
            "required_preservation_coverage": pres,
            "dev_sentinel_languages": ["lingala", "swahili"]}  # NO kd_enabled
    bindings = {"job_id": "arm2-control-1",
                "comparative": {"preservation_languages": pres},
                "acceptance_criteria": ["PASS"], "result_verifier": spec}
    env = {"MEDZEN_EXECUTION_MODE": "arm2_comparative", "MEDZEN_KD_ENABLE": "0",
           "MEDZEN_MAX_STEPS": "30",
           "MEDZEN_DEV_SENTINEL_MANIFEST_FILES":
               "lingala=a.jsonl,swahili=b.jsonl"}
    with pytest.raises(JobRefusal, match="kd_enabled=false"):
        validate_arm2_semantics(bindings, env)
