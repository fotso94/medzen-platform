from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6A-IAM-APPLY-2026-003C-D.json"


def _record() -> dict:
    return json.loads(RESULT.read_text())


def test_003c_d_phase_a_is_exactly_one_create_and_eventually_online() -> None:
    record = _record()
    assert record["status"] == "VERIFIED_COMPLETE"
    assert record["outcome"] == "PASS_AFTER_EVENTUAL_SSM_REGISTRATION"
    terraform = record["terraform"]
    assert (terraform["create"], terraform["update"], terraform["delete"]) == (1, 0, 0)
    assert terraform["only_resource"] == "aws_iam_role_policy.node_ssm_core"
    assert terraform["post_apply_plan"] == "NO_CHANGES"
    registration = record["cpu_registration"]
    assert registration["commands_sent_to_cpu_nodes"] == 0
    assert registration["final_outcome"] == "PASS_BOTH_EXISTING_CPU_NODES_ONLINE"
    assert len(registration["eventual_registration"]) == 2
    assert {item["ping_status"] for item in registration["eventual_registration"]} == {"Online"}


def test_003c_d_phase_a_binds_immutable_governance_and_policy_sources() -> None:
    record = _record()
    for binding in (record["authorization"], record["independent_review"], record["packet"]):
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == binding["sha256"]
    policy = record["applied_policy"]
    assert hashlib.sha256((ROOT / policy["source_path"]).read_bytes()).hexdigest() == policy["source_sha256"]
    assert policy["readback_semantically_equal"] is True


def test_003c_d_phase_a_preserves_zero_gpu_and_production_boundaries() -> None:
    record = _record()
    boundary = record["phase_b_boundary_at_record_time"]
    for key in (
        "gpu_desired",
        "gpu_instances",
        "gpu_nodes",
        "gpu_seconds_added",
        "approved_asr_objects",
        "production_registry_parameters",
    ):
        assert boundary[key] == 0
    assert boundary["deadline_armed"] is False
    assert boundary["b6a_workload_applied"] is False
    assert record["applied_policy"]["command_sending_access"] is False


def test_003c_d_phase_a_does_not_invent_delayed_cloudtrail_evidence() -> None:
    cloudtrail = _record()["cloudtrail"]
    assert cloudtrail["put_role_policy_event_available_at_record_time"] is False
    assert "no event identity or timestamp is invented" in cloudtrail["interpretation"]
