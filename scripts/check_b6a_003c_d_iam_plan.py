#!/usr/bin/env python3
"""Accept only the one independently reviewed 003C-D node SSM policy create."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ADDRESS = "aws_iam_role_policy.node_ssm_core"
EXPECTED_NAME = "medzen-speech-node-ssm-core"
EXPECTED_ROLE = "medzen-speech-node-role"
POLICY = ROOT / "platform/iam/medzen-node-ssm-core.json"


class PlanRefusal(RuntimeError):
    pass


def check(plan: dict) -> dict:
    changes = plan.get("resource_changes", [])
    mutable = [
        item
        for item in changes
        if item.get("change", {}).get("actions") != ["no-op"]
    ]
    if len(mutable) != 1:
        raise PlanRefusal("003C-D IAM plan must contain exactly one mutable resource")
    change = mutable[0]
    if change.get("address") != EXPECTED_ADDRESS:
        raise PlanRefusal("003C-D IAM plan changes an unexpected resource")
    if change.get("change", {}).get("actions") != ["create"]:
        raise PlanRefusal("003C-D IAM policy must be a create-only change")
    after = change["change"].get("after", {})
    if after.get("name") != EXPECTED_NAME or after.get("role") != EXPECTED_ROLE:
        raise PlanRefusal("003C-D IAM role or policy name differs")
    try:
        rendered_policy = json.loads(after["policy"])
    except Exception as exc:
        raise PlanRefusal("003C-D IAM policy in the plan is malformed") from exc
    if rendered_policy != json.loads(POLICY.read_bytes()):
        raise PlanRefusal("003C-D planned IAM policy differs from the frozen source")
    return {
        "status": "PASS_EXACTLY_ONE_IAM_CREATE",
        "resource": EXPECTED_ADDRESS,
        "name": EXPECTED_NAME,
        "role": EXPECTED_ROLE,
        "creates": 1,
        "updates": 0,
        "deletes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        completed = subprocess.run(
            ["terraform", "-chdir=infra", "show", "-json", str(args.plan)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise PlanRefusal("Terraform plan JSON could not be read")
        result = check(json.loads(completed.stdout))
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
