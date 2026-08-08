from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/B6A-SAMPLER-DEBUG-AUTH-2026-001.json"
POLICY_V1 = ROOT / "platform/runtime-receipt-policy-v1.yaml"
POLICY_V2 = ROOT / "platform/runtime-receipt-policy-v2.yaml"


def _auth() -> dict:
    return json.loads(AUTH.read_text())


def test_debug_authorization_is_not_003c_e_and_is_bounded() -> None:
    auth = _auth()
    assert auth["id"] == "B6A-SAMPLER-DEBUG-AUTH-2026-001"
    assert auth["status"] == "owner-approved"
    scope = auth["aws_scope"]
    assert scope["maximum_gpu_nodes"] == 1
    assert scope["maximum_debug_window_seconds"] == 900
    assert scope["remaining_allowance_before_debug_seconds"] == 6009
    assert "execution_or_approval_of_packet_003C_E" in auth["prohibited_operations"]


def test_v2_raw_output_exception_is_strictly_pre_artifact() -> None:
    policy = yaml.safe_load(POLICY_V2.read_text())
    exception = policy["pre_artifact_raw_output_exception"]
    assert exception["allowed_stages"] == [
        "local_bindings", "dra_stable_readiness", "sampler_self_test"
    ]
    assert exception["required_facts"] == {
        "model_artifact_present_on_node": False,
        "audio_artifact_present_on_node": False,
        "model_or_audio_workload_applied": False,
    }
    assert exception["maximum_utf8_bytes_per_field"] == 32768
    assert "raw_stdout" in exception["allowed_fields"]
    assert "raw_stderr" in exception["allowed_fields"]


def test_v1_and_003c_d_receipts_remain_immutable() -> None:
    auth = _auth()
    binding = auth["policy_binding"]
    assert hashlib.sha256(POLICY_V1.read_bytes()).hexdigest() == binding["historical_v1_sha256"]
    assert hashlib.sha256(POLICY_V2.read_bytes()).hexdigest() == binding["sha256"]
    result = json.loads(
        (ROOT / "platform/evidence/B6A-PACKET-2026-003C-D-BLOCKED-SSM-SAMPLER.json").read_text()
    )
    for receipt in result["stage_receipts"]:
        assert hashlib.sha256((ROOT / receipt["path"]).read_bytes()).hexdigest() == receipt["sha256"]


def test_debug_window_forbids_model_audio_and_production_paths() -> None:
    auth = _auth()
    invariant = auth["pre_artifact_invariant"]
    assert invariant["model_artifact_present_on_node"] is False
    assert invariant["audio_artifact_present_on_node"] is False
    assert invariant["full_b6a_workload_permitted"] is False
    prohibited = " ".join(auth["prohibited_operations"])
    assert "approved_asr_write" in prohibited
    assert "production_ssm_change" in prohibited
