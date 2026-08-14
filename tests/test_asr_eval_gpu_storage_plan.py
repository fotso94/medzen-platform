from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_asr_eval_gpu_storage_plan import validate_plan


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


def _snapshot(disk_size: int, *, before: bool) -> dict:
    value = {
        "ami_type": "AL2023_x86_64_NVIDIA",
        "cluster_name": "medzen-speech",
        "disk_size": disk_size,
        "force_update_version": None,
        "instance_types": ["g6.xlarge"],
        "labels": {"workload": "gpu"},
        "launch_template": [],
        "node_group_name": "gpu",
        "node_role_arn": "arn:aws:iam::558069890522:role/medzen-speech-node-role",
        "remote_access": [],
        "scaling_config": [{"desired_size": 0, "max_size": 1, "min_size": 0}],
        "subnet_ids": ["subnet-a", "subnet-b", "subnet-c"],
        "tags": {} if before else None,
        "tags_all": {
            "Component": "speech-platform",
            "Environment": "dev",
            "ManagedBy": "terraform",
            "Project": "medzen-speech",
        },
        "taint": [
            {
                "effect": "NO_SCHEDULE",
                "key": "nvidia.com/gpu",
                "value": "true",
            }
        ],
        "timeouts": None,
        "update_config": [
            {
                "max_unavailable": 1,
                "max_unavailable_percentage": 0 if before else None,
            }
        ],
    }
    if before:
        value["capacity_type"] = "ON_DEMAND"
        value["node_repair_config"] = []
    return value


def _plan() -> dict:
    return {
        "resource_changes": [
            {
                "address": "aws_eks_node_group.gpu",
                "change": {
                    "actions": ["delete", "create"],
                    "before": _snapshot(20, before=True),
                    "after": _snapshot(40, before=False),
                    "after_unknown": copy.deepcopy(EXPECTED_AFTER_UNKNOWN),
                    "replace_paths": [["disk_size"]],
                },
            }
        ],
        "output_changes": {},
    }


def test_exact_gpu_storage_replacement_passes():
    result = validate_plan(_plan())
    assert result["status"] == (
        "PASS_EXACT_ASR_BASE_MODEL_GPU_STORAGE_PACKET_2026_003"
    )
    assert result["summary"] == {
        "add": 1,
        "change": 0,
        "destroy": 1,
        "replacement": 1,
    }
    assert result["gpu_scaling"]["desired"] == 0


@pytest.mark.parametrize(
    ("address", "actions"),
    [
        ("aws_eks_node_group.gpu", ["update"]),
        ("aws_eks_node_group.gpu", ["create", "delete"]),
        ("aws_eks_node_group.cpu", ["delete", "create"]),
        ("aws_iam_role.node", ["update"]),
    ],
)
def test_wrong_resource_or_action_refuses(address, actions):
    plan = _plan()
    plan["resource_changes"][0]["address"] = address
    plan["resource_changes"][0]["change"]["actions"] = actions
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(plan)


def test_extra_resource_or_output_change_refuses():
    extra = _plan()
    extra["resource_changes"].append(
        {"address": "aws_eks_cluster.this", "change": {"actions": ["update"]}}
    )
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(extra)

    output = _plan()
    output["output_changes"] = {"gpu": {"actions": ["update"]}}
    with pytest.raises(ValueError, match="unexpected output changes"):
        validate_plan(output)


@pytest.mark.parametrize(
    ("before", "after"),
    [(19, 40), (20, 39), (20, 41)],
)
def test_non_exact_disk_transition_refuses(before, after):
    plan = _plan()
    plan["resource_changes"][0]["change"]["before"]["disk_size"] = before
    plan["resource_changes"][0]["change"]["after"]["disk_size"] = after
    with pytest.raises(ValueError, match="exactly 20 -> 40"):
        validate_plan(plan)


def test_replacement_cause_must_be_disk_size_only():
    plan = _plan()
    plan["resource_changes"][0]["change"]["replace_paths"] = [
        ["disk_size"],
        ["ami_type"],
    ]
    with pytest.raises(ValueError, match="caused only by disk_size"):
        validate_plan(plan)


def test_unexpected_unknown_replacement_field_refuses():
    plan = _plan()
    plan["resource_changes"][0]["change"]["after_unknown"]["ami_type"] = True
    with pytest.raises(ValueError, match="unknown fields differ"):
        validate_plan(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instance_types", ["g6.2xlarge"]),
        ("node_role_arn", "arn:aws:iam::558069890522:role/other"),
        ("subnet_ids", ["subnet-a"]),
        ("labels", {"workload": "other"}),
    ],
)
def test_other_configuration_drift_refuses(field, value):
    plan = _plan()
    plan["resource_changes"][0]["change"]["after"][field] = value
    with pytest.raises(ValueError, match="outside disk_size changed"):
        validate_plan(plan)


def test_nonzero_gpu_desired_size_refuses():
    plan = _plan()
    plan["resource_changes"][0]["change"]["before"]["scaling_config"][0][
        "desired_size"
    ] = 1
    plan["resource_changes"][0]["change"]["after"]["scaling_config"][0][
        "desired_size"
    ] = 1
    with pytest.raises(ValueError, match="scaling must remain 0/0/1"):
        validate_plan(plan)


def test_noop_and_data_reads_are_ignored_but_mutation_is_not():
    plan = _plan()
    plan["resource_changes"].extend(
        [
            {"address": "data.aws_vpc.shared", "change": {"actions": ["read"]}},
            {"address": "aws_eks_cluster.this", "change": {"actions": ["no-op"]}},
        ]
    )
    assert validate_plan(plan)["replacement_cause"] == "disk_size"

    mutated = copy.deepcopy(plan)
    mutated["resource_changes"][-1]["change"]["actions"] = ["update"]
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(mutated)
