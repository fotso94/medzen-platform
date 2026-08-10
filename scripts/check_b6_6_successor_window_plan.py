#!/usr/bin/env python3
"""Extend the proven window guard for the principal-independent endpoint path."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import check_b6_6_window_plan as proven


ROLE_ARN = "arn:aws:iam::558069890522:role/medzen-b6-window-probe-execution"


def _policy(statement: dict[str, Any]) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": [statement]})


def validate_create(plan: dict[str, Any]) -> None:
    actual = proven.changes(plan)
    if actual != {address: ["create"] for address in proven.ADDRESSES}:
        raise ValueError(f"create delta differs: {actual!r}")

    source_address = "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]"
    source = proven._after(plan, source_address)
    source_config = proven._config(plan, source_address)["expressions"]
    for field in ("security_group_id", "referenced_security_group_id"):
        references = source_config[field].get("references", [])
        if "aws_security_group.b6_probe_endpoints" not in references:
            raise ValueError(f"probe endpoint {field} must reference the temporary SG")
    if (
        source.get("from_port") != 443
        or source.get("to_port") != 443
        or source.get("ip_protocol") != "tcp"
    ):
        raise ValueError("probe endpoint source rule differs")

    expected = {
        "ecr_api": (
            "ProbeNetworkRegistryToken",
            {"ecr:GetAuthorizationToken"},
            {"*"},
        ),
        "ecr_dkr": (
            "ProbeNetworkQualifiedImagePull",
            {
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            },
            {"arn:aws:ecr:eu-central-1:558069890522:repository/medzen-rag-index"},
        ),
    }
    for purpose, (sid, actions, resources) in expected.items():
        endpoint = proven._after(plan, f"aws_vpc_endpoint.b6_probe_{purpose}[0]")
        statement = proven._policy_statement(endpoint.get("policy", ""))
        if (
            statement.get("Sid") != sid
            or statement.get("Effect") != "Allow"
            or proven._string_set(statement.get("Action"), "actions") != actions
            or proven._string_set(statement.get("Resource"), "resources") != resources
            or proven._principal_set(statement) != {"*"}
            or "Condition" in statement
        ):
            raise ValueError(f"{purpose} successor endpoint policy differs")

    # Reuse every unchanged assertion in the proven 12-create guard after
    # normalizing only the three intentionally changed representations.
    compatible = copy.deepcopy(plan)
    source = proven._after(compatible, source_address)
    source["referenced_security_group_id"] = proven.PROBE_SG
    replacements = {
        "ecr_api": (
            "ExactProbeRoleRegistryToken",
            "ecr:GetAuthorizationToken",
            "*",
        ),
        "ecr_dkr": (
            "ExactProbeRoleQualifiedImagePull",
            [
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            ],
            "arn:aws:ecr:eu-central-1:558069890522:repository/medzen-rag-index",
        ),
    }
    for purpose, (sid, actions, resource) in replacements.items():
        endpoint = proven._after(
            compatible, f"aws_vpc_endpoint.b6_probe_{purpose}[0]"
        )
        endpoint["policy"] = _policy(
            {
                "Sid": sid,
                "Effect": "Allow",
                "Principal": {"AWS": ROLE_ARN},
                "Action": actions,
                "Resource": resource,
            }
        )
    proven.validate_create(compatible)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "destroy", "cleanup"))
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = proven.load(args.plan)
        {
            "create": validate_create,
            "destroy": proven.validate_destroy,
            "cleanup": proven.validate_cleanup,
        }[args.mode](plan)
    except (
        OSError,
        KeyError,
        ValueError,
        StopIteration,
        subprocess.SubprocessError,
    ) as exc:
        print(f"REFUSING B6.6 SUCCESSOR {args.mode.upper()}: {exc}")
        return 2
    print(
        f"PASS_B6_6_SUCCESSOR_{args.mode.upper()} "
        f"changes={len(proven.changes(plan))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
