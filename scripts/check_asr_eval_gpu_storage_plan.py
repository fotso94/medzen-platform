#!/usr/bin/env python3
"""Fail closed unless a saved Terraform plan is only the GPU 20->40 GiB replacement."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADDRESS = "aws_eks_node_group.gpu"
EXPECTED_ACTIONS = ("delete", "create")
EXPECTED_REPLACE_PATHS = [["disk_size"]]
CONFIGURATION_KEYS = {
    "ami_type",
    "cluster_name",
    "disk_size",
    "force_update_version",
    "instance_types",
    "labels",
    "launch_template",
    "node_group_name",
    "node_role_arn",
    "remote_access",
    "scaling_config",
    "subnet_ids",
    "tags",
    "taint",
    "timeouts",
    "update_config",
}
EXPECTED_AFTER_UNKNOWN = {
    "arn": True,
    "capacity_type": True,
    "id": True,
    "instance_types": [False],
    "labels": {},
    "launch_template": [],
    "node_group_name_prefix": True,
    "node_repair_config": True,
    "release_version": True,
    "remote_access": [],
    "resources": True,
    "scaling_config": [{}],
    "status": True,
    "subnet_ids": [False, False, False],
    "tags_all": {},
    "taint": [{}],
    "update_config": [{}],
    "version": True,
}


def _normalized_configuration(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = {key: snapshot.get(key) for key in CONFIGURATION_KEYS}
    # Terraform represents provider-default empty tags as {} in prior state and
    # null in replacement configuration. tags_all is the effective tag set and
    # must remain byte-equivalent.
    result["tags"] = result.get("tags") or {}
    update_config = result.get("update_config")
    if update_config == [
        {"max_unavailable": 1, "max_unavailable_percentage": 0}
    ]:
        result["update_config"] = [
            {"max_unavailable": 1, "max_unavailable_percentage": None}
        ]
    return result


def _changed_resources(plan: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {
        resource.get("address", "<missing-address>"): tuple(
            resource.get("change", {}).get("actions", [])
        )
        for resource in plan.get("resource_changes", [])
        if tuple(resource.get("change", {}).get("actions", []))
        not in {("no-op",), ("read",)}
    }


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    changed = _changed_resources(plan)
    if changed != {ADDRESS: EXPECTED_ACTIONS}:
        raise ValueError(
            f"resource changes differ: got {changed}, "
            f"expected {{{ADDRESS!r}: {EXPECTED_ACTIONS!r}}}"
        )

    outputs = {
        name: tuple(change.get("actions", []))
        for name, change in plan.get("output_changes", {}).items()
        if tuple(change.get("actions", [])) != ("no-op",)
    }
    if outputs:
        raise ValueError(f"unexpected output changes: {outputs}")

    resources = [
        item for item in plan.get("resource_changes", []) if item.get("address") == ADDRESS
    ]
    if len(resources) != 1:
        raise ValueError(f"expected exactly one {ADDRESS} change record")
    change = resources[0].get("change", {})
    before = change.get("before")
    after = change.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("GPU node-group before/after snapshots are required")
    if before.get("disk_size") != 20 or after.get("disk_size") != 40:
        raise ValueError(
            "disk_size transition must be exactly 20 -> 40 GiB, got "
            f"{before.get('disk_size')!r} -> {after.get('disk_size')!r}"
        )
    if change.get("replace_paths") != EXPECTED_REPLACE_PATHS:
        raise ValueError(
            "replacement must be caused only by disk_size, got "
            f"{change.get('replace_paths')!r}"
        )

    if before.get("capacity_type") != "ON_DEMAND":
        raise ValueError(f"unexpected prior capacity_type: {before.get('capacity_type')!r}")
    if before.get("node_repair_config") != []:
        raise ValueError("unexpected prior node_repair_config")
    if change.get("after_unknown") != EXPECTED_AFTER_UNKNOWN:
        raise ValueError(
            "replacement after-state unknown fields differ from the reviewed shape: "
            f"{change.get('after_unknown')!r}"
        )

    before_config = _normalized_configuration(before)
    after_config = _normalized_configuration(after)
    before_config.pop("disk_size")
    after_config.pop("disk_size")
    if before_config != after_config:
        differences = {
            key: {"before": before_config.get(key), "after": after_config.get(key)}
            for key in sorted(before_config)
            if before_config.get(key) != after_config.get(key)
        }
        raise ValueError(
            f"GPU node-group configuration outside disk_size changed: {differences}"
        )

    scaling = after.get("scaling_config")
    if scaling != [{"desired_size": 0, "max_size": 1, "min_size": 0}]:
        raise ValueError(f"GPU scaling must remain 0/0/1, got {scaling!r}")
    if after.get("cluster_name") != "medzen-speech":
        raise ValueError("unexpected cluster_name")
    if after.get("node_group_name") != "gpu":
        raise ValueError("unexpected node_group_name")
    if after.get("instance_types") != ["g6.xlarge"]:
        raise ValueError(f"unexpected GPU instance type: {after.get('instance_types')!r}")
    if after.get("ami_type") != "AL2023_x86_64_NVIDIA":
        raise ValueError(f"unexpected GPU AMI type: {after.get('ami_type')!r}")
    if after.get("labels") != {"workload": "gpu"}:
        raise ValueError(f"unexpected GPU labels: {after.get('labels')!r}")
    if after.get("taint") != [
        {"effect": "NO_SCHEDULE", "key": "nvidia.com/gpu", "value": "true"}
    ]:
        raise ValueError(f"unexpected GPU taint: {after.get('taint')!r}")

    return {
        "status": "PASS_EXACT_ASR_BASE_MODEL_GPU_STORAGE_PACKET_2026_003",
        "summary": {"add": 1, "change": 0, "destroy": 1, "replacement": 1},
        "resource_actions": {ADDRESS: list(EXPECTED_ACTIONS)},
        "replacement_cause": "disk_size",
        "field_transition": {"disk_size_gib": {"before": 20, "after": 40}},
        "gpu_scaling": {"minimum": 0, "desired": 0, "maximum": 1},
    }


def load_saved_plan(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'infra'}", "show", "-json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "terraform show failed")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan_bytes = args.plan.read_bytes()
        summary = validate_plan(load_saved_plan(args.plan))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSING GPU STORAGE APPLY: {exc}", file=sys.stderr)
        return 2
    summary["saved_plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    summary["saved_plan_bytes"] = len(plan_bytes)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
