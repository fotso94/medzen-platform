#!/usr/bin/env python3
"""Restore and rotate the B6 synthetic secret with receipt-per-stage safety."""

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
from typing import Any, Callable

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-002.json"
EXPECTED_ACCOUNT = "558069890522"
EXPECTED_REGION = "eu-central-1"
EXPECTED_OPERATOR = f"arn:aws:iam::{EXPECTED_ACCOUNT}:user/s.fotso"
EXPECTED_ORCHESTRATOR = f"arn:aws:iam::{EXPECTED_ACCOUNT}:role/medzen-orch-role"
SECRET_NAME = "medzen/client-api-keys"
SECRET_ARN = f"arn:aws:secretsmanager:{EXPECTED_REGION}:{EXPECTED_ACCOUNT}:secret:medzen/client-api-keys-NxZGxE"
KMS_KEY = f"arn:aws:kms:{EXPECTED_REGION}:{EXPECTED_ACCOUNT}:key/9c336116-c648-4548-95c6-1b926478ae57"
OLD_VERSION = "f78c8aa8-2765-4788-9928-dd1ba7c406bf"
TOKEN_PATH = Path("/private/tmp/medzen-b6-6-client-token")


class RestorationRefusal(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def persist(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, canonical(value))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_receipt(path: Path, expected_status: str) -> dict[str, Any]:
    if not path.is_file():
        raise RestorationRefusal(f"durable {path.stem} receipt is absent")
    value = json.loads(path.read_bytes())
    if value.get("status") != expected_status:
        raise RestorationRefusal(f"durable {path.stem} receipt is malformed")
    return value


def load_authorization(path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    authorization = json.loads(path.read_bytes())
    if authorization.get("id") != "B6-AWS-AUTH-2026-012A" or authorization.get("status") != "owner-approved":
        raise RestorationRefusal("exact owner authorization is absent")
    manifest = json.loads(MANIFEST.read_bytes())
    binding = authorization.get("request_manifest", {})
    if binding.get("path") != str(MANIFEST.relative_to(ROOT)) or binding.get("sha256") != sha(MANIFEST.read_bytes()):
        raise RestorationRefusal("request-manifest binding mismatch")
    packet_binding = authorization.get("packet", {})
    packet = ROOT / str(packet_binding.get("path", ""))
    if not packet.is_file() or packet_binding.get("sha256") != sha(packet.read_bytes()):
        raise RestorationRefusal("packet binding mismatch")
    return authorization, manifest, packet


def expected_tags() -> dict[str, str]:
    return {
        "Project": "medzen-speech",
        "ManagedBy": "terraform",
        "CostCenter": "speech-platform",
        "Workstream": "integration-window-auth",
        "Classification": "SYNTHETIC_TEST_ONLY",
        "BudgetRegistry": "COST-REGISTRY-2026-003",
        "Stage": "B6.6",
        "Environment": "dev",
        "Component": "speech-platform",
    }


def validate_description(description: dict[str, Any], *, pending: bool) -> None:
    if description.get("Name") != SECRET_NAME or description.get("ARN") != SECRET_ARN:
        raise RestorationRefusal("secret identity differs")
    if description.get("KmsKeyId") != KMS_KEY:
        raise RestorationRefusal("secret KMS key differs")
    tags = {item["Key"]: item["Value"] for item in description.get("Tags", [])}
    if tags != expected_tags():
        raise RestorationRefusal("secret tags differ")
    deleted = description.get("DeletedDate")
    if pending != (deleted is not None):
        raise RestorationRefusal("secret recovery state differs")
    versions = description.get("VersionIdsToStages", {})
    if versions.get(OLD_VERSION) != ["AWSCURRENT"] or set(versions) != {OLD_VERSION}:
        raise RestorationRefusal("historical secret version differs")


def expected_resource_policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowOnlyOrchestratorRead",
                "Effect": "Allow",
                "Principal": {"AWS": EXPECTED_ORCHESTRATOR},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": SECRET_ARN,
            },
            {
                "Sid": "DenyEveryOtherPrincipalRead",
                "Effect": "Deny",
                "Principal": {"AWS": "*"},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": SECRET_ARN,
                "Condition": {"ArnNotEquals": {"aws:PrincipalArn": EXPECTED_ORCHESTRATOR}},
            },
        ],
    }


def validate_kms_policy(policy: dict[str, Any]) -> None:
    statements = {item.get("Sid"): item for item in policy.get("Statement", [])}
    if set(statements) != {"DescribeExistingB6ClientKeyKmsKey", "DecryptOnlyB6ClientKeyViaSecretsManager"}:
        raise RestorationRefusal("orchestrator KMS policy statements differ")
    decrypt = statements["DecryptOnlyB6ClientKeyViaSecretsManager"]
    if decrypt.get("Action") != "kms:Decrypt" or decrypt.get("Resource") != KMS_KEY:
        raise RestorationRefusal("orchestrator KMS decrypt boundary differs")
    condition = decrypt.get("Condition", {})
    if condition.get("StringEquals", {}).get("kms:ViaService") != f"secretsmanager.{EXPECTED_REGION}.amazonaws.com":
        raise RestorationRefusal("orchestrator KMS service boundary differs")
    if condition.get("StringLike", {}).get("kms:EncryptionContext:SecretARN") != f"arn:aws:secretsmanager:{EXPECTED_REGION}:{EXPECTED_ACCOUNT}:secret:medzen/client-api-keys*":
        raise RestorationRefusal("orchestrator KMS encryption context differs")


def restore(secret_client: Any, receipt: Path, authorization: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    before = secret_client.describe_secret(SecretId=SECRET_NAME)
    validate_description(before, pending=True)
    restored = secret_client.restore_secret(SecretId=SECRET_ARN)
    after = secret_client.describe_secret(SecretId=SECRET_NAME)
    validate_description(after, pending=False)
    value = {
        "record": "B6_SYNTHETIC_SECRET_RESTORATION_STAGE",
        "status": "PASS_RESTORED_AWAITING_BOUNDARY_RECONSTRUCTION",
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "manifest_sha256": sha(MANIFEST.read_bytes()),
        "secret_arn": restored.get("ARN", SECRET_ARN),
        "historical_version_id": OLD_VERSION,
        "plaintext_read_or_created": False,
    }
    persist(receipt, value)
    return value


def adopt_restored(secret_client: Any, receipt: Path, authorization: dict[str, Any]) -> dict[str, Any]:
    current = secret_client.describe_secret(SecretId=SECRET_NAME)
    validate_description(current, pending=False)
    if TOKEN_PATH.exists():
        raise RestorationRefusal("local token unexpectedly exists before successor adoption")
    value = {
        "record": "B6_SYNTHETIC_SECRET_SUCCESSOR_ADOPTION_STAGE",
        "status": "PASS_RESTORED_AWAITING_BOUNDARY_RECONSTRUCTION",
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "manifest_sha256": sha(MANIFEST.read_bytes()),
        "secret_arn": SECRET_ARN,
        "historical_version_id": OLD_VERSION,
        "source_refusal_receipt": "B6-PACKET-2026-012-REFUSED-IMPORTED-STATE-DRIFT",
        "aws_mutation_performed": False,
        "plaintext_read_or_created": False,
    }
    persist(receipt, value)
    return value


def write_token(token: str) -> None:
    if len(token) != 43 or not token.isascii() or "\n" in token or "\r" in token:
        raise RestorationRefusal("generated token encoding differs")
    descriptor = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (token + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if stat.S_IMODE(TOKEN_PATH.stat().st_mode) != 0o600 or TOKEN_PATH.stat().st_size != 44:
        raise RestorationRefusal("local token boundary differs")


def rotate(
    secret_client: Any,
    iam_client: Any,
    receipt: Path,
    authorization: dict[str, Any],
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> dict[str, Any]:
    validate_description(secret_client.describe_secret(SecretId=SECRET_NAME), pending=False)
    actual_policy = json.loads(secret_client.get_resource_policy(SecretId=SECRET_ARN)["ResourcePolicy"])
    if canonical(actual_policy) != canonical(expected_resource_policy()):
        raise RestorationRefusal("secret resource policy differs")
    validation = secret_client.validate_resource_policy(
        SecretId=SECRET_ARN,
        ResourcePolicy=json.dumps(expected_resource_policy(), separators=(",", ":")),
    )
    if validation.get("PolicyValidationPassed") is not True:
        raise RestorationRefusal("secret resource policy validation failed")
    kms_policy = iam_client.get_role_policy(
        RoleName="medzen-orch-role",
        PolicyName="medzen-orch-b6-client-secret-kms",
    )["PolicyDocument"]
    validate_kms_policy(kms_policy)
    if TOKEN_PATH.exists():
        raise RestorationRefusal("local token already exists")
    token = token_factory(32)
    write_token(token)
    token_hash = sha(token.encode("ascii"))
    secret_value = {
        "schema_version": 1,
        "classification": "B6_6_SYNTHETIC_INTEGRATION_ONLY",
        "clients": [{"client_id": "b6-window-probe", "enabled": True, "key_sha256": token_hash}],
    }
    published = secret_client.put_secret_value(
        SecretId=SECRET_ARN,
        SecretString=canonical(secret_value).decode().rstrip("\n"),
        VersionStages=["AWSCURRENT"],
    )
    value = {
        "record": "B6_SYNTHETIC_SECRET_ROTATION_STAGE",
        "status": "PASS_ROTATED_AWAITING_VERIFICATION",
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "new_version_id": published["VersionId"],
        "historical_version_id": OLD_VERSION,
        "bearer_token_sha256": token_hash,
        "secret_value_sha256": sha(canonical(secret_value).rstrip(b"\n")),
        "local_token_path": str(TOKEN_PATH),
        "local_token_mode": "0600",
        "plaintext_recorded": False,
    }
    persist(receipt, value)
    return value


def _error_code(exc: Exception) -> str | None:
    return getattr(exc, "response", {}).get("Error", {}).get("Code")


def verify(secret_client: Any, rotation_receipt: Path, receipt: Path, authorization: dict[str, Any]) -> dict[str, Any]:
    rotation = json.loads(rotation_receipt.read_bytes())
    if rotation.get("status") != "PASS_ROTATED_AWAITING_VERIFICATION":
        raise RestorationRefusal("rotation receipt is absent or malformed")
    if not TOKEN_PATH.is_file() or stat.S_IMODE(TOKEN_PATH.stat().st_mode) != 0o600:
        raise RestorationRefusal("local token is absent or has wrong mode")
    raw = TOKEN_PATH.read_bytes()
    if len(raw) != 44 or raw[-1:] != b"\n" or sha(raw[:-1]) != rotation["bearer_token_sha256"]:
        raise RestorationRefusal("local token does not match rotation receipt")
    versions = secret_client.list_secret_version_ids(SecretId=SECRET_ARN, IncludeDeprecated=True)["Versions"]
    by_id = {item["VersionId"]: item.get("VersionStages", []) for item in versions}
    new_version = rotation["new_version_id"]
    if by_id.get(new_version) != ["AWSCURRENT"] or by_id.get(OLD_VERSION) != ["AWSPREVIOUS"]:
        raise RestorationRefusal("secret version transition differs")
    secret_client.update_secret_version_stage(
        SecretId=SECRET_ARN,
        VersionStage="AWSPREVIOUS",
        RemoveFromVersionId=OLD_VERSION,
    )
    versions = secret_client.list_secret_version_ids(SecretId=SECRET_ARN, IncludeDeprecated=True)["Versions"]
    by_id = {item["VersionId"]: item.get("VersionStages", []) for item in versions}
    if by_id.get(new_version) != ["AWSCURRENT"] or by_id.get(OLD_VERSION) != []:
        raise RestorationRefusal("historical version still has a staging label")
    try:
        secret_client.get_secret_value(SecretId=SECRET_ARN)
    except Exception as exc:
        if _error_code(exc) != "AccessDeniedException":
            raise
        operator_read = "EXPLICITLY_DENIED_AS_REQUIRED"
    else:
        raise RestorationRefusal("operator unexpectedly read the secret")
    value = {
        "record": "B6_SYNTHETIC_SECRET_VERIFICATION_STAGE",
        "status": "VERIFIED_COMPLETE",
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "new_version_id": new_version,
        "new_version_stages": ["AWSCURRENT"],
        "historical_version_id": OLD_VERSION,
        "historical_version_stages": [],
        "operator_get_secret_value": operator_read,
        "only_allowed_reader": EXPECTED_ORCHESTRATOR,
        "plaintext_recorded": False,
    }
    persist(receipt, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("adopt", "rotate", "verify"))
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("REFUSING: --apply is required for approved AWS execution", file=sys.stderr)
        return 2
    try:
        authorization, manifest, _ = load_authorization(args.authorization.resolve())
        import boto3

        session = boto3.Session(profile_name="medzen", region_name=EXPECTED_REGION)
        identity = session.client("sts").get_caller_identity()
        if identity.get("Account") != EXPECTED_ACCOUNT or identity.get("Arn") != EXPECTED_OPERATOR:
            raise RestorationRefusal("operator identity differs")
        secret_client = session.client("secretsmanager")
        if args.phase == "adopt":
            result = adopt_restored(secret_client, args.receipts_dir / "restore.json", authorization)
        elif args.phase == "rotate":
            require_receipt(
                args.receipts_dir / "restore.json",
                "PASS_RESTORED_AWAITING_BOUNDARY_RECONSTRUCTION",
            )
            require_receipt(
                args.receipts_dir / "terraform_reconciliation.json",
                "PASS_TERRAFORM_RECONCILED",
            )
            result = rotate(secret_client, session.client("iam"), args.receipts_dir / "rotation.json", authorization)
        else:
            require_receipt(
                args.receipts_dir / "restore.json",
                "PASS_RESTORED_AWAITING_BOUNDARY_RECONSTRUCTION",
            )
            require_receipt(
                args.receipts_dir / "terraform_reconciliation.json",
                "PASS_TERRAFORM_RECONCILED",
            )
            result = verify(secret_client, args.receipts_dir / "rotation.json", args.receipts_dir / "verification.json", authorization)
    except (OSError, KeyError, ValueError, ClientError, RestorationRefusal) as exc:
        print(f"REFUSING OR STOPPED B6 CLIENT-SECRET RESTORATION: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"phase": args.phase, "status": result["status"], "plaintext_recorded": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
