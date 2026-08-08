from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_b6a_003a_tf_plan import KMS_KEY_ARN, validate_plan


def _change(address, after, actions=None):
    return {
        "address": address,
        "change": {
            "actions": actions or ["create"],
            "before": None,
            "after": after,
            "after_unknown": {},
        },
    }


def _ecr_plan():
    return {
        "resource_changes": [
            _change(
                "aws_ecr_repository.b6a_nvidia_dra",
                {
                    "name": "medzen-nvidia-dra",
                    "image_tag_mutability": "IMMUTABLE",
                    "force_delete": False,
                    "image_scanning_configuration": [{"scan_on_push": True}],
                    "encryption_configuration": [
                        {"encryption_type": "KMS", "kms_key": KMS_KEY_ARN}
                    ],
                },
            )
        ],
        "output_changes": {},
    }


def _identity_plan():
    trust = (
        ROOT
        / "platform/iam/b6a/medzen-b6a-asr-role.trust.template.json"
    ).read_text()
    policy = (
        ROOT
        / "platform/iam/b6a/medzen-b6a-asr-role.policy.template.json"
    ).read_text()
    return {
        "resource_changes": [
            _change(
                "aws_iam_role.b6a_asr",
                {
                    "name": "medzen-b6a-asr-role",
                    "max_session_duration": 3600,
                    "assume_role_policy": trust,
                },
            ),
            _change(
                "aws_iam_role_policy.b6a_asr",
                {"name": "medzen-b6a-asr-access", "policy": policy},
            ),
            _change(
                "aws_eks_pod_identity_association.b6a_asr",
                {
                    "cluster_name": "medzen-speech",
                    "namespace": "medzen",
                    "service_account": "asr-runtime-b6a",
                },
            ),
        ],
        "output_changes": {},
    }


def test_exact_ecr_phase_passes():
    result = validate_plan(_ecr_plan(), "ecr")
    assert result["status"] == "PASS_EXACT_B6A_PACKET_2026_003A_ECR_PHASE"
    assert (result["add"], result["change"], result["destroy"]) == (1, 0, 0)


def test_exact_identity_phase_passes():
    result = validate_plan(_identity_plan(), "identity")
    assert result["status"] == "PASS_EXACT_B6A_PACKET_2026_003A_IDENTITY_PHASE"
    assert (result["add"], result["change"], result["destroy"]) == (3, 0, 0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "wrong"),
        ("image_tag_mutability", "MUTABLE"),
        ("force_delete", True),
        ("image_scanning_configuration", [{"scan_on_push": False}]),
        ("encryption_configuration", [{"encryption_type": "AES256"}]),
    ],
)
def test_ecr_phase_refuses_any_boundary_change(field, value):
    plan = _ecr_plan()
    plan["resource_changes"][0]["change"]["after"][field] = value
    with pytest.raises(ValueError, match="DRA repository"):
        validate_plan(plan, "ecr")


def test_identity_phase_refuses_policy_or_identity_drift():
    policy_drift = _identity_plan()
    policy = policy_drift["resource_changes"][1]["change"]["after"]
    policy["policy"] = json.dumps({"Version": "2012-10-17", "Statement": []})
    with pytest.raises(ValueError, match="inline policy differs"):
        validate_plan(policy_drift, "identity")

    identity_drift = _identity_plan()
    association = identity_drift["resource_changes"][2]["change"]["after"]
    association["namespace"] = "default"
    with pytest.raises(ValueError, match="namespace differs"):
        validate_plan(identity_drift, "identity")


@pytest.mark.parametrize("phase", ["ecr", "identity"])
def test_every_extra_or_destructive_change_refuses(phase):
    plan = _ecr_plan() if phase == "ecr" else _identity_plan()
    plan["resource_changes"].append(
        _change("aws_eks_node_group.gpu", {"desired_size": 1}, ["update"])
    )
    with pytest.raises(ValueError, match="resource changes differ"):
        validate_plan(plan, phase)


def test_output_change_and_unknown_phase_refuse():
    output = _ecr_plan()
    output["output_changes"] = {"unexpected": {"actions": ["create"]}}
    with pytest.raises(ValueError, match="unexpected output changes"):
        validate_plan(output, "ecr")
    with pytest.raises(ValueError, match="unknown phase"):
        validate_plan(_ecr_plan(), "all")
