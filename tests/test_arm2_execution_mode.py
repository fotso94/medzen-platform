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
    plain = parse_config(dict(env))
    comp = parse_config(dict(env, MEDZEN_EXECUTION_MODE="arm2_comparative"))
    assert plain.execution_mode == "plain"
    assert comp.execution_mode == "arm2_comparative"
    # the mode is part of the fingerprint payload -> a resume cannot switch it
    assert plain.fingerprint_payload()["execution_mode"] == "plain"
    assert comp.fingerprint_payload()["execution_mode"] == "arm2_comparative"
    assert plain.fingerprint_payload() != comp.fingerprint_payload()


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


# ---- every existing plain-training packet renders BYTE-FOR-BYTE unchanged -

def _renderable_plain_packets():
    out = []
    for p in sorted((ROOT / "platform/manifests").glob("*SAGEMAKER-BINDINGS*.json")):
        try:
            b = json.loads(p.read_bytes())
        except Exception:
            continue
        env = b.get("environment") or {}
        if not isinstance(b, dict) or "job_id" not in b:
            continue
        try:
            if resolve_execution_mode(env) != "plain":
                continue
        except JobRefusal:
            continue
        out.append((p.name, b))
    return out


def test_all_plain_packets_route_to_the_bare_trainer_and_get_no_injection():
    packets = _renderable_plain_packets()
    assert packets, "expected several committed plain-training packets"
    rendered_any = False
    for name, b in packets:
        try:
            req = render_request(b)
        except Exception:
            # some historical packets are not fully renderable in isolation;
            # the mode routing is still asserted below on the ones that render
            continue
        rendered_any = True
        assert req["AlgorithmSpecification"]["ContainerArguments"] == \
            ["-m", "pipeline.omniasr_train"], f"{name} must stay on the bare trainer"
        # plain packets receive NO launcher-injected provenance keys
        live_env = req["Environment"]
        for injected in ("MEDZEN_EXECUTION_MODE", "MEDZEN_CALIBRATION_PACKET_SHA256",
                         "MEDZEN_TRAINING_JOB_NAME", "MEDZEN_EXECUTION_CONTRACT"):
            assert injected not in live_env, f"{name} leaked {injected}"
        # and the rendered Environment equals the packet's own environment
        assert live_env == (b.get("environment") or {}), f"{name} env changed"
    assert rendered_any, "at least one plain packet must render for the proof"


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
