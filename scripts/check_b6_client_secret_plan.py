#!/usr/bin/env python3
"""Fail closed unless the B6 client-secret Terraform plan has one exact delta."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


EXPECTED = {
    "aws_iam_role_policy.b6_client_keys_kms": ["create"],
    "aws_secretsmanager_secret.b6_client_keys": ["create"],
    "aws_secretsmanager_secret_policy.b6_client_keys": ["create"],
}


def validate(plan_path: Path) -> dict[str, list[str]]:
    raw = subprocess.run(
        ["terraform", "-chdir=infra", "show", "-json", str(plan_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    plan = json.loads(raw)
    changes = {
        item["address"]: item["change"]["actions"]
        for item in plan.get("resource_changes", [])
        if item["change"]["actions"] not in (["no-op"], ["read"])
    }
    if changes != EXPECTED:
        raise RuntimeError(f"secret plan delta mismatch: {changes!r}")
    for item in plan.get("resource_changes", []):
        address = item["address"]
        if address == "aws_secretsmanager_secret.b6_client_keys":
            after = item["change"].get("after") or {}
            if after.get("name") != "medzen/client-api-keys":
                raise RuntimeError("secret name changed")
            if after.get("recovery_window_in_days") != 7:
                raise RuntimeError("secret recovery window changed")
            if "secret_string" in json.dumps(after).lower():
                raise RuntimeError("secret value entered Terraform plan")
        if address == "aws_iam_role_policy.b6_client_keys_kms":
            policy = json.loads((item["change"].get("after") or {}).get("policy", "{}"))
            statements = {statement.get("Sid"): statement for statement in policy.get("Statement", [])}
            decrypt = statements.get("DecryptOnlyB6ClientKeyViaSecretsManager", {})
            if decrypt.get("Action") != "kms:Decrypt":
                raise RuntimeError("supplemental KMS policy decrypt action changed")
            conditions = decrypt.get("Condition", {})
            if conditions.get("StringEquals", {}).get("kms:ViaService") != "secretsmanager.eu-central-1.amazonaws.com":
                raise RuntimeError("supplemental KMS ViaService changed")
            if conditions.get("StringLike", {}).get("kms:EncryptionContext:SecretARN") != "arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys*":
                raise RuntimeError("supplemental KMS encryption context changed")
    return changes


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_b6_client_secret_plan.py PLAN", file=sys.stderr)
        return 2
    try:
        changes = validate(Path(sys.argv[1]))
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6 CLIENT-SECRET PLAN: {exc}", file=sys.stderr)
        return 2
    print(f"PASS_B6_CLIENT_SECRET_PLAN changes={len(changes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
