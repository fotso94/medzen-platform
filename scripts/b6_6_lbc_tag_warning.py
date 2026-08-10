#!/usr/bin/env python3
"""Classify only four exact post-create LBC tag denials as non-fatal warnings."""

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
        "target_healthy": True,
        "orchestrator_readyz": True,
        "fargate_probe_exit_code": 0,
        "creation_time_exact_tags": True,
    }
    if any(proof.get(key) != value for key, value in required.items()):
        raise TagWarningRefusal("functional ALB prerequisite proof is incomplete")
    receipt_hash = proof.get("receipt_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", str(receipt_hash)):
        raise TagWarningRefusal("functional ALB proof receipt hash is malformed")


def classify(observations: list[dict[str, Any]], proof: dict[str, Any]) -> dict[str, Any]:
    validate_proof(proof)
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
