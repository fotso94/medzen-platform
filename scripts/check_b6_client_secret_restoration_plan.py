#!/usr/bin/env python3
"""Refuse unless a post-import plan recreates only the B6 secret boundaries."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SECRET = "aws_secretsmanager_secret.b6_client_keys[0]"
POLICY = "aws_secretsmanager_secret_policy.b6_client_keys[0]"
KMS = "aws_iam_role_policy.b6_client_keys_kms[0]"
EXPECTED_CHANGES = {POLICY: ["create"], KMS: ["create"]}
EXPECTED_NORMALIZE_CHANGES = {SECRET: ["update"]}
SECRET_ARN = "arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE"
KMS_KEY = "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57"
ORCHESTRATOR = "arn:aws:iam::558069890522:role/medzen-orch-role"


def expected_resource_policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowOnlyOrchestratorRead",
                "Effect": "Allow",
                "Principal": {"AWS": ORCHESTRATOR},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": SECRET_ARN,
            },
            {
                "Sid": "DenyEveryOtherPrincipalRead",
                "Effect": "Deny",
                "Principal": {"AWS": "*"},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": SECRET_ARN,
                "Condition": {"ArnNotEquals": {"aws:PrincipalArn": ORCHESTRATOR}},
            },
        ],
    }


def expected_kms_policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DescribeExistingB6ClientKeyKmsKey",
                "Effect": "Allow",
                "Action": "kms:DescribeKey",
                "Resource": KMS_KEY,
            },
            {
                "Sid": "DecryptOnlyB6ClientKeyViaSecretsManager",
                "Effect": "Allow",
                "Action": "kms:Decrypt",
                "Resource": KMS_KEY,
                "Condition": {
                    "StringEquals": {"kms:ViaService": "secretsmanager.eu-central-1.amazonaws.com"},
                    "StringLike": {
                        "kms:EncryptionContext:SecretARN": "arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys*"
                    },
                },
            },
        ],
    }


def expected_explicit_tags() -> dict[str, str]:
    return {
        "Project": "medzen-speech",
        "Environment": "dev",
        "CostCenter": "speech-platform",
        "Stage": "B6.6",
        "Workstream": "integration-window-auth",
        "BudgetRegistry": "COST-REGISTRY-2026-003",
        "Classification": "SYNTHETIC_TEST_ONLY",
    }


def expected_tags_all() -> dict[str, str]:
    return {
        **expected_explicit_tags(),
        "ManagedBy": "terraform",
        "Component": "speech-platform",
    }


def imported_explicit_tags() -> dict[str, str]:
    return {
        key: value
        for key, value in expected_explicit_tags().items()
        if key not in {"Project", "Environment"}
    }


def load(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(ROOT / "scripts/terraform_medzen.sh"), "show", "-json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def changes(plan: dict[str, Any]) -> dict[str, list[str]]:
    return {
        item["address"]: item["change"]["actions"]
        for item in plan.get("resource_changes", [])
        if item["change"]["actions"] not in (["no-op"], ["read"])
    }


def item(plan: dict[str, Any], address: str) -> dict[str, Any]:
    return next(value for value in plan["resource_changes"] if value["address"] == address)


def validate_cleanup(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    permitted = {SECRET, POLICY, KMS}
    if not actual or not set(actual).issubset(permitted):
        raise ValueError(f"cleanup delta differs: {actual!r}")
    if any(actions != ["delete"] for actions in actual.values()):
        raise ValueError(f"cleanup contains a non-delete action: {actual!r}")
    for address in actual:
        before = item(plan, address)["change"].get("before") or {}
        if address == SECRET and (
            before.get("arn") != SECRET_ARN
            or before.get("name") != "medzen/client-api-keys"
            or before.get("kms_key_id") != KMS_KEY
            or before.get("recovery_window_in_days") not in (None, 7)
            or before.get("tags_all") != expected_tags_all()
            or before.get("tags") not in (
                imported_explicit_tags(),
                expected_explicit_tags(),
            )
        ):
            raise ValueError("cleanup secret identity differs")
        if address == POLICY and before.get("secret_arn") != SECRET_ARN:
            raise ValueError("cleanup resource-policy identity differs")
        if address == KMS and (
            before.get("name") != "medzen-orch-b6-client-secret-kms"
            or before.get("role") != "medzen-orch-role"
        ):
            raise ValueError("cleanup KMS policy identity differs")


def validate(plan: dict[str, Any], mode: str = "reconcile") -> None:
    if mode == "cleanup":
        validate_cleanup(plan)
        return
    if mode not in {"reconcile", "normalize"}:
        raise ValueError(f"unknown plan mode: {mode}")
    expected_changes = EXPECTED_CHANGES if mode == "reconcile" else EXPECTED_NORMALIZE_CHANGES
    if changes(plan) != expected_changes:
        raise ValueError(f"restoration delta differs: {changes(plan)!r}")
    secret_change = item(plan, SECRET)["change"]
    expected_secret_actions = ["no-op"] if mode == "reconcile" else ["update"]
    if secret_change["actions"] != expected_secret_actions:
        raise ValueError("restored secret action differs")
    secret = secret_change.get("after") or {}
    if (
        secret.get("arn") != SECRET_ARN
        or secret.get("name") != "medzen/client-api-keys"
        or secret.get("kms_key_id") != KMS_KEY
        or secret.get("recovery_window_in_days") != 7
    ):
        raise ValueError("restored secret identity or encryption boundary differs")
    if any(secret.get("tags", {}).get(key) != value for key, value in expected_explicit_tags().items()):
        raise ValueError("restored secret allocation tags differ")
    if mode == "normalize":
        before = secret_change.get("before") or {}
        if (
            before.get("arn") != SECRET_ARN
            or before.get("id") != SECRET_ARN
            or before.get("name") != "medzen/client-api-keys"
            or before.get("kms_key_id") != KMS_KEY
            or before.get("tags") != imported_explicit_tags()
            or before.get("tags_all") != expected_tags_all()
            or before.get("force_overwrite_replica_secret") is not None
            or before.get("recovery_window_in_days") is not None
            or secret.get("tags") != expected_explicit_tags()
            or secret.get("tags_all") != expected_tags_all()
            or secret.get("force_overwrite_replica_secret") is not False
            or secret_change.get("after_unknown") != {}
        ):
            raise ValueError("imported-secret normalization boundary differs")
        changed_fields = {
            key
            for key in set(before) | set(secret)
            if before.get(key) != secret.get(key)
        }
        if changed_fields != {
            "force_overwrite_replica_secret",
            "recovery_window_in_days",
            "tags",
        }:
            raise ValueError(f"unexpected imported-secret normalized fields: {sorted(changed_fields)!r}")
        return

    policy = item(plan, POLICY)["change"].get("after") or {}
    if policy.get("secret_arn") != SECRET_ARN or policy.get("block_public_policy") is not True:
        raise ValueError("secret resource-policy boundary differs")
    policy_doc = json.loads(policy.get("policy", "{}"))
    if policy_doc != expected_resource_policy():
        raise ValueError("secret resource-policy statements differ")

    kms = item(plan, KMS)["change"].get("after") or {}
    if kms.get("name") != "medzen-orch-b6-client-secret-kms" or kms.get("role") != "medzen-orch-role":
        raise ValueError("orchestrator KMS inline-policy identity differs")
    kms_doc = json.loads(kms.get("policy", "{}"))
    if kms_doc != expected_kms_policy():
        raise ValueError("orchestrator KMS inline-policy statements differ")
    if "secret_string" in json.dumps(plan).lower():
        raise ValueError("secret plaintext entered the Terraform plan")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("reconcile", "normalize", "cleanup"), default="reconcile")
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = load(args.plan)
        validate(plan, args.mode)
    except (OSError, KeyError, StopIteration, ValueError, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6 CLIENT-SECRET RESTORATION PLAN: {exc}", file=sys.stderr)
        return 2
    if args.mode == "cleanup":
        print(f"PASS_B6_CLIENT_SECRET_RESTORATION_CLEANUP destroys={len(changes(plan))}")
    elif args.mode == "normalize":
        print("PASS_B6_CLIENT_SECRET_NORMALIZATION_PLAN changes=1 add=0 update=1 destroy=0")
    else:
        print("PASS_B6_CLIENT_SECRET_RESTORATION_PLAN changes=2 add=2 update=0 destroy=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
