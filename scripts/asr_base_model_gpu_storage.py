#!/usr/bin/env python3
"""Pre-envelope GPU node-root capacity gate for the offline ASR pilot."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


GIB = 1024**3


class GpuStorageRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GpuStorageRefusal(
            "GPU_STORAGE_POLICY_MALFORMED",
            f"GPU storage field must be one positive integer: {field}",
        )
    return value


def _bound_json(root: Path, binding: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(binding, dict):
        raise GpuStorageRefusal(
            "GPU_STORAGE_POLICY_MALFORMED", f"{label} binding is absent"
        )
    relative = binding.get("path")
    expected = binding.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected, str):
        raise GpuStorageRefusal(
            "GPU_STORAGE_POLICY_MALFORMED", f"{label} binding is malformed"
        )
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
        body = path.read_bytes()
    except (ValueError, OSError) as exc:
        raise GpuStorageRefusal(
            "GPU_STORAGE_EVIDENCE_ABSENT", f"{label} evidence is unavailable"
        ) from exc
    if hashlib.sha256(body).hexdigest() != expected:
        raise GpuStorageRefusal(
            "GPU_STORAGE_EVIDENCE_HASH_DIFFERS", f"{label} evidence hash differs"
        )
    try:
        value = json.loads(body)
    except Exception as exc:
        raise GpuStorageRefusal(
            "GPU_STORAGE_EVIDENCE_MALFORMED", f"{label} evidence is malformed"
        ) from exc
    if not isinstance(value, dict):
        raise GpuStorageRefusal(
            "GPU_STORAGE_EVIDENCE_MALFORMED", f"{label} evidence is not an object"
        )
    return value


def validate_gpu_storage_prerequisite(
    root: Path,
    policy: Any,
    nodegroup: Any,
    *,
    expected_image: dict[str, Any],
) -> dict[str, Any]:
    """Validate immutable capacity evidence and the current live node group."""
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise GpuStorageRefusal(
            "GPU_STORAGE_POLICY_MALFORMED", "GPU storage policy schema differs"
        )
    capacity = _bound_json(root, policy.get("capacity_qualification"), label="capacity")
    applied = _bound_json(root, policy.get("storage_apply_evidence"), label="storage apply")
    fixture = _bound_json(root, policy.get("live_fixture"), label="live fixture")

    if capacity.get("status") != "PASS_MEASURED_CAPACITY_DIAGNOSIS":
        raise GpuStorageRefusal(
            "GPU_STORAGE_EVIDENCE_DIFFERS", "capacity evidence is not PASS"
        )
    if applied.get("status") != "VERIFIED_COMPLETE":
        raise GpuStorageRefusal(
            "GPU_STORAGE_EVIDENCE_DIFFERS", "storage apply evidence is not complete"
        )
    if fixture.get("nodegroup", {}).get("diskSize") != 40:
        raise GpuStorageRefusal(
            "GPU_STORAGE_EVIDENCE_DIFFERS", "recorded live fixture is not 40 GiB"
        )

    bound_image = policy.get("image")
    if not isinstance(bound_image, dict) or any(
        bound_image.get(field) != expected_image.get(field)
        for field in ("oci_index_digest", "linux_amd64_digest")
    ):
        raise GpuStorageRefusal(
            "GPU_STORAGE_IMAGE_IDENTITY_DIFFERS",
            "GPU storage measurements are not bound to the evaluated image",
        )
    qualified_image = capacity.get("qualified_image", {})
    if (
        qualified_image.get("oci_index_digest") != bound_image.get("oci_index_digest")
        or qualified_image.get("linux_amd64_child_digest")
        != bound_image.get("linux_amd64_digest")
    ):
        raise GpuStorageRefusal(
            "GPU_STORAGE_IMAGE_IDENTITY_DIFFERS",
            "capacity evidence image identity differs",
        )

    measured = capacity.get("capacity_policy_derivation", {})
    archive = _positive_integer(measured.get("archive_bytes"), field="archive_bytes")
    unpacked = _positive_integer(
        measured.get("unpacked_rootfs_bytes"), field="unpacked_rootfs_bytes"
    )
    system_reserve = _positive_integer(
        measured.get("observed_system_and_eviction_reserve_bytes"),
        field="observed_system_and_eviction_reserve_bytes",
    )
    workload_reserve = _positive_integer(
        measured.get("workload_emptydir_reserve_bytes"),
        field="workload_emptydir_reserve_bytes",
    )
    margin = _positive_integer(
        measured.get("safety_margin_bytes"), field="safety_margin_bytes"
    )
    calculated = archive + unpacked + system_reserve + workload_reserve + margin
    if (
        measured.get("pull_plus_unpack_peak_bytes") != archive + unpacked
        or measured.get("calculated_required_bytes") != calculated
        or measured.get("rounded_calculated_minimum_gib") != math.ceil(calculated / GIB)
    ):
        raise GpuStorageRefusal(
            "GPU_STORAGE_CAPACITY_ARITHMETIC_DIFFERS",
            "capacity evidence arithmetic differs",
        )
    operational_floor = _positive_integer(
        policy.get("operational_floor_gib"), field="operational_floor_gib"
    )
    if (
        operational_floor != 40
        or measured.get("recommended_operational_floor_gib") != operational_floor
        or applied.get("post_apply_readback", {}).get("disk_size_gib")
        != operational_floor
        or applied.get("conclusion", {}).get("gpu_root_volume_gib")
        != operational_floor
    ):
        raise GpuStorageRefusal(
            "GPU_STORAGE_OPERATIONAL_FLOOR_DIFFERS",
            "the reviewed and applied GPU storage floor differs",
        )

    if not isinstance(nodegroup, dict):
        raise GpuStorageRefusal(
            "GPU_NODEGROUP_STORAGE_RESPONSE_MALFORMED",
            "GPU node-group response is not an object",
        )
    disk_size = nodegroup.get("diskSize")
    if isinstance(disk_size, bool) or not isinstance(disk_size, int):
        raise GpuStorageRefusal(
            "GPU_NODEGROUP_STORAGE_RESPONSE_MALFORMED",
            "GPU node-group diskSize is absent or malformed",
        )
    if disk_size < operational_floor:
        raise GpuStorageRefusal(
            "GPU_ROOT_VOLUME_BELOW_OPERATIONAL_FLOOR",
            f"GPU root volume {disk_size} GiB is below the {operational_floor} GiB floor",
        )
    scaling = nodegroup.get("scalingConfig")
    health = nodegroup.get("health")
    resources = nodegroup.get("resources")
    groups = resources.get("autoScalingGroups") if isinstance(resources, dict) else None
    if (
        nodegroup.get("status") != "ACTIVE"
        or scaling != {"minSize": 0, "maxSize": 1, "desiredSize": 0}
        or not isinstance(health, dict)
        or health.get("issues") != []
        or nodegroup.get("instanceTypes") != ["g6.xlarge"]
        or nodegroup.get("amiType") != "AL2023_x86_64_NVIDIA"
        or not isinstance(groups, list)
        or len(groups) != 1
        or not isinstance(groups[0].get("name"), str)
    ):
        raise GpuStorageRefusal(
            "GPU_NODEGROUP_STORAGE_STATE_DIFFERS",
            "GPU node-group capacity preconditions differ",
        )
    asg = policy.get("gpu_asg_name")
    if groups[0]["name"] != asg:
        raise GpuStorageRefusal(
            "GPU_NODEGROUP_ASG_BINDING_DIFFERS",
            "GPU node-group backing ASG differs from the reviewed binding",
        )

    return {
        "status": "PASS_PRE_ENVELOPE_GPU_STORAGE",
        "image": dict(bound_image),
        "archive_bytes": archive,
        "unpacked_rootfs_bytes": unpacked,
        "calculated_required_bytes": calculated,
        "calculated_minimum_gib": math.ceil(calculated / GIB),
        "operational_floor_gib": operational_floor,
        "live_root_volume_gib": disk_size,
        "headroom_above_calculated_requirement_bytes": disk_size * GIB - calculated,
        "gpu_asg_name": asg,
        "gpu_desired": 0,
        "aws_read_calls": 1,
        "aws_mutations": 0,
        "attempt_envelope_created": False,
        "attempt_number_consumed": False,
    }
