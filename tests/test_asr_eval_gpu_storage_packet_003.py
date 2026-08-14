from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT
    / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-003-gpu-storage.md"
)
PLAN_EVIDENCE = (
    ROOT / "platform/evidence/ASR-BASE-MODEL-GPU-STORAGE-PLAN-2026-001.json"
)
CAPACITY = (
    ROOT
    / "platform/evidence/ASR-EVAL-RUNTIME-GPU-EPHEMERAL-STORAGE-QUALIFICATION-2026-001.json"
)
REFUSAL = (
    ROOT
    / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002R-ATTEMPT-19-EPHEMERAL-STORAGE-REFUSAL.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_is_owner_gated_and_does_not_authorize_attempt_20():
    text = PACKET.read_text()
    assert "OWNER APPROVAL REQUIRED — NOT AUTHORIZED" in text
    assert "Approve ASR base-model AWS change packet 2026-003 only." in text
    assert "Attempt 20 remains unauthorized" in text
    assert "No AWS mutation is permitted before independent review PASS" in text


def test_plan_evidence_binds_immutable_capacity_and_refusal_records():
    value = _load(PLAN_EVIDENCE)
    assert value["status"] == "PASS_PRE_APPLY_PLAN_QUALIFICATION"
    assert value["capacity_basis"]["sha256"] == _sha(CAPACITY)
    assert value["attempt_19_refusal"]["sha256"] == _sha(REFUSAL)
    assert value["capacity_basis"]["operational_floor_gib"] == 40


def test_plan_is_exact_single_replacement_at_zero():
    value = _load(PLAN_EVIDENCE)
    guard = value["terraform"]["guard"]
    assert guard["status"] == (
        "PASS_EXACT_ASR_BASE_MODEL_GPU_STORAGE_PACKET_2026_003"
    )
    assert guard["summary"] == {
        "add": 1,
        "change": 0,
        "destroy": 1,
        "replacement": 1,
    }
    assert guard["resource_actions"] == {
        "aws_eks_node_group.gpu": ["delete", "create"]
    }
    assert guard["replace_paths"] == [["disk_size"]]
    assert value["live_pre_change_readback"]["desired"] == 0
    assert value["preparation_non_events"]["aws_mutations"] == 0


def test_gpu_source_sets_reviewed_floor_once():
    source = (ROOT / "infra/eks.tf").read_text()
    assert source.count("disk_size = 40") == 1
    assert "Forty GiB is the independently reviewed operational floor" in source
