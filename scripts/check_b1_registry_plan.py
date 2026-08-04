#!/usr/bin/env python3
"""Fail closed unless a saved Terraform plan is exactly B1 packet 002."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ACTIONS = {
    "aws_iam_role.registry_publisher": ("create",),
    "aws_iam_role_policy.registry_publisher": ("create",),
    'aws_iam_role_policy.pod["speech-orchestrator"]': ("update",),
    'aws_iam_role_policy.pod["llm-gateway"]': ("update",),
    'aws_iam_role_policy.pod["tts-gateway"]': ("update",),
}
EXPECTED_OUTPUTS = {
    "registry_parameter_prefix",
    "registry_publisher_role_arn",
}


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, tuple[str, ...]] = {}
    for resource in plan.get("resource_changes", []):
        actions = tuple(resource.get("change", {}).get("actions", []))
        if actions != ("no-op",):
            changed[resource.get("address", "<missing-address>")] = actions

    problems: list[str] = []
    if changed != EXPECTED_ACTIONS:
        missing = sorted(set(EXPECTED_ACTIONS) - set(changed))
        unexpected = sorted(set(changed) - set(EXPECTED_ACTIONS))
        wrong = sorted(
            address
            for address in set(changed) & set(EXPECTED_ACTIONS)
            if changed[address] != EXPECTED_ACTIONS[address]
        )
        if missing:
            problems.append(f"missing expected changes: {missing}")
        if unexpected:
            problems.append(f"unexpected changes: {unexpected}")
        if wrong:
            problems.append(
                "wrong actions: "
                + repr({address: changed[address] for address in wrong})
            )

    outputs = {
        name: tuple(change.get("actions", []))
        for name, change in plan.get("output_changes", {}).items()
        if tuple(change.get("actions", [])) != ("no-op",)
    }
    if set(outputs) != EXPECTED_OUTPUTS or any(
        actions != ("create",) for actions in outputs.values()
    ):
        problems.append(
            f"output changes differ: got {outputs}, expected creates for "
            f"{sorted(EXPECTED_OUTPUTS)}"
        )

    destructive = {
        address: actions
        for address, actions in changed.items()
        if "delete" in actions or actions == ("create", "delete")
    }
    if destructive:
        problems.append(f"destructive or replacement actions: {destructive}")

    if problems:
        raise ValueError("; ".join(problems))

    return {
        "status": "PASS_EXACT_B1_PACKET_2026_002",
        "add": 2,
        "change": 3,
        "destroy": 0,
        "changed_resources": [
            {"address": address, "actions": list(EXPECTED_ACTIONS[address])}
            for address in sorted(EXPECTED_ACTIONS)
        ],
        "changed_outputs": [
            {"name": name, "actions": ["create"]}
            for name in sorted(EXPECTED_OUTPUTS)
        ],
    }


def load_saved_plan(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'infra'}", "show", "-json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "terraform show failed")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan_bytes = args.plan.read_bytes()
        summary = validate_plan(load_saved_plan(args.plan))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSING B1 APPLY: {exc}", file=sys.stderr)
        return 2
    summary["saved_plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    summary["saved_plan_bytes"] = len(plan_bytes)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
