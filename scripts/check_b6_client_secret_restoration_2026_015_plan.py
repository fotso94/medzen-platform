#!/usr/bin/env python3
"""Extend the proven 2026-012A secret guards with normalize-if-needed logic."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import check_b6_client_secret_restoration_plan as proven


def _exact_secret_after(plan: dict[str, Any]) -> None:
    change = proven.item(plan, proven.SECRET)["change"]
    after = change.get("after") or {}
    if (
        change.get("actions") != ["no-op"]
        or after.get("arn") != proven.SECRET_ARN
        or after.get("id") != proven.SECRET_ARN
        or after.get("name") != "medzen/client-api-keys"
        or after.get("kms_key_id") != proven.KMS_KEY
        or after.get("recovery_window_in_days") != 7
        or after.get("force_overwrite_replica_secret") is not False
        or after.get("tags") != proven.expected_explicit_tags()
        or after.get("tags_all") != proven.expected_tags_all()
        or change.get("after_unknown") not in (None, {})
    ):
        raise ValueError("exact restored-secret no-op differs")


def _exact_policy_after(plan: dict[str, Any]) -> None:
    policy_change = proven.item(plan, proven.POLICY)["change"]
    policy = policy_change.get("after") or {}
    if (
        policy_change.get("actions") != ["no-op"]
        or policy.get("secret_arn") != proven.SECRET_ARN
        or policy.get("block_public_policy") is not True
        or json.loads(policy.get("policy", "{}")) != proven.expected_resource_policy()
    ):
        raise ValueError("exact resource-policy no-op differs")
    kms_change = proven.item(plan, proven.KMS)["change"]
    kms = kms_change.get("after") or {}
    if (
        kms_change.get("actions") != ["no-op"]
        or kms.get("name") != "medzen-orch-b6-client-secret-kms"
        or kms.get("role") != "medzen-orch-role"
        or json.loads(kms.get("policy", "{}")) != proven.expected_kms_policy()
    ):
        raise ValueError("exact KMS-policy no-op differs")


def validate_normalize_if_needed(plan: dict[str, Any]) -> str:
    actual = proven.changes(plan)
    if actual == proven.EXPECTED_NORMALIZE_CHANGES:
        proven.validate(plan, "normalize")
        return "APPLY_EXACT_NORMALIZATION"
    if actual == {}:
        _exact_secret_after(plan)
        if "secret_string" in json.dumps(plan).lower():
            raise ValueError("secret plaintext entered the Terraform plan")
        return "NO_NORMALIZATION_REQUIRED"
    raise ValueError(f"normalize-if-needed delta differs: {actual!r}")


def validate_residual(plan: dict[str, Any], *, include_policies: bool) -> None:
    if proven.changes(plan) != {}:
        raise ValueError(f"residual plan is not no-op: {proven.changes(plan)!r}")
    _exact_secret_after(plan)
    if include_policies:
        _exact_policy_after(plan)
    if "secret_string" in json.dumps(plan).lower():
        raise ValueError("secret plaintext entered the Terraform plan")


def validate(plan: dict[str, Any], mode: str) -> str:
    if mode == "normalize-if-needed":
        return validate_normalize_if_needed(plan)
    if mode == "reconcile":
        proven.validate(plan, "reconcile")
        return "APPLY_EXACT_BOUNDARY_CREATES"
    if mode == "cleanup":
        proven.validate(plan, "cleanup")
        return "APPLY_EXACT_CLEANUP_SUBSET"
    if mode == "residual-secret":
        validate_residual(plan, include_policies=False)
        return "NO_CHANGES_SECRET"
    if mode == "residual-all":
        validate_residual(plan, include_policies=True)
        return "NO_CHANGES_ALL_BOUNDARIES"
    raise ValueError(f"unknown mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "normalize-if-needed",
            "reconcile",
            "cleanup",
            "residual-secret",
            "residual-all",
        ),
    )
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        result = validate(proven.load(args.plan), args.mode)
    except (
        OSError,
        KeyError,
        StopIteration,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            f"REFUSING B6 PACKET 2026-015 TERRAFORM PLAN: {exc}",
            file=sys.stderr,
        )
        return 2
    print(f"PASS_B6_2026_015_PLAN mode={args.mode} outcome={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
