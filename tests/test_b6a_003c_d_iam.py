from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "platform/iam/medzen-node-ssm-core.json"


def test_node_ssm_policy_is_frozen_and_contains_no_data_sink_permissions():
    policy = json.loads(POLICY.read_bytes())
    actions = {
        action
        for statement in policy["Statement"]
        for action in statement["Action"]
    }
    assert "ssm:UpdateInstanceInformation" in actions
    assert "ssmmessages:OpenControlChannel" in actions
    assert "ec2messages:GetMessages" in actions
    assert not any(action.startswith("s3:") for action in actions)
    assert not any(action.startswith("logs:") for action in actions)
    assert "ssm:GetParameter" not in actions
    assert "ssm:GetParameters" not in actions
    assert "ssm:PutInventory" not in actions
    assert "ssm:SendCommand" not in actions


def test_terraform_change_is_one_reviewable_inline_policy_on_shared_node_role():
    source = (ROOT / "infra/eks.tf").read_text()
    block = source[source.index('resource "aws_iam_role_policy" "node_ssm_core"'):]
    block = block[:block.index("# ---- CPU node group")]
    assert 'name   = "${var.name}-node-ssm-core"' in block
    assert "role   = aws_iam_role.node.id" in block
    assert "medzen-node-ssm-core.json" in block
    assert "independent IAM review" in source
