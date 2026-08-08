from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_b6a_eks_standard_plan import validate_plan


def _plan(address="aws_eks_cluster.this", actions=None, outputs=None):
    before = {
        "name": "medzen-speech",
        "version": "1.36",
        "upgrade_policy": [{"support_type": "EXTENDED"}],
    }
    after = {
        **before,
        "upgrade_policy": [{"support_type": "STANDARD"}],
    }
    return {
        "resource_changes": [
            {
                "address": address,
                "change": {
                    "actions": actions or ["update"],
                    "before": before,
                    "after": after,
                    "after_unknown": {},
                },
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


@pytest.mark.parametrize(
    ("snapshot", "policy", "message"),
    [
        ("before", "STANDARD", "support policy must start"),
        ("after", "EXTENDED", "support policy must end"),
    ],
)
def test_wrong_support_policy_transition_refuses(snapshot, policy, message):
    plan = _plan()
    plan["resource_changes"][0]["change"][snapshot]["upgrade_policy"] = [
        {"support_type": policy}
    ]
    with pytest.raises(ValueError, match=message):
        validate_plan(plan)


def test_any_other_cluster_attribute_change_refuses():
    plan = _plan()
    plan["resource_changes"][0]["change"]["after"]["version"] = "1.37"
    with pytest.raises(ValueError, match="outside upgrade_policy changed"):
        validate_plan(plan)


def test_missing_or_unknown_after_state_refuses():
    missing = _plan()
    missing["resource_changes"][0]["change"].pop("after")
    with pytest.raises(ValueError, match="before/after snapshots are required"):
        validate_plan(missing)

    unknown = _plan()
    unknown["resource_changes"][0]["change"]["after_unknown"] = {
        "platform_version": True
    }
    with pytest.raises(ValueError, match="unknown values"):
        validate_plan(unknown)
