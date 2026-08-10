#!/usr/bin/env python3
"""One-time bridge from packet-018 deletion state to the R1 persistent secret."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import ReceiptStore
from scripts.b6_6_bindings import validate as validate_bindings
from scripts.b6_6_credential import ACCOUNT, KMS_KEY, PROFILE, REGION, SECRET_ARN, SECRET_NAME


class BridgeRefusal(RuntimeError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _operator_denied(client: Any) -> bool:
    try:
        client.get_secret_value(SecretId=SECRET_ARN)
    except ClientError as exc:
        return exc.response.get("Error", {}).get("Code") == "AccessDeniedException"
    return False


def _permanent_resource_policy() -> str:
    orchestrator = f"arn:aws:iam::{ACCOUNT}:role/medzen-orch-role"
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowOnlyOrchestratorRead",
                    "Effect": "Allow",
                    "Principal": {"AWS": orchestrator},
                    "Action": "secretsmanager:GetSecretValue",
                    "Resource": SECRET_ARN,
                },
                {
                    "Sid": "DenyEveryOtherPrincipalRead",
                    "Effect": "Deny",
                    "Principal": {"AWS": "*"},
                    "Action": "secretsmanager:GetSecretValue",
                    "Resource": SECRET_ARN,
                    "Condition": {"ArnNotEquals": {"aws:PrincipalArn": orchestrator}},
                },
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def execute(
    authorization: Path,
    packet_sha256: str,
    receipts_dir: Path,
    *,
    mode: str,
) -> dict[str, Any]:
    validate_bindings(authorization, packet_sha256, ROOT)
    store = ReceiptStore(receipts_dir)
    status = "REFUSED"
    payload: dict[str, Any] = {"reason_code": "PERSISTENT_SECRET_BRIDGE_REFUSED"}
    try:
        session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        if session.client("sts").get_caller_identity().get("Account") != ACCOUNT:
            raise BridgeRefusal("AWS account differs")
        client = session.client("secretsmanager")
        before = client.describe_secret(SecretId=SECRET_ARN)
        identity_differs = (
            before.get("ARN") != SECRET_ARN
            or before.get("Name") != SECRET_NAME
            or before.get("KmsKeyId") != KMS_KEY
        )
        if identity_differs:
            raise BridgeRefusal("exact persistent secret identity differs")
        if mode == "initial":
            if before.get("DeletedDate") is None:
                raise BridgeRefusal("exact recoverable packet-018 secret state differs")
            client.restore_secret(SecretId=SECRET_ARN)
        elif mode == "continuation":
            if before.get("DeletedDate") is not None or not _operator_denied(client):
                raise BridgeRefusal("contained packet-019 continuation state differs")
        else:
            raise BridgeRefusal("unknown bridge mode")

        # Close the only possible plaintext-read interval before Terraform state
        # work. A failed import/plan therefore still leaves the secret denied.
        client.put_resource_policy(
            SecretId=SECRET_ARN,
            ResourcePolicy=_permanent_resource_policy(),
            BlockPublicPolicy=True,
        )
        if not _operator_denied(client):
            raise BridgeRefusal("operator deny was not installed before Terraform")

        state = _run(["terraform", "-chdir=infra", "state", "list"]).stdout.splitlines()
        address = "aws_secretsmanager_secret.b6_client_keys[0]"
        if address not in state:
            _run(["scripts/terraform_medzen.sh", "import", address, SECRET_ARN])
        policy_address = "aws_secretsmanager_secret_policy.b6_client_keys[0]"
        if policy_address not in state:
            _run(["scripts/terraform_medzen.sh", "import", policy_address, SECRET_ARN])
        plan = Path("/private/tmp/b6-019-persistent-secret-bridge.tfplan")
        _run([
            "scripts/terraform_medzen.sh",
            "plan",
            "-input=false",
            f"-out={plan}",
            "-var=account_id=558069890522",
            "-var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso",
            "-target=aws_secretsmanager_secret.b6_client_keys",
            "-target=aws_secretsmanager_secret_policy.b6_client_keys",
            "-target=aws_iam_role_policy.b6_client_keys_kms",
            "-var=enable_b6_client_keys=true",
        ])
        _run([sys.executable, "scripts/check_b6_6_persistent_secret_plan.py", str(plan)])
        _run(["scripts/terraform_medzen.sh", "apply", "-input=false", "-auto-approve", str(plan)])
        after = client.describe_secret(SecretId=SECRET_ARN)
        if (
            after.get("ARN") != SECRET_ARN
            or after.get("Name") != SECRET_NAME
            or after.get("KmsKeyId") != KMS_KEY
            or after.get("DeletedDate") is not None
            or not _operator_denied(client)
        ):
            raise BridgeRefusal("persistent secret post-bridge invariants differ")
        status = "PASS"
        payload = {
            "transition": (
                "RECOVERABLE_DELETION_TO_PERSISTENT_OPERATOR_DENIED"
                if mode == "initial"
                else "CONTAINED_RESTORED_STATE_TO_TERRAFORM_MANAGED_PERSISTENT"
            ),
            "secret_arn": SECRET_ARN,
            "existing_secret_reused": True,
            "historical_version_count_evaluated": False,
            "historical_tags_evaluated": False,
            "permanent_resource_policy": True,
            "operator_deny_installed_before_terraform": True,
            "operator_get_secret_value": "EXPLICITLY_DENIED_AS_REQUIRED",
            "compute_started": False,
            "window_resources_created": 0,
        }
    except Exception as exc:
        payload = {
            "reason_code": "PERSISTENT_SECRET_BRIDGE_REFUSED",
            "exception_class": type(exc).__name__,
            "compute_started": False,
        }
    finally:
        result = store.persist("persistent_secret_bridge", status, payload)
    if status != "PASS":
        raise BridgeRefusal(result["receipt_sha256"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("initial", "continuation"), required=True)
    args = parser.parse_args()
    try:
        result = execute(
            args.authorization.resolve(),
            args.packet_sha256,
            args.receipts_dir.resolve(),
            mode=args.mode,
        )
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
