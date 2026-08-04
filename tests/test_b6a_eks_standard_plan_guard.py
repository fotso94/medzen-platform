from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_b6a_eks_standard_plan import validate_plan


def _plan(address="aws_eks_cluster.this", actions=None, outputs=None):
    return {
        "resource_changes": [
            {
                "address": address,
                "change": {"actions": actions or ["update"]},
            }
        ],
        "output_changes": outputs or {},
    }


def test_exact_support_policy_update_passes():
    result = validate_plan(_plan())
    assert result["status"] == "PASS_EXACT_B6A_PACKET_2026_004"
    assert (result["add"], result["change"], result["destroy"]) == (0, 1, 0)


@pytest.mark.parametrize(
    ("address", "actions"),
    [
        ("aws_eks_cluster.this", ["delete", "create"]),
        ("aws_eks_node_group.gpu", ["update"]),
        ("aws_ssm_parameter.registry", ["create"]),
    ],
)
def test_any_different_resource_or_action_refuses(address, actions):
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(_plan(address=address, actions=actions))


def test_additional_change_refuses():
    plan = _plan()
    plan["resource_changes"].append(
        {"address": "aws_eks_node_group.gpu", "change": {"actions": ["update"]}}
    )
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(plan)


def test_output_change_refuses():
    with pytest.raises(ValueError, match="unexpected output changes"):
        validate_plan(_plan(outputs={"cluster": {"actions": ["update"]}}))
