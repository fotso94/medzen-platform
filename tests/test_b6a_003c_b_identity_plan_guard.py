from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_b6a_003c_b_identity_plan import validate_plan  # noqa: E402


def _change(address, after, actions=None):
    return {"address": address, "change": {"actions": actions or ["create"], "after": after}}


def _plan():
    trust = (ROOT / "platform/iam/b6a/medzen-b6a-asr-role.trust.template.json").read_text()
    policy = (ROOT / "platform/iam/b6a/medzen-b6a-asr-role.policy.template.json").read_text()
    return {
        "resource_changes": [
            _change("aws_iam_role.b6a_asr", {
                "name": "medzen-b6a-asr-role",
                "max_session_duration": 3600,
                "assume_role_policy": trust,
            }),
            _change("aws_iam_role_policy.b6a_asr", {
                "name": "medzen-b6a-asr-access", "policy": policy,
            }),
            _change("aws_eks_pod_identity_association.b6a_asr", {
                "cluster_name": "medzen-speech",
                "namespace": "medzen",
                "service_account": "asr-runtime-b6a",
            }),
        ],
        "output_changes": {},
    }


def test_exact_003c_b_identity_plan_passes():
    result = validate_plan(_plan())
    assert result["status"] == "PASS_EXACT_B6A_PACKET_2026_003C_B_IDENTITY_PHASE"
    assert (result["add"], result["change"], result["destroy"]) == (3, 0, 0)


def test_identity_policy_drift_or_gpu_change_refuses():
    plan = _plan()
    plan["resource_changes"][1]["change"]["after"]["policy"] = json.dumps({})
    with pytest.raises(ValueError, match="inline policy differs"):
        validate_plan(plan)
    plan = _plan()
    plan["resource_changes"].append(
        _change("aws_eks_node_group.gpu", {"desired_size": 1}, ["update"])
    )
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(plan)
