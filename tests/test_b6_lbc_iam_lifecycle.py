from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_policy(ref: str | None = None) -> dict:
    if ref is None:
        text = (ROOT / "platform/iam/medzen-lbc-role.policy.template.json").read_text()
    else:
        import subprocess

        text = subprocess.run(
            ["git", "show", f"{ref}:platform/iam/medzen-lbc-role.policy.template.json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return json.loads(text.replace("${alb_security_group_id}", "sg-0f0f6c66852830013"))


def by_sid(policy: dict) -> dict[str, dict]:
    return {item["Sid"]: item for item in policy["Statement"]}


def actions(policy: dict) -> set[str]:
    result: set[str] = set()
    for item in policy["Statement"]:
        value = item["Action"]
        result.update(value if isinstance(value, list) else [value])
    return result


def test_create_actions_use_their_documented_parent_resources() -> None:
    policy = by_sid(load_policy())
    listener = policy["CreateOnlyExactB6ListenersOnClusterTaggedAlb"]
    rule = policy["CreateOnlyExactB6RulesOnClusterTaggedListener"]
    assert listener["Action"] == "elasticloadbalancing:CreateListener"
    assert ":loadbalancer/app/medzen-b6-*/*" in listener["Resource"]
    assert ":listener/" not in listener["Resource"]
    assert rule["Action"] == "elasticloadbalancing:CreateRule"
    assert ":listener/app/medzen-b6-*/*/*" in rule["Resource"]
    assert ":listener-rule/" not in rule["Resource"]
    for statement in (listener, rule):
        assert statement["Condition"]["StringEquals"] == {
            "aws:RequestedRegion": "eu-central-1",
            "aws:ResourceTag/elbv2.k8s.aws/cluster": "medzen-speech",
        }


def test_create_manage_and_tag_boundaries_preserve_actions_and_existing_statements() -> None:
    before = load_policy("master")
    after = load_policy()
    before_sids = by_sid(before)
    after_sids = by_sid(after)
    assert actions(before) == actions(after)
    assert not any(item["Effect"] == "Deny" for item in before["Statement"])
    assert not any(item["Effect"] == "Deny" for item in after["Statement"])
    assert after_sids["TagOnlyExactB6Resources"] == before_sids["TagOnlyExactB6Resources"]
    for sid in set(before_sids) - {"CreateAndManageExactB6ListenersAndRules"}:
        assert after_sids[sid] == before_sids[sid]


def test_dependent_tag_authorization_is_create_only_and_exactly_tag_constrained() -> None:
    statement = by_sid(load_policy())["TagOnlyDuringExactB6ListenerAndRuleCreation"]
    assert statement["Action"] == "elasticloadbalancing:AddTags"
    assert statement["Resource"] == "*"
    equals = statement["Condition"]["StringEquals"]
    assert equals["elasticloadbalancing:CreateAction"] == ["CreateListener", "CreateRule"]
    assert equals["aws:RequestedRegion"] == "eu-central-1"
    assert equals["aws:RequestTag/elbv2.k8s.aws/cluster"] == "medzen-speech"
    assert equals["aws:RequestTag/Project"] == "medzen-speech"
    assert equals["aws:RequestTag/Environment"] == "dev"
    assert equals["aws:RequestTag/Stage"] == "B6.6"
    assert equals["aws:RequestTag/Workstream"] == "integration-window"
    assert equals["aws:RequestTag/BudgetRegistry"] == "COST-REGISTRY-2026-003"
    assert statement["Condition"]["Null"] == {
        "aws:TagKeys": "false",
        "elasticloadbalancing:CreateAction": "false",
    }
    assert set(statement["Condition"]["ForAllValues:StringEquals"]["aws:TagKeys"]) == {
        "elbv2.k8s.aws/cluster", "ingress.k8s.aws/stack", "ingress.k8s.aws/resource",
        "Project", "Environment", "Stage", "Workstream", "BudgetRegistry",
    }


def test_lifecycle_matrix_covers_every_requested_pair_and_fail_closed_boundaries() -> None:
    lifecycle = load_module("b6_lbc_lifecycle", "scripts/simulate_b6_lbc_lifecycle.py")
    positive = {(item.action, item.resource) for item in lifecycle.REQUIRED_ALLOW}
    assert ("elasticloadbalancing:CreateListener", lifecycle.LOAD_BALANCER) in positive
    assert ("elasticloadbalancing:CreateRule", lifecycle.LISTENER) in positive
    assert ("elasticloadbalancing:ModifyListener", lifecycle.LISTENER) in positive
    assert ("elasticloadbalancing:ModifyRule", lifecycle.RULE) in positive
    assert ("elasticloadbalancing:DeleteListener", lifecycle.LISTENER) in positive
    assert ("elasticloadbalancing:DeleteRule", lifecycle.RULE) in positive
    observations = {(item.action, item.resource) for item in lifecycle.SIMULATOR_LIMITATION_OBSERVATIONS}
    assert ("elasticloadbalancing:AddTags", lifecycle.LISTENER) in observations
    assert ("elasticloadbalancing:AddTags", lifecycle.RULE) in observations
    assert ("elasticloadbalancing:RemoveTags", lifecycle.LISTENER) in observations
    assert ("elasticloadbalancing:RemoveTags", lifecycle.RULE) in observations
    assert all(item.expected_proposed == "implicitDeny" for item in lifecycle.REQUIRED_DENY)
    assert any(item.name == "deny_create_listener_outside_name" for item in lifecycle.REQUIRED_DENY)
    assert any(item.name == "deny_create_rule_outside_name" for item in lifecycle.REQUIRED_DENY)
    control = json.loads(lifecycle.SIMULATOR_CONTROL_POLICY)
    assert control["Statement"] == [{
        "Sid": "UnrestrictedTagControl",
        "Effect": "Allow",
        "Action": ["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"],
        "Resource": "*",
    }]
    source = (ROOT / "scripts/simulate_b6_lbc_lifecycle.py").read_text()
    assert '"live-postapply"' in source


def _fake_plan(before: dict, after: dict) -> dict:
    return {
        "resource_changes": [{
            "address": "aws_iam_role_policy.b6_load_balancer_controller",
            "change": {
                "actions": ["update"],
                "before": {"name": "medzen-lbc-access", "role": "medzen-lbc-role", "policy": json.dumps(before)},
                "after": {"name": "medzen-lbc-access", "role": "medzen-lbc-role", "policy": json.dumps(after)},
            },
        }]
    }


def test_terraform_guard_accepts_only_the_one_policy_update() -> None:
    guard = load_module("b6_lbc_iam_plan", "scripts/check_b6_lbc_iam_correction_plan.py")
    before = load_policy("master")
    after = load_policy()
    guard.validate(_fake_plan(before, after))

    extra = _fake_plan(before, after)
    extra["resource_changes"].append({
        "address": "aws_eks_node_group.cpu",
        "change": {"actions": ["update"], "before": {}, "after": {}},
    })
    with pytest.raises(ValueError, match="delta mismatch"):
        guard.validate(extra)


def test_terraform_guard_refuses_unrelated_statement_or_action_change() -> None:
    guard = load_module("b6_lbc_iam_plan_changed", "scripts/check_b6_lbc_iam_correction_plan.py")
    before = load_policy("master")
    after = load_policy()
    by_sid(after)["ReadOnlyDiscoveryInExactRegion"]["Action"].append("iam:CreateRole")
    with pytest.raises(ValueError):
        guard.validate(_fake_plan(before, after))


def test_packet_and_evidence_bind_full_lifecycle_without_authorizing_execution() -> None:
    evidence = json.loads(
        (ROOT / "platform/evidence/B6-LBC-IAM-LIFECYCLE-SIMULATION-2026-001.json").read_text()
    )
    assert evidence["status"] == "LOCAL_PREPARATION_COMPLETE_AWAITING_INDEPENDENT_REVIEW"
    assert evidence["simulations"]["live_before"]["required_positive_failures"] == [
        "create_listener_on_parent_load_balancer",
        "add_tags_during_create_listener",
        "add_tags_during_create_rule",
    ]
    assert evidence["simulations"]["live_role_plus_proposed_overlay"]["required_positive_allowed"] == 21
    isolated = evidence["simulations"]["proposed_policy_in_isolation"]
    assert isolated["required_positive_allowed"] == 21
    assert isolated["negative_boundary_implicit_denies"] == 19
    assert isolated["mismatches"] == 0
    assert evidence["terraform_plan"]["adds"] == 0
    assert evidence["terraform_plan"]["updates"] == 1
    assert evidence["terraform_plan"]["destroys"] == 0
    assert all(value == 0 for value in evidence["explicit_non_events"].values())
    assert sha(ROOT / evidence["simulations"]["script"]["path"]) == evidence["simulations"]["script"]["sha256"]
    assert sha(ROOT / evidence["terraform_plan"]["guard_path"]) == evidence["terraform_plan"]["guard_sha256"]

    packet = (
        ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-011-lbc-iam-lifecycle-correction.md"
    ).read_text()
    assert "DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL" in packet
    assert "0 add / 1 in-place update / 0 destroy" in packet
    assert "No worker scale-up" in packet
    assert "Approve B6 AWS change packet 2026-011 only." in packet
