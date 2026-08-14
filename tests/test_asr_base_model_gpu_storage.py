from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_gpu_storage import (
    GpuStorageRefusal,
    validate_gpu_storage_prerequisite,
)


CAPACITY = Path(
    "platform/evidence/ASR-EVAL-RUNTIME-GPU-EPHEMERAL-STORAGE-QUALIFICATION-2026-001.json"
)
APPLY = Path(
    "platform/evidence/ASR-BASE-MODEL-GPU-STORAGE-APPLY-2026-001.json"
)
FIXTURE = Path(
    "tests/fixtures/asr_base_model_pilot/aws-live-2026-08-14/eks-describe-nodegroup-gpu-40gib.json"
)
INDEX = "sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa"
CHILD = "sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e"
ASG = "eks-gpu-14cfff59-42c6-46ad-8d59-37cd02daefa8"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(root: Path = ROOT) -> dict:
    def binding(relative: Path) -> dict:
        return {"path": str(relative), "sha256": _sha(root / relative)}

    return {
        "schema_version": 1,
        "classification": "PRE_ENVELOPE_NON_CONSUMING_PREREQUISITE",
        "capacity_qualification": binding(CAPACITY),
        "storage_apply_evidence": binding(APPLY),
        "live_fixture": binding(FIXTURE),
        "image": {
            "oci_index_digest": INDEX,
            "linux_amd64_digest": CHILD,
        },
        "operational_floor_gib": 40,
        "gpu_asg_name": ASG,
    }


def _nodegroup(root: Path = ROOT) -> dict:
    return json.loads((root / FIXTURE).read_bytes())["nodegroup"]


def _expected_image() -> dict:
    return {"oci_index_digest": INDEX, "linux_amd64_digest": CHILD}


def _copy_evidence(tmp_path: Path) -> None:
    for relative in (CAPACITY, APPLY, FIXTURE):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())


def test_exact_40_gib_state_passes_before_attempt_consumption() -> None:
    result = validate_gpu_storage_prerequisite(
        ROOT, _policy(), _nodegroup(), expected_image=_expected_image()
    )
    assert result["status"] == "PASS_PRE_ENVELOPE_GPU_STORAGE"
    assert result["calculated_minimum_gib"] == 29
    assert result["operational_floor_gib"] == 40
    assert result["live_root_volume_gib"] == 40
    assert result["attempt_envelope_created"] is False
    assert result["attempt_number_consumed"] is False
    assert result["aws_read_calls"] == 1
    assert result["aws_mutations"] == 0


def test_20_gib_state_refuses_before_attempt_consumption() -> None:
    nodegroup = copy.deepcopy(_nodegroup())
    nodegroup["diskSize"] = 20
    with pytest.raises(GpuStorageRefusal) as captured:
        validate_gpu_storage_prerequisite(
            ROOT, _policy(), nodegroup, expected_image=_expected_image()
        )
    assert captured.value.reason_code == "GPU_ROOT_VOLUME_BELOW_OPERATIONAL_FLOOR"


@pytest.mark.parametrize("value", [None, "40", True])
def test_missing_or_malformed_live_disk_size_refuses(value: object) -> None:
    nodegroup = copy.deepcopy(_nodegroup())
    if value is None:
        nodegroup.pop("diskSize")
    else:
        nodegroup["diskSize"] = value
    with pytest.raises(GpuStorageRefusal) as captured:
        validate_gpu_storage_prerequisite(
            ROOT, _policy(), nodegroup, expected_image=_expected_image()
        )
    assert captured.value.reason_code == "GPU_NODEGROUP_STORAGE_RESPONSE_MALFORMED"


def test_missing_capacity_evidence_refuses(tmp_path: Path) -> None:
    _copy_evidence(tmp_path)
    (tmp_path / CAPACITY).unlink()
    policy = _policy(ROOT)
    with pytest.raises(GpuStorageRefusal) as captured:
        validate_gpu_storage_prerequisite(
            tmp_path, policy, _nodegroup(), expected_image=_expected_image()
        )
    assert captured.value.reason_code == "GPU_STORAGE_EVIDENCE_ABSENT"


def test_capacity_evidence_hash_drift_refuses(tmp_path: Path) -> None:
    _copy_evidence(tmp_path)
    (tmp_path / CAPACITY).write_bytes((tmp_path / CAPACITY).read_bytes() + b"\n")
    with pytest.raises(GpuStorageRefusal) as captured:
        validate_gpu_storage_prerequisite(
            tmp_path,
            _policy(ROOT),
            _nodegroup(),
            expected_image=_expected_image(),
        )
    assert captured.value.reason_code == "GPU_STORAGE_EVIDENCE_HASH_DIFFERS"


def test_capacity_arithmetic_drift_refuses_even_with_rebound_hash(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)
    capacity = json.loads((tmp_path / CAPACITY).read_bytes())
    capacity["capacity_policy_derivation"]["calculated_required_bytes"] += 1
    (tmp_path / CAPACITY).write_text(json.dumps(capacity), encoding="utf-8")
    policy = _policy(tmp_path)
    with pytest.raises(GpuStorageRefusal) as captured:
        validate_gpu_storage_prerequisite(
            tmp_path,
            policy,
            _nodegroup(),
            expected_image=_expected_image(),
        )
    assert captured.value.reason_code == "GPU_STORAGE_CAPACITY_ARITHMETIC_DIFFERS"


def test_image_identity_drift_refuses() -> None:
    expected = _expected_image()
    expected["linux_amd64_digest"] = "sha256:" + "0" * 64
    with pytest.raises(GpuStorageRefusal) as captured:
        validate_gpu_storage_prerequisite(
            ROOT, _policy(), _nodegroup(), expected_image=expected
        )
    assert captured.value.reason_code == "GPU_STORAGE_IMAGE_IDENTITY_DIFFERS"


def test_backing_asg_drift_refuses() -> None:
    nodegroup = copy.deepcopy(_nodegroup())
    nodegroup["resources"]["autoScalingGroups"][0]["name"] = "eks-gpu-drift"
    with pytest.raises(GpuStorageRefusal) as captured:
        validate_gpu_storage_prerequisite(
            ROOT, _policy(), nodegroup, expected_image=_expected_image()
        )
    assert captured.value.reason_code == "GPU_NODEGROUP_ASG_BINDING_DIFFERS"
