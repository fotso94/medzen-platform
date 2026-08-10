#!/usr/bin/env python3
"""Run the reviewed B6 ALB lifecycle through the AWS IAM simulator."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACCOUNT = "558069890522"
REGION = "eu-central-1"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/medzen-lbc-role"
ALB_SECURITY_GROUP = "sg-0f0f6c66852830013"
LOAD_BALANCER = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
    "loadbalancer/app/medzen-b6-window/1111111111111111"
)
LISTENER = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
    "listener/app/medzen-b6-window/1111111111111111/2222222222222222"
)
RULE = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
    "listener-rule/app/medzen-b6-window/1111111111111111/2222222222222222/3333333333333333"
)
TARGET_GROUP = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
    "targetgroup/k8s-medzen-test/4444444444444444"
)
OUTSIDE_LOAD_BALANCER = LOAD_BALANCER.replace("medzen-b6-window", "unrelated-window")
OUTSIDE_LISTENER = LISTENER.replace("medzen-b6-window", "unrelated-window")
OUTSIDE_RULE = RULE.replace("medzen-b6-window", "unrelated-window")

TAG_KEYS = (
    "elbv2.k8s.aws/cluster",
    "ingress.k8s.aws/stack",
    "ingress.k8s.aws/resource",
    "Project",
    "Environment",
    "Stage",
    "Workstream",
    "BudgetRegistry",
)
SUBNETS = (
    "subnet-00232b25bc1ac407a",
    "subnet-05029419c6c61a536",
    "subnet-01fb2fc3f56bce55e",
)
SIMULATOR_CONTROL_POLICY = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "UnrestrictedTagControl",
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:AddTags",
                "elasticloadbalancing:RemoveTags",
            ],
            "Resource": "*",
        }],
    },
    separators=(",", ":"),
)


@dataclass(frozen=True)
class Scenario:
    name: str
    action: str
    resource: str
    expected_proposed: str | None
    context_overrides: tuple[tuple[str, tuple[str, ...], str], ...] = ()
    context_removals: tuple[str, ...] = ()


def allow(name: str, action: str, resource: str) -> Scenario:
    return Scenario(name, action, resource, "allowed")


def deny(
    name: str,
    action: str,
    resource: str,
    *,
    overrides: tuple[tuple[str, tuple[str, ...], str], ...] = (),
    removals: tuple[str, ...] = (),
) -> Scenario:
    return Scenario(name, action, resource, "implicitDeny", overrides, removals)


REQUIRED_ALLOW = (
    allow("describe_load_balancers", "elasticloadbalancing:DescribeLoadBalancers", "*"),
    allow("create_load_balancer", "elasticloadbalancing:CreateLoadBalancer", "*"),
    allow("create_target_group", "elasticloadbalancing:CreateTargetGroup", "*"),
    allow("create_listener_on_parent_load_balancer", "elasticloadbalancing:CreateListener", LOAD_BALANCER),
    allow("create_rule_on_parent_listener", "elasticloadbalancing:CreateRule", LISTENER),
    allow("modify_listener", "elasticloadbalancing:ModifyListener", LISTENER),
    allow("modify_rule", "elasticloadbalancing:ModifyRule", RULE),
    allow("set_rule_priorities", "elasticloadbalancing:SetRulePriorities", RULE),
    Scenario(
        "add_tags_during_create_listener",
        "elasticloadbalancing:AddTags",
        "*",
        "allowed",
        (("elasticloadbalancing:CreateAction", ("CreateListener",), "string"),),
    ),
    Scenario(
        "add_tags_during_create_rule",
        "elasticloadbalancing:AddTags",
        "*",
        "allowed",
        (("elasticloadbalancing:CreateAction", ("CreateRule",), "string"),),
    ),
    allow("delete_listener", "elasticloadbalancing:DeleteListener", LISTENER),
    allow("delete_rule", "elasticloadbalancing:DeleteRule", RULE),
    allow("modify_load_balancer_attributes", "elasticloadbalancing:ModifyLoadBalancerAttributes", LOAD_BALANCER),
    allow("set_security_groups", "elasticloadbalancing:SetSecurityGroups", LOAD_BALANCER),
    allow("set_subnets", "elasticloadbalancing:SetSubnets", LOAD_BALANCER),
    allow("modify_target_group", "elasticloadbalancing:ModifyTargetGroup", TARGET_GROUP),
    allow("modify_target_group_attributes", "elasticloadbalancing:ModifyTargetGroupAttributes", TARGET_GROUP),
    allow("register_targets", "elasticloadbalancing:RegisterTargets", TARGET_GROUP),
    allow("deregister_targets", "elasticloadbalancing:DeregisterTargets", TARGET_GROUP),
    allow("delete_target_group", "elasticloadbalancing:DeleteTargetGroup", TARGET_GROUP),
    allow("delete_load_balancer", "elasticloadbalancing:DeleteLoadBalancer", LOAD_BALANCER),
)

REQUIRED_DENY = (
    deny("deny_public_load_balancer", "elasticloadbalancing:CreateLoadBalancer", "*", overrides=(("elasticloadbalancing:Scheme", ("internet-facing",), "string"),)),
    deny("deny_wrong_alb_security_group", "elasticloadbalancing:CreateLoadBalancer", "*", overrides=(("elasticloadbalancing:SecurityGroup", ("sg-00000000000000000",), "stringList"),)),
    deny("deny_wrong_alb_subnet", "elasticloadbalancing:CreateLoadBalancer", "*", overrides=(("elasticloadbalancing:Subnet", ("subnet-00000000000000000",), "stringList"),)),
    deny("deny_create_listener_outside_name", "elasticloadbalancing:CreateListener", OUTSIDE_LOAD_BALANCER),
    deny("deny_create_listener_on_child_arn", "elasticloadbalancing:CreateListener", LISTENER),
    deny("deny_create_listener_missing_cluster_tag", "elasticloadbalancing:CreateListener", LOAD_BALANCER, removals=("aws:ResourceTag/elbv2.k8s.aws/cluster",)),
    deny("deny_create_rule_outside_name", "elasticloadbalancing:CreateRule", OUTSIDE_LISTENER),
    deny("deny_create_rule_on_child_arn", "elasticloadbalancing:CreateRule", RULE),
    deny("deny_modify_listener_outside_name", "elasticloadbalancing:ModifyListener", OUTSIDE_LISTENER),
    deny("deny_modify_listener_on_rule_arn", "elasticloadbalancing:ModifyListener", RULE),
    deny("deny_modify_rule_outside_name", "elasticloadbalancing:ModifyRule", OUTSIDE_RULE),
    deny("deny_modify_rule_on_listener_arn", "elasticloadbalancing:ModifyRule", LISTENER),
    deny(
        "deny_create_listener_tags_unexpected_key",
        "elasticloadbalancing:AddTags",
        "*",
        overrides=(
            ("aws:TagKeys", ("Unexpected",), "stringList"),
            ("elasticloadbalancing:CreateAction", ("CreateListener",), "string"),
        ),
    ),
    deny(
        "deny_create_rule_tags_unexpected_key",
        "elasticloadbalancing:AddTags",
        "*",
        overrides=(
            ("aws:TagKeys", ("Unexpected",), "stringList"),
            ("elasticloadbalancing:CreateAction", ("CreateRule",), "string"),
        ),
    ),
    deny("deny_delete_listener_outside_name", "elasticloadbalancing:DeleteListener", OUTSIDE_LISTENER),
    deny("deny_delete_rule_outside_name", "elasticloadbalancing:DeleteRule", OUTSIDE_RULE),
    deny("deny_delete_load_balancer_missing_cluster_tag", "elasticloadbalancing:DeleteLoadBalancer", LOAD_BALANCER, removals=("aws:ResourceTag/elbv2.k8s.aws/cluster",)),
    deny("deny_delete_target_group_missing_cluster_tag", "elasticloadbalancing:DeleteTargetGroup", TARGET_GROUP, removals=("aws:ResourceTag/elbv2.k8s.aws/cluster",)),
    deny("deny_wrong_region", "elasticloadbalancing:ModifyListener", LISTENER, overrides=(("aws:RequestedRegion", ("us-east-1",), "string"),)),
)

# AWS's simulator currently returns implicitDeny for AddTags/RemoveTags when a
# listener or listener-rule ARN is supplied, even for a control policy with
# Resource="*". Keep these exact requested pairs in every report, but do not
# misrepresent that service-model limitation as a proposed-policy decision.
SIMULATOR_LIMITATION_OBSERVATIONS = (
    Scenario("observe_add_tags_listener_arn", "elasticloadbalancing:AddTags", LISTENER, None),
    Scenario("observe_add_tags_rule_arn", "elasticloadbalancing:AddTags", RULE, None),
    Scenario("observe_remove_tags_listener_arn", "elasticloadbalancing:RemoveTags", LISTENER, None),
    Scenario("observe_remove_tags_rule_arn", "elasticloadbalancing:RemoveTags", RULE, None),
)


def _base_context() -> dict[str, tuple[tuple[str, ...], str]]:
    return {
        "aws:RequestedRegion": ((REGION,), "string"),
        "aws:RequestTag/elbv2.k8s.aws/cluster": (("medzen-speech",), "string"),
        "aws:RequestTag/Project": (("medzen-speech",), "string"),
        "aws:RequestTag/Environment": (("dev",), "string"),
        "aws:RequestTag/Stage": (("B6.6",), "string"),
        "aws:RequestTag/Workstream": (("integration-window",), "string"),
        "aws:RequestTag/BudgetRegistry": (("COST-REGISTRY-2026-003",), "string"),
        "aws:ResourceTag/elbv2.k8s.aws/cluster": (("medzen-speech",), "string"),
        "aws:TagKeys": (TAG_KEYS, "stringList"),
        "elasticloadbalancing:Scheme": (("internal",), "string"),
        "elasticloadbalancing:SecurityGroup": ((ALB_SECURITY_GROUP,), "stringList"),
        "elasticloadbalancing:Subnet": (SUBNETS, "stringList"),
    }


def context_entries(scenario: Scenario) -> list[dict[str, Any]]:
    values = _base_context()
    for key in scenario.context_removals:
        values.pop(key, None)
    for key, override, kind in scenario.context_overrides:
        values[key] = (override, kind)
    return [
        {
            "ContextKeyName": key,
            "ContextKeyValues": list(content),
            "ContextKeyType": kind,
        }
        for key, (content, kind) in sorted(values.items())
    ]


def rendered_policy(path: Path) -> str:
    value = path.read_text()
    if value.count("${alb_security_group_id}") != 2:
        raise ValueError("ALB policy template placeholder count differs")
    rendered = value.replace("${alb_security_group_id}", ALB_SECURITY_GROUP)
    json.loads(rendered)
    return rendered


def _call_aws(
    scenario: Scenario,
    *,
    profile: str,
    mode: str,
    policy: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ActionNames": [scenario.action],
        "ResourceArns": [scenario.resource],
        "ContextEntries": context_entries(scenario),
    }
    if mode in ("custom-proposed", "custom-simulator-control"):
        if policy is None:
            raise ValueError("custom simulation requires the proposed policy")
        payload["PolicyInputList"] = [policy]
        operation = "simulate-custom-policy"
    else:
        payload["PolicySourceArn"] = ROLE_ARN
        if mode == "principal-overlay":
            if policy is None:
                raise ValueError("principal overlay requires the proposed policy")
            payload["PolicyInputList"] = [policy]
        operation = "simulate-principal-policy"
    process = subprocess.run(
        [
            "aws",
            "iam",
            operation,
            "--cli-input-json",
            json.dumps(payload, separators=(",", ":")),
            "--profile",
            profile,
            "--output",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(process.stdout)["EvaluationResults"]
    if len(result) != 1:
        raise RuntimeError(f"unexpected IAM simulation result count for {scenario.name}")
    item = result[0]
    return {
        "name": scenario.name,
        "action": scenario.action,
        "resource": scenario.resource,
        "decision": item["EvalDecision"],
        "missing_context": sorted(item.get("MissingContextValues", [])),
        "matched_statement_ids": sorted(
            entry.get("SourcePolicyId", "")
            for entry in item.get("MatchedStatements", [])
            if entry.get("SourcePolicyId")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "live-baseline",
            "live-postapply",
            "principal-overlay",
            "custom-proposed",
            "custom-simulator-control",
        ),
    )
    parser.add_argument("--profile", default="medzen")
    parser.add_argument(
        "--policy-template",
        type=Path,
        default=Path("platform/iam/medzen-lbc-role.policy.template.json"),
    )
    args = parser.parse_args()
    if args.mode in ("live-baseline", "live-postapply"):
        policy = None
    elif args.mode == "custom-simulator-control":
        policy = SIMULATOR_CONTROL_POLICY
    else:
        policy = rendered_policy(args.policy_template)
    scenarios = REQUIRED_ALLOW + REQUIRED_DENY + SIMULATOR_LIMITATION_OBSERVATIONS
    if args.mode == "principal-overlay":
        scenarios = REQUIRED_ALLOW
    elif args.mode == "custom-simulator-control":
        scenarios = SIMULATOR_LIMITATION_OBSERVATIONS
    results = [
        _call_aws(scenario, profile=args.profile, mode=args.mode, policy=policy)
        for scenario in scenarios
    ]
    mismatches: list[dict[str, str]] = []
    if args.mode != "live-baseline":
        for scenario, result in zip(scenarios, results, strict=True):
            if (
                scenario.expected_proposed is not None
                and result["decision"] != scenario.expected_proposed
            ):
                mismatches.append(
                    {
                        "name": scenario.name,
                        "expected": scenario.expected_proposed,
                        "actual": result["decision"],
                    }
                )
    output = {
        "schema": "MEDZEN_B6_LBC_IAM_LIFECYCLE_SIMULATION_V1",
        "mode": args.mode,
        "api": (
            "SimulateCustomPolicy"
            if args.mode in ("custom-proposed", "custom-simulator-control")
            else "SimulatePrincipalPolicy"
        ),
        "policy_source_arn": (
            ROLE_ARN
            if args.mode in ("live-baseline", "live-postapply", "principal-overlay")
            else None
        ),
        "proposed_policy_overlay": args.mode == "principal-overlay",
        "policy_template_sha256": (
            hashlib.sha256(args.policy_template.read_bytes()).hexdigest()
            if args.mode in ("principal-overlay", "custom-proposed")
            else None
        ),
        "control_policy_sha256": (
            hashlib.sha256(SIMULATOR_CONTROL_POLICY.encode()).hexdigest()
            if args.mode == "custom-simulator-control"
            else None
        ),
        "scenario_count": len(results),
        "allowed": sum(item["decision"] == "allowed" for item in results),
        "implicit_denies": sum(item["decision"] == "implicitDeny" for item in results),
        "explicit_denies": sum(item["decision"] == "explicitDeny" for item in results),
        "mismatches": mismatches,
        "results": results,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 2 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
