#!/usr/bin/env python3
"""Refuse unless the one-time bridge touches only the persistent secret boundary."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "aws_secretsmanager_secret.b6_client_keys[0]",
    "aws_secretsmanager_secret_policy.b6_client_keys[0]",
    "aws_iam_role_policy.b6_client_keys_kms[0]",
}
SECRET = "aws_secretsmanager_secret.b6_client_keys[0]"


def load(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'infra'}", "show", "-json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def validate(plan: dict[str, Any]) -> dict[str, list[str]]:
    changes = {
        item["address"]: item["change"]["actions"]
        for item in plan.get("resource_changes", [])
        if item["change"]["actions"] not in (["no-op"], ["read"])
    }
    if not set(changes).issubset(ALLOWED):
        raise ValueError(f"bridge plan includes an unrelated resource: {changes!r}")
    if any(
        actions not in (["create"], ["update"])
        for actions in changes.values()
    ):
        raise ValueError(f"bridge plan contains replacement or deletion: {changes!r}")
    if changes.get(SECRET) == ["create"]:
        raise ValueError("bridge must import and retain the existing secret")
    if changes.get("aws_secretsmanager_secret_policy.b6_client_keys[0]") == ["create"]:
        raise ValueError("bridge must import the already-installed operator-deny policy")
    if changes.get("aws_iam_role_policy.b6_client_keys_kms[0]") != ["create"]:
        raise ValueError("orchestrator KMS reader policy is not created")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        changes = validate(load(args.plan))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6.6 PERSISTENT-SECRET BRIDGE: {exc}", file=sys.stderr)
        return 2
    print(f"PASS_B6_6_PERSISTENT_SECRET_BRIDGE changes={len(changes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
