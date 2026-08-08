from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_b6a_003c_d_iam_plan import PlanRefusal, check


def plan(actions=("create",), address="aws_iam_role_policy.node_ssm_core"):
    return {"resource_changes": [{
        "address": address,
        "change": {
            "actions": list(actions),
            "after": {
                "name": "medzen-speech-node-ssm-core",
                "role": "medzen-speech-node-role",
                "policy": json.dumps(json.loads(
                    (ROOT / "platform/iam/medzen-node-ssm-core.json").read_bytes()
                )),
            },
        },
    }]}


def test_guard_accepts_only_one_exact_create():
    assert check(plan())["status"] == "PASS_EXACTLY_ONE_IAM_CREATE"
    with pytest.raises(PlanRefusal, match="unexpected resource"):
        check(plan(address="aws_iam_role_policy.trainer"))
    with pytest.raises(PlanRefusal, match="create-only"):
        check(plan(actions=("update",)))


def test_guard_refuses_any_second_change():
    value = plan()
    value["resource_changes"].append({
        "address": "aws_ssm_parameter.serving",
        "change": {"actions": ["create"], "after": {}},
    })
    with pytest.raises(PlanRefusal, match="exactly one"):
        check(value)
