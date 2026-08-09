#!/usr/bin/env python3
"""Publish one synthetic B6 client-key hash after exact owner authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "platform/manifests/B6-CLIENT-API-KEYS-2026-001.json"
EXPECTED_ACCOUNT = "558069890522"
EXPECTED_REGION = "eu-central-1"
EXPECTED_OPERATOR = f"arn:aws:iam::{EXPECTED_ACCOUNT}:user/s.fotso"
EXPECTED_ORCHESTRATOR = f"arn:aws:iam::{EXPECTED_ACCOUNT}:role/medzen-orch-role"
TOKEN_PATH = Path("/private/tmp/medzen-b6-6-client-token")


class SecretRefusal(RuntimeError):
    """An immutable input, identity, policy or AWS result disagreed."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def load_authorization(path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    authorization = json.loads(path.read_bytes())
    if authorization.get("status") != "OWNER_APPROVED_FOR_EXECUTION":
        raise SecretRefusal("owner authorization is absent")
    manifest = json.loads(MANIFEST.read_bytes())
    manifest_binding = authorization.get("request_manifest", {})
    if (
        manifest_binding.get("path") != str(MANIFEST.relative_to(ROOT))
        or manifest_binding.get("sha256") != sha256(MANIFEST.read_bytes())
    ):
        raise SecretRefusal("authorization manifest binding mismatch")
    packet_binding = authorization.get("packet", {})
    packet = ROOT / str(packet_binding.get("path", ""))
    if not packet.is_file() or packet_binding.get("sha256") != sha256(packet.read_bytes()):
        raise SecretRefusal("authorization packet binding mismatch")
    return authorization, manifest, packet


def expected_resource_policy(secret_arn: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowOnlyOrchestratorRead",
                "Effect": "Allow",
                "Principal": {"AWS": EXPECTED_ORCHESTRATOR},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": secret_arn,
            },
            {
                "Sid": "DenyEveryOtherPrincipalRead",
                "Effect": "Deny",
                "Principal": {"AWS": "*"},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": secret_arn,
                "Condition": {
                    "ArnNotEquals": {"aws:PrincipalArn": EXPECTED_ORCHESTRATOR}
                },
            },
        ],
    }


def write_token(token: str) -> None:
    descriptor = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (token + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if stat.S_IMODE(TOKEN_PATH.stat().st_mode) != 0o600:
        raise SecretRefusal("local token mode is not 0600")


def execute(authorization_path: Path, receipt_path: Path) -> dict[str, Any]:
    import boto3

    authorization, manifest, packet = load_authorization(authorization_path)
    session = boto3.Session(profile_name="medzen", region_name=EXPECTED_REGION)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != EXPECTED_ACCOUNT or identity.get("Arn") != EXPECTED_OPERATOR:
        raise SecretRefusal("operator account or caller identity mismatch")

    client = session.client("secretsmanager")
    described = client.describe_secret(SecretId=manifest["aws"]["secret_name"])
    if described.get("KmsKeyId") != manifest["aws"]["kms_key_arn"]:
        raise SecretRefusal("secret KMS binding mismatch")
    if described.get("VersionIdsToStages"):
        raise SecretRefusal("secret already has a value; rotation requires a new packet")

    secret_arn = described["ARN"]
    actual_policy = json.loads(
        client.get_resource_policy(SecretId=secret_arn)["ResourcePolicy"]
    )
    expected_policy = expected_resource_policy(secret_arn)
    if canonical(actual_policy) != canonical(expected_policy):
        raise SecretRefusal("secret resource policy differs from the reviewed boundary")
    validation = client.validate_resource_policy(
        SecretId=secret_arn,
        ResourcePolicy=json.dumps(expected_policy, separators=(",", ":")),
    )
    if validation.get("PolicyValidationPassed") is not True:
        raise SecretRefusal("Secrets Manager resource policy validation failed")

    token = secrets.token_urlsafe(manifest["key_material_contract"]["random_bytes"])
    token_hash = sha256(token.encode("ascii"))
    value = {
        "schema_version": 1,
        "classification": manifest["classification"],
        "clients": [{
            "client_id": manifest["key_material_contract"]["client_id"],
            "enabled": True,
            "key_sha256": token_hash,
        }],
    }
    write_token(token)
    try:
        published = client.put_secret_value(
            SecretId=secret_arn,
            SecretString=canonical(value).decode("utf-8").rstrip("\n"),
            VersionStages=["AWSCURRENT"],
        )
    except Exception:
        TOKEN_PATH.unlink(missing_ok=True)
        raise

    try:
        client.get_secret_value(SecretId=secret_arn)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "AccessDeniedException":
            raise
        operator_read = "EXPLICITLY_DENIED_AS_REQUIRED"
    else:
        raise SecretRefusal("operator unexpectedly read the orchestrator-only secret")

    receipt = {
        "record": "B6_SYNTHETIC_CLIENT_API_KEYS_EXECUTION",
        "id": "B6-CLIENT-API-KEYS-2026-001",
        "revision": 1,
        "status": "VERIFIED_COMPLETE",
        "completed_utc": now(),
        "authorization": {
            "path": str(authorization_path.relative_to(ROOT)),
            "sha256": sha256(authorization_path.read_bytes()),
            "id": authorization["id"],
        },
        "packet": {
            "path": str(packet.relative_to(ROOT)),
            "sha256": sha256(packet.read_bytes()),
        },
        "request_manifest": {
            "path": str(MANIFEST.relative_to(ROOT)),
            "sha256": sha256(MANIFEST.read_bytes()),
        },
        "aws": {
            "account": identity["Account"],
            "region": EXPECTED_REGION,
            "operator": identity["Arn"],
            "secret_arn": secret_arn,
            "kms_key_arn": described["KmsKeyId"],
        },
        "publication": {
            "version_id": published["VersionId"],
            "stages": ["AWSCURRENT"],
            "secret_value_sha256": sha256(canonical(value).rstrip(b"\n")),
            "bearer_token_sha256": token_hash,
            "plaintext_recorded": False,
            "local_token_path": str(TOKEN_PATH),
            "local_token_mode": "0600",
        },
        "access_boundary": {
            "resource_policy_validation": "PASS",
            "operator_get_secret_value": operator_read,
            "only_allowed_reader": EXPECTED_ORCHESTRATOR,
            "orchestrator_live_read": "DEFERRED_TO_B6_6_POD_IDENTITY_PROOF",
        },
        "explicit_non_events": {
            "real_client_keys": 0,
            "plaintext_in_receipt": 0,
            "new_kms_keys": 0,
            "new_iam_roles": 0,
            "nodes_scaled": 0,
            "deployments": 0,
        },
    }
    receipt_path.write_bytes(canonical(receipt))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("REFUSING: --apply is required for approved AWS execution", file=sys.stderr)
        return 2
    try:
        receipt = execute(args.authorization, args.receipt)
    except (OSError, KeyError, ValueError, ClientError, SecretRefusal) as exc:
        print(f"REFUSING OR STOPPED B6 CLIENT SECRET: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": receipt["status"],
        "secret_arn": receipt["aws"]["secret_arn"],
        "version_id": receipt["publication"]["version_id"],
        "plaintext_recorded": False,
        "receipt": str(args.receipt),
        "receipt_sha256": sha256(args.receipt.read_bytes()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
