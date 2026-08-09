from copy import deepcopy
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_b6_5a_registry_plan import validate_plan  # noqa: E402


def policy(*, include_tags: bool) -> str:
    statements = [
        {
            "Sid": "ReadAndWriteExactRegistryPrefix",
            "Effect": "Allow",
            "Action": [
                "ssm:GetParameter", "ssm:GetParameters",
                "ssm:GetParametersByPath", "ssm:PutParameter",
            ],
            "Resource": "registry",
        },
        {
            "Sid": "DenyParameterDeletion",
            "Effect": "Deny",
            "Action": ["ssm:DeleteParameter", "ssm:DeleteParameters"],
            "Resource": "*",
        },
    ]
    if include_tags:
        keys = [
            "Project", "Environment", "CostCenter", "Stage", "Workstream",
            "BudgetRegistry",
        ]
        statements.extend([
            {
                "Sid": "TagRegistryParametersForCostAllocation",
                "Effect": "Allow",
                "Action": "ssm:AddTagsToResource",
                "Resource": "registry",
                "Condition": {
                    "ForAllValues:StringEquals": {"aws:TagKeys": keys},
                    "StringEquals": {
                        f"aws:RequestTag/{key}": "bound" for key in keys
                    },
                },
            },
            {
                "Sid": "ReadRegistryParameterTags",
                "Effect": "Allow",
                "Action": "ssm:ListTagsForResource",
                "Resource": "registry",
            },
        ])
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


def valid_plan() -> dict:
    return {
        "resource_changes": [{
            "address": "aws_iam_role_policy.registry_publisher",
            "change": {
                "actions": ["update"],
                "before": {"id": "policy", "name": "access", "policy": policy(include_tags=False)},
                "after": {"id": "policy", "name": "access", "policy": policy(include_tags=True)},
            },
        }],
        "output_changes": {},
    }


def test_exact_publisher_policy_delta_passes():
    result = validate_plan(valid_plan())
    assert result["add"] == 0
    assert result["change"] == 1
    assert result["destroy"] == 0
    assert result["global_delete_deny_retained"] is True


def test_unexpected_resource_or_field_change_refuses():
    plan = valid_plan()
    plan["resource_changes"].append({
        "address": "aws_ssm_parameter.unapproved",
        "change": {"actions": ["create"]},
    })
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(plan)
    plan = valid_plan()
    plan["resource_changes"][0]["change"]["after"]["name"] = "changed"
    with pytest.raises(ValueError, match="outside policy"):
        validate_plan(plan)


def test_missing_delete_deny_or_extra_action_refuses():
    plan = valid_plan()
    after = json.loads(plan["resource_changes"][0]["change"]["after"]["policy"])
    after["Statement"] = [
        item for item in after["Statement"]
        if item["Sid"] != "DenyParameterDeletion"
    ]
    plan["resource_changes"][0]["change"]["after"]["policy"] = json.dumps(after)
    with pytest.raises(ValueError, match="required read/write/delete-deny"):
        validate_plan(plan)

    plan = valid_plan()
    after = json.loads(plan["resource_changes"][0]["change"]["after"]["policy"])
    after["Statement"][0]["Action"].append("ssm:DeleteParameterHistory")
    plan["resource_changes"][0]["change"]["after"]["policy"] = json.dumps(after)
    with pytest.raises(ValueError, match="action delta differs"):
        validate_plan(plan)
