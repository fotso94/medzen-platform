#!/usr/bin/env python3
"""Classify tag denials only on the exact live B6 listener and three rules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ACCOUNT = "558069890522"
REGION = "eu-central-1"
ALB_NAME = "medzen-b6-window"
ALLOWED_ERRORS = {"AccessDenied", "AccessDeniedException"}
ALLOWED_ACTIONS = {
    "elasticloadbalancing:AddTags",
    "elasticloadbalancing:RemoveTags",
}
ARN = re.compile(
    rf"^arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
    rf"(?P<kind>listener|listener-rule)/app/{ALB_NAME}/(?P<suffix>[0-9a-f]+/[0-9a-f]+(?:/[0-9a-f]+)?)$"
)


class TagWarningRefusal(RuntimeError):
    pass


def validate_proof(proof: dict[str, Any]) -> None:
    required = {
        "internal_alb": True,
        "alb_security_group": "sg-0f0f6c66852830013",
        "listener_port": 80,
        "route_count": 3,
        "target_healthy": True,
        "creation_time_exact_tags": True,
        "tagged_resource_count": 5,
    }
    if any(proof.get(key) != value for key, value in required.items()):
        raise TagWarningRefusal("functional ALB prerequisite proof is incomplete")
    receipt_hash = proof.get("receipt_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt_hash)):
        raise TagWarningRefusal("functional ALB proof receipt hash is malformed")
    fargate_hash = proof.get("fargate_probe_receipt_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", str(fargate_hash)):
        raise TagWarningRefusal("functional Fargate proof receipt hash is malformed")
    resources = proof.get("tag_mutation_resource_arns")
    if not isinstance(resources, list) or len(resources) != 4 or len(set(resources)) != 4:
        raise TagWarningRefusal("exact tag-mutation resource set is absent")
    matches = [ARN.fullmatch(str(resource)) for resource in resources]
    if any(match is None for match in matches):
        raise TagWarningRefusal("tag-mutation resource ARN differs")
    kinds = [match.group("kind") for match in matches if match is not None]
    if kinds.count("listener") != 1 or kinds.count("listener-rule") != 3:
        raise TagWarningRefusal("tag-mutation resource kinds differ")


def classify(observations: list[dict[str, Any]], proof: dict[str, Any]) -> dict[str, Any]:
    validate_proof(proof)
    allowed_resources = set(proof["tag_mutation_resource_arns"])
    normalized: list[dict[str, Any]] = []
    for observation in observations:
        action = observation.get("operation")
        error = observation.get("error_code")
        resource = observation.get("resource_arn")
        observed = observation.get("observed_utc")
        timing = observation.get("timing")
        match = ARN.fullmatch(str(resource))
        kind_shape_is_exact = match is not None and len(match.group("suffix").split("/")) == (
            2 if match.group("kind") == "listener" else 3
        )
        if (
            action not in ALLOWED_ACTIONS
            or error not in ALLOWED_ERRORS
            or match is None
            or not kind_shape_is_exact
            or resource not in allowed_resources
            or timing != "POST_CREATE"
            or not isinstance(observed, str)
            or not observed.endswith("Z")
        ):
            raise TagWarningRefusal("an observation is outside the exact non-fatal allowlist")
        normalized.append(
            {
                "operation": action,
                "error_code": error,
                "resource_arn": resource,
                "resource_type": match.group("kind"),
                "observed_utc": observed,
            }
        )
    return {
        "status": "WARNING_NON_FATAL" if normalized else "PASS_NO_TAG_MUTATION_DENIAL",
        "warning_count": len(normalized),
        "warnings": normalized,
        "functional_alb_proof_receipt_sha256": proof["receipt_sha256"],
        "functional_fargate_probe_receipt_sha256": proof["fargate_probe_receipt_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--functional-proof", type=Path, required=True)
    args = parser.parse_args()
    try:
        observations = json.loads(args.observations.read_bytes())
        proof = json.loads(args.functional_proof.read_bytes())
        if not isinstance(observations, list) or not isinstance(proof, dict):
            raise TagWarningRefusal("inputs have wrong types")
        result = classify(observations, proof)
    except (OSError, json.JSONDecodeError, TagWarningRefusal) as exc:
        print(f"REFUSING B6 LBC TAG-WARNING CLASSIFICATION: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
