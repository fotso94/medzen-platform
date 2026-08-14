from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT / "platform/evidence/ASR-BASE-MODEL-GPU-STORAGE-APPLY-2026-001.json"
)
AUTH = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-003.json"
PACKET = (
    ROOT
    / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-003-gpu-storage.md"
)
GUARD = ROOT / "scripts/check_asr_eval_gpu_storage_plan.py"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_completion_binds_exact_authorization_packet_and_guard():
    value = _load(EVIDENCE)
    assert value["status"] == "VERIFIED_COMPLETE"
    assert value["authorization"]["sha256"] == _sha(AUTH)
    assert value["change_packet"]["sha256"] == _sha(PACKET)
    assert value["reviewed_saved_plan"]["guard_sha256"] == _sha(GUARD)
    assert value["independent_review"]["decision"] == "APPROVED"


def test_completion_is_exact_storage_replacement_at_zero():
    value = _load(EVIDENCE)
    plan = value["reviewed_saved_plan"]
    assert plan["summary"] == {
        "add": 1,
        "change": 0,
        "destroy": 1,
        "replacement": 1,
    }
    assert plan["resource_actions"] == {
        "aws_eks_node_group.gpu": ["delete", "create"]
    }
    assert plan["replace_paths"] == [["disk_size"]]
    assert plan["field_transition"]["disk_size_gib"] == {
        "before": 20,
        "after": 40,
    }
    assert value["pre_apply_readback"]["desired"] == 0
    assert value["post_apply_readback"]["desired"] == 0


def test_postconditions_and_zero_compute_are_proven():
    value = _load(EVIDENCE)
    after = value["post_apply_readback"]
    assert after["status"] == "ACTIVE"
    assert after["disk_size_gib"] == 40
    assert after["health_issues"] == 0
    assert after["autoscaling_instances"] == 0
    assert after["gpu_ec2_instances"] == 0
    assert after["kubernetes_gpu_nodes"] == 0
    assert after["cpu_desired"] == 0
    assert value["cost"]["gpu_compute_seconds"] == 0


def test_residual_plan_and_attempt_boundary_close_packet():
    value = _load(EVIDENCE)
    assert value["residual_plan"]["status"] == "NO_CHANGES"
    assert value["residual_plan"]["detailed_exit_code"] == 0
    assert value["residual_plan"]["resource_changes"] == 0
    assert value["residual_plan"]["output_changes"] == 0
    assert all(count == 0 for count in value["explicit_non_events"].values())
    assert value["conclusion"]["packet_2026_003_complete"] is True
    assert value["conclusion"]["attempt_20_authorized"] is False
