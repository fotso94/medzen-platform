from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-004.json"


def _evidence():
    return json.loads(EVIDENCE.read_text())


def test_local_engineering_004_binds_immutable_inputs_and_control_sources():
    evidence = _evidence()
    assert evidence["status"] == (
        "LOCAL_DEPLOYMENT_CONTROLS_COMPLETE_AWS_DEPLOYMENT_NOT_AUTHORIZED"
    )
    for key in ("authorization", "immutable_scan_evidence"):
        binding = evidence[key]
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == (
            binding["sha256"]
        )
    for relative, expected in evidence["control_source_bindings"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_local_engineering_004_binds_exact_renders_and_synthetic_audio():
    evidence = _evidence()
    for binding in (
        evidence["rendered_manifests"]["workload"],
        evidence["rendered_manifests"]["nvidia_dra"],
        evidence["rendered_manifests"]["values"],
        evidence["synthetic_input"],
    ):
        path = binding.get("path") or binding.get("wav_path")
        expected = binding.get("sha256") or binding.get("wav_sha256")
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
    assert evidence["synthetic_input"]["contains_phi"] is False
    assert evidence["rendered_manifests"]["nvidia_dra"][
        "resourceslice_policy_bound_to_actual_daemonset_service_account"
    ] is True


def test_local_engineering_004_records_no_cloud_mutation_or_gpu_cost():
    preserved = _evidence()["prohibited_state_preserved"]
    for key in (
        "aws_mutation_attempted_during_local_preparation",
        "artifact_upload_attempted",
        "dra_install_attempted",
        "kubernetes_apply_attempted",
        "gpu_scale_up_attempted",
        "approved_asr_write_attempted",
        "model_registration_attempted",
        "language_serving_field_change_attempted",
        "production_ssm_change_attempted",
    ):
        assert preserved[key] is False
    assert _evidence()["budget"]["gpu_hours"] == 0
    assert _evidence()["budget"]["gpu_cost_usd"] == 0.0
    assert preserved["b6a_complete"] is False
    assert preserved["b6_complete"] is False


def test_local_engineering_004_requires_fresh_guarded_plan_after_approval():
    terraform = _evidence()["terraform"]
    assert terraform["guard_status"] == (
        "PASS_EXACT_B6A_PACKET_2026_003C_B_IDENTITY_PHASE"
    )
    assert terraform["read_only_full_plan"] == (
        "3_ADD_0_CHANGE_0_DESTROY_NO_UNRELATED_DRIFT"
    )
    assert terraform["preparation_plan_executable_after_packet_approval"] is False
    assert terraform["apply_attempted"] is False
