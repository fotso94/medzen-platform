#!/usr/bin/env python3
"""Registry-alias rollback (B7 Phase 4 / Base v5 B8). FAILS CLOSED.

Rollback is an alias switch, never an image rebuild: the SSM parameter's
own version history is the rollback evidence (infra/ssm.tf), so restoring
service means re-publishing the PREVIOUS version's value as a new version
— auditable, reversible, seconds to execute.

Guards:
  * the production pointer /medzen/registry/serving/current is refused
    outright — production rollback needs its own packet;
  * the target parameter must live under /medzen/registry/;
  * there must BE a previous version (a parameter with a single version
    has nothing to roll back to);
  * dry-run is the default — mutation requires --execute.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

PRODUCTION_POINTER = "/medzen/registry/serving/current"
REGISTRY_PREFIX = "/medzen/registry/"


class RollbackRefusal(RuntimeError):
    pass


def plan_rollback(ssm_client: Any, parameter_name: str) -> dict[str, Any]:
    """Resolve current + previous versions and return the restore plan."""
    if parameter_name == PRODUCTION_POINTER:
        raise RollbackRefusal(
            "the production serving pointer requires its own reviewed packet"
        )
    if not parameter_name.startswith(REGISTRY_PREFIX):
        raise RollbackRefusal(f"{parameter_name} is outside {REGISTRY_PREFIX}")
    history = ssm_client.get_parameter_history(
        Name=parameter_name, WithDecryption=True
    ).get("Parameters", [])
    if len(history) < 2:
        raise RollbackRefusal(
            f"{parameter_name} has {len(history)} version(s) — nothing to roll back to"
        )
    ordered = sorted(history, key=lambda p: p["Version"])
    current, previous = ordered[-1], ordered[-2]
    if current["Value"] == previous["Value"]:
        raise RollbackRefusal(
            "current and previous versions are identical — rollback would be a no-op"
        )
    return {
        "parameter": parameter_name,
        "current_version": current["Version"],
        "previous_version": previous["Version"],
        "restore_value": previous["Value"],
        "current_value": current["Value"],
    }


def execute_rollback(ssm_client: Any, plan: dict[str, Any]) -> dict[str, Any]:
    response = ssm_client.put_parameter(
        Name=plan["parameter"],
        Value=plan["restore_value"],
        Type="SecureString",
        Overwrite=True,
    )
    readback = ssm_client.get_parameter(Name=plan["parameter"], WithDecryption=True)
    observed = readback["Parameter"]["Value"]
    if observed != plan["restore_value"]:
        raise RollbackRefusal("post-rollback readback differs from the restored value")
    return {
        "status": "PASS_ALIAS_ROLLBACK",
        "parameter": plan["parameter"],
        "restored_from_version": plan["previous_version"],
        "new_version": response.get("Version"),
        "readback_verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameter", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="mutate; default is a dry-run plan")
    args = parser.parse_args()
    import boto3

    ssm = boto3.client("ssm", region_name="eu-central-1")
    try:
        plan = plan_rollback(ssm, args.parameter)
        if not args.execute:
            print(json.dumps({"status": "DRY_RUN_PLAN", **plan}))
            return 0
        result = execute_rollback(ssm, plan)
    except RollbackRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
