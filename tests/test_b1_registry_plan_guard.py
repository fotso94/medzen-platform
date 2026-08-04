from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_b1_registry_plan import EXPECTED_ACTIONS, EXPECTED_OUTPUTS, validate_plan


def _plan():
    return {
        "resource_changes": [
            {"address": address, "change": {"actions": list(actions)}}
            for address, actions in EXPECTED_ACTIONS.items()
        ],
        "output_changes": {
            name: {"actions": ["create"]} for name in EXPECTED_OUTPUTS
        },
    }


def test_exact_b1_packet_plan_passes():
    summary = validate_plan(_plan())
    assert summary["status"] == "PASS_EXACT_B1_PACKET_2026_002"
    assert (summary["add"], summary["change"], summary["destroy"]) == (2, 3, 0)


@pytest.mark.parametrize(
    ("address", "actions"),
    [
        ("aws_ssm_parameter.serving", ["create"]),
        ("aws_eks_cluster.this", ["update"]),
        ("aws_iam_role.registry_publisher", ["delete", "create"]),
    ],
)
def test_any_unexpected_or_destructive_change_refuses(address, actions):
    plan = _plan()
    plan["resource_changes"].append({
        "address": address,
        "change": {"actions": actions},
    })
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_missing_expected_change_refuses():
    plan = _plan()
    plan["resource_changes"].pop()
    with pytest.raises(ValueError, match="missing expected changes"):
        validate_plan(plan)


def test_output_drift_refuses():
    plan = copy.deepcopy(_plan())
    plan["output_changes"]["unexpected"] = {"actions": ["create"]}
    with pytest.raises(ValueError, match="output changes differ"):
        validate_plan(plan)
