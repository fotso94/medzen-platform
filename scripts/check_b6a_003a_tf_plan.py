#!/usr/bin/env python3
"""Fail closed unless a saved plan is one exact B6A packet 003A phase."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "558069890522"
REGION = "eu-central-1"
KMS_KEY_ARN = (
    "arn:aws:kms:eu-central-1:558069890522:key/"
    "9c336116-c648-4548-95c6-1b926478ae57"
)
PHASE_ACTIONS = {
    "ecr": {"aws_ecr_repository.b6a_nvidia_dra": ("create",)},
    "identity": {
        "aws_iam_role.b6a_asr": ("create",),
        "aws_iam_role_policy.b6a_asr": ("create",),
        "aws_eks_pod_identity_association.b6a_asr": ("create",),
    },
}


def _json_document(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a JSON string")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} is malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return parsed


def _expected_document(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text())


def _changed(plan: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {
        resource.get("address", "<missing-address>"): tuple(
            resource.get("change", {}).get("actions", [])
        )
        for resource in plan.get("resource_changes", [])
        if tuple(resource.get("change", {}).get("actions", [])) != ("no-op",)
    }


def _resource(plan: dict[str, Any], address: str) -> dict[str, Any]:
    matches = [
        item for item in plan.get("resource_changes", [])
        if item.get("address") == address
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one {address} change record")
    after = matches[0].get("change", {}).get("after")
    if not isinstance(after, dict):
        raise ValueError(f"{address} exact after-state is required")
    return after


def _outputs(plan: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(change.get("actions", []))
        for name, change in plan.get("output_changes", {}).items()
        if tuple(change.get("actions", [])) != ("no-op",)
    }


def _validate_ecr(plan: dict[str, Any]) -> None:
    after = _resource(plan, "aws_ecr_repository.b6a_nvidia_dra")
    expected = {
        "name": "medzen-nvidia-dra",
        "image_tag_mutability": "IMMUTABLE",
        "force_delete": False,
        "image_scanning_configuration": [{"scan_on_push": True}],
        "encryption_configuration": [
            {"encryption_type": "KMS", "kms_key": KMS_KEY_ARN}
        ],
    }
    for field, value in expected.items():
        if after.get(field) != value:
            raise ValueError(
                f"DRA repository {field} differs: {after.get(field)!r} != {value!r}"
            )


def _validate_identity(plan: dict[str, Any]) -> None:
    role = _resource(plan, "aws_iam_role.b6a_asr")
    if role.get("name") != "medzen-b6a-asr-role":
        raise ValueError("unexpected B6A role name")
    if role.get("max_session_duration") != 3600:
        raise ValueError("B6A role session duration must be 3600 seconds")
    if _json_document(role.get("assume_role_policy"), "assume_role_policy") != (
        _expected_document(
            "platform/iam/b6a/medzen-b6a-asr-role.trust.template.json"
        )
    ):
        raise ValueError("B6A trust policy differs from reviewed template")

    policy = _resource(plan, "aws_iam_role_policy.b6a_asr")
    if policy.get("name") != "medzen-b6a-asr-access":
        raise ValueError("unexpected B6A inline policy name")
    if _json_document(policy.get("policy"), "inline policy") != _expected_document(
        "platform/iam/b6a/medzen-b6a-asr-role.policy.template.json"
    ):
        raise ValueError("B6A inline policy differs from reviewed template")

    association = _resource(
        plan, "aws_eks_pod_identity_association.b6a_asr"
    )
    expected_association = {
        "cluster_name": "medzen-speech",
        "namespace": "medzen",
        "service_account": "asr-runtime-b6a",
    }
    for field, value in expected_association.items():
        if association.get(field) != value:
            raise ValueError(
                f"Pod Identity {field} differs: {association.get(field)!r} != {value!r}"
            )


def validate_plan(plan: dict[str, Any], phase: str) -> dict[str, Any]:
    if phase not in PHASE_ACTIONS:
        raise ValueError(f"unknown phase: {phase}")
    changed = _changed(plan)
    if changed != PHASE_ACTIONS[phase]:
        raise ValueError(
            f"resource changes differ: got {changed}, expected {PHASE_ACTIONS[phase]}"
        )
    outputs = _outputs(plan)
    if outputs:
        raise ValueError(f"unexpected output changes: {outputs}")
    if phase == "ecr":
        _validate_ecr(plan)
    else:
        _validate_identity(plan)
    return {
        "status": f"PASS_EXACT_B6A_PACKET_2026_003A_{phase.upper()}_PHASE",
        "phase": phase,
        "add": len(PHASE_ACTIONS[phase]),
        "change": 0,
        "destroy": 0,
        "changed_resources": [
            {"address": address, "actions": list(actions)}
            for address, actions in sorted(PHASE_ACTIONS[phase].items())
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
    parser.add_argument("--phase", required=True, choices=sorted(PHASE_ACTIONS))
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan_bytes = args.plan.read_bytes()
        summary = validate_plan(load_saved_plan(args.plan), args.phase)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSING B6A PACKET 003A APPLY: {exc}", file=sys.stderr)
        return 2
    summary["saved_plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    summary["saved_plan_bytes"] = len(plan_bytes)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
