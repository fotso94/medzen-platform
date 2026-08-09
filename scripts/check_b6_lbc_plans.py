#!/usr/bin/env python3
"""Validate the two-stage Terraform boundary for B6 packet 2026-005."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STAGE_A = {
    "aws_ecr_registry_scanning_configuration.b6a_runtime": ["update"],
    "aws_ecr_repository.b6_load_balancer_controller": ["create"],
}
STAGE_B = {
    "aws_security_group.b6_internal_alb": ["create"],
    "aws_iam_role.b6_load_balancer_controller": ["create"],
    "aws_iam_role_policy.b6_load_balancer_controller": ["create"],
    "aws_eks_pod_identity_association.b6_load_balancer_controller": ["create"],
}
FILTERS = {
    "medzen-model-loader", "medzen-asr-runtime", "medzen-nvidia-dra",
    "medzen-rag-index", "medzen-llm-gateway", "medzen-orchestrator",
    "medzen-speech-tts-gateway", "medzen-aws-load-balancer-controller",
}
DIGEST = "sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f"


def load(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'infra'}", "show", "-json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def changed(plan: dict[str, Any]) -> dict[str, list[str]]:
    return {
        item["address"]: item["change"]["actions"]
        for item in plan.get("resource_changes", [])
        if item["change"]["actions"] not in (["no-op"], ["read"])
    }


def validate_stage_a(plan: dict[str, Any]) -> None:
    if changed(plan) != STAGE_A:
        raise ValueError(f"Stage A delta mismatch: {changed(plan)!r}")
    repository = next(
        item for item in plan["resource_changes"]
        if item["address"] == "aws_ecr_repository.b6_load_balancer_controller"
    )["change"]["after"]
    if (
        repository.get("name") != "medzen-aws-load-balancer-controller"
        or repository.get("image_tag_mutability") != "IMMUTABLE"
        or repository.get("force_delete") is not False
        or repository["image_scanning_configuration"][0].get("scan_on_push") is not True
        or repository["encryption_configuration"][0].get("encryption_type") != "KMS"
    ):
        raise ValueError("controller repository hardening changed")
    scanning = next(
        item for item in plan["resource_changes"]
        if item["address"] == "aws_ecr_registry_scanning_configuration.b6a_runtime"
    )["change"]["after"]
    filters = {
        item["filter"] for rule in scanning["rule"]
        for item in rule["repository_filter"]
    }
    if filters != FILTERS:
        raise ValueError(f"registry scan filters differ: {filters!r}")


def validate_stage_b(plan: dict[str, Any]) -> None:
    if changed(plan) != STAGE_B:
        raise ValueError(f"Stage B delta mismatch: {changed(plan)!r}")
    security_group = next(
        item for item in plan["resource_changes"]
        if item["address"] == "aws_security_group.b6_internal_alb"
    )["change"]["after"]
    if security_group.get("vpc_id") != "vpc-051aa9df8b64bf141":
        raise ValueError("ALB security group VPC changed")
    ingress = security_group.get("ingress")
    if ingress not in (None, [], set()):
        raise ValueError("controller packet must create no ALB ingress")
    egress = security_group.get("egress") or []
    if len(egress) != 1 or egress[0].get("from_port") != 8080 or egress[0].get("to_port") != 8080:
        raise ValueError("ALB security group egress changed")
    association = next(
        item for item in plan["resource_changes"]
        if item["address"] == "aws_eks_pod_identity_association.b6_load_balancer_controller"
    )["change"]["after"]
    if association.get("namespace") != "kube-system" or association.get("service_account") != "aws-load-balancer-controller":
        raise ValueError("controller Pod Identity binding changed")
    if DIGEST not in (ROOT / "scripts/pin_aws_lbc_digest.py").read_text():
        raise ValueError("post-renderer digest binding changed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("a", "b"))
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = load(args.plan)
        (validate_stage_a if args.stage == "a" else validate_stage_b)(plan)
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6 LBC STAGE {args.stage.upper()}: {exc}", file=sys.stderr)
        return 2
    print(f"PASS_B6_LBC_STAGE_{args.stage.upper()} changes={len(changed(plan))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
