#!/usr/bin/env python3
"""Exact mutation inventory and fail-closed plan guard for the pilot successor."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
CLUSTER = "medzen-speech"
VPC = "vpc-051aa9df8b64bf141"
LEGACY_GPU_ASG = "eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26"
NODE_SG = "sg-070fc00321934eacb"
NAMESPACE = "medzen-asr-eval"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def exact_plan(bindings: dict[str, Any], attempt: int) -> dict[str, Any]:
    if attempt not in set(range(1, 42)):
        raise ValueError("attempt must be 1 through 41")
    image_digest = bindings.get("image", {}).get("linux_amd64_digest")
    image_index = bindings.get("image", {}).get("oci_index_digest")
    image_tag = bindings.get("image", {}).get("tag")
    bundle_sha = bindings.get("pilot_bundle", {}).get("sha256")
    if not isinstance(image_digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
        raise ValueError("deployable image digest is absent")
    if not isinstance(bundle_sha, str) or SHA_RE.fullmatch(bundle_sha) is None:
        raise ValueError("pilot bundle hash is absent")
    if image_index is None:
        image_index = "sha256:" + "0" * 64
    if not isinstance(image_index, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_index) is None:
        raise ValueError("OCI index digest is malformed")
    if not isinstance(image_tag, str) or re.fullmatch(r"[a-zA-Z0-9_.-]{1,300}", image_tag) is None:
        raise ValueError("immutable image tag is absent or malformed")
    gpu_asg = LEGACY_GPU_ASG
    if attempt >= 20:
        gpu_asg = bindings.get("aws", {}).get("gpu_asg_name")
        if (
            not isinstance(gpu_asg, str)
            or re.fullmatch(r"eks-gpu-[0-9a-f-]{36}", gpu_asg) is None
        ):
            raise ValueError("attempt 20 or later requires the exact current GPU ASG binding")
    permanent_create_only = [] if attempt >= 9 else [
        f"s3:medzen-speech/research/asr-base-model/pilot/{bundle_sha}/**",
    ]
    temporary_create_then_delete = [
        "autoscaling:scheduled-action/medzen-asr-eval-2026-001-deadline-scale-zero",
        "ec2:gp3-volume/medzen-asr-eval-60gib-kms-encrypted",
        "ec2:volume-attachment/gpu-node:/var/lib/medzen-asr-eval",
        "ec2:security-group/medzen-asr-eval-vpce",
        "ec2:vpc-endpoint/com.amazonaws.eu-central-1.ecr.api",
        "ec2:vpc-endpoint/com.amazonaws.eu-central-1.ecr.dkr",
        "ec2:vpc-endpoint/com.amazonaws.eu-central-1.s3",
        "eks:addon-configuration/vpc-cni-network-policy-strict",
        "kubernetes:namespace/medzen-asr-eval",
        "kubernetes:namespace/nvidia-dra-driver",
        "kubernetes:nvidia-dra-driver/exact-locked-manifest",
        "kubernetes:resourceclaimtemplate/asr-eval-gpu",
        "kubernetes:networkpolicy/asr-eval-default-deny",
        "kubernetes:networkpolicy/asr-eval-private-egress",
        "kubernetes:job/asr-base-model-pilot",
        "kubernetes:pod/asr-eval-inbound-control",
        "node-local:/var/lib/medzen-asr-eval/attempt",
    ]
    if attempt >= 15:
        temporary_create_then_delete.insert(
            temporary_create_then_delete.index(
                "kubernetes:nvidia-dra-driver/exact-locked-manifest"
            ),
            "kubernetes:networkpolicy/nvidia-dra-driver/medzen-dra-kubernetes-api-egress",
        )
    image_publication_required = bindings.get("image", {}).get(
        "publication_required", False
    )
    if not isinstance(image_publication_required, bool):
        raise ValueError("image publication requirement must be boolean")
    if attempt < 5 or image_publication_required:
        permanent_create_only[:0] = [
            f"ecr:repository/medzen-asr-eval-runtime:oci-index/{image_index}",
            f"ecr:repository/medzen-asr-eval-runtime:tag/{image_tag}",
            "ecr:repository/medzen-asr-eval-runtime:content-addressed-blobs/from-verified-oci-layout",
        ]
        temporary_create_then_delete.insert(
            1,
            "ecr:registry-scanning-configuration/merge-exact-filter-then-restore-prior-filter-list",
        )
    return {
        "schema_version": 1,
        "classification": "OFFLINE_EVALUATION_ONLY",
        "attempt": attempt,
        "account": ACCOUNT,
        "region": REGION,
        "profile": PROFILE,
        "cluster": CLUSTER,
        "vpc": VPC,
        "permanent_create_only": permanent_create_only,
        "permanent_bounded_update": [],
        "temporary_create_then_delete": temporary_create_then_delete,
        "bounded_capacity_change": [
            f"autoscaling:{gpu_asg}/desired=1-then-0",
        ],
        "read_only_existing": [
            f"ec2:security-group/{NODE_SG}",
            "ecr:repository/medzen-asr-eval-runtime",
            "ecr:repository/medzen-nvidia-dra",
            "s3:medzen-speech/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/**",
            "s3:medzen-speech/eval/**",
            f"s3:medzen-speech/research/asr-base-model/pilot/{bundle_sha}/**",
        ],
        "prohibited_substrings": [
            "iam:", "kms:create", "ssm:parameter", "approved/asr",
            "/medzen/registry/production", "elasticloadbalancing:", "service/",
            "ingress/", "mlflow", "model-registration",
        ],
        "image_digest": image_digest,
    }


def validate_plan(plan: dict[str, Any], bindings: dict[str, Any], attempt: int) -> dict[str, Any]:
    expected = exact_plan(bindings, attempt)
    if plan != expected:
        raise ValueError("execution plan differs from the exact allowlist")
    inspected = {key: value for key, value in plan.items() if key != "prohibited_substrings"}
    flattened = json.dumps(inspected, sort_keys=True).casefold()
    for prohibited in expected["prohibited_substrings"]:
        if prohibited.casefold() in flattened:
            raise ValueError(f"execution plan contains prohibited scope: {prohibited}")
    return {
        "status": "PASS_EXACT_EXECUTION_PLAN",
        "attempt": attempt,
        "permanent_create_only": len(plan["permanent_create_only"]),
        "permanent_bounded_update": len(plan["permanent_bounded_update"]),
        "temporary_create_then_delete": len(plan["temporary_create_then_delete"]),
        "bounded_capacity_change": len(plan["bounded_capacity_change"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("render", "validate"))
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    bindings = json.loads(args.bindings.read_bytes())
    try:
        plan = exact_plan(bindings, args.attempt)
        if args.mode == "validate":
            if args.plan is None:
                raise ValueError("--plan is required for validate")
            result = validate_plan(json.loads(args.plan.read_bytes()), bindings, args.attempt)
        else:
            result = plan
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
