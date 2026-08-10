#!/usr/bin/env python3
"""Restore and rotate the exact post-attempt-4 B6 synthetic secret safely."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.b6_client_secret_restoration_2026_015_bindings import validate as validate_bindings


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
PACKET_EXPIRES_UTC = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)
OPERATOR = f"arn:aws:iam::{ACCOUNT}:user/s.fotso"
ORCHESTRATOR = f"arn:aws:iam::{ACCOUNT}:role/medzen-orch-role"
SECRET_NAME = "medzen/client-api-keys"
SECRET_ARN = (
    f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:medzen/client-api-keys-NxZGxE"
)
KMS_KEY = (
    f"arn:aws:kms:{REGION}:{ACCOUNT}:key/9c336116-c648-4548-95c6-1b926478ae57"
)
PRIOR_CURRENT_VERSION = "d09d567e-9bde-482a-b95a-3cab990a1006"
OLDER_VERSION = "f78c8aa8-2765-4788-9928-dd1ba7c406bf"
TOKEN_PATH = Path("/private/tmp/medzen-b6-6-client-token")
TERRAFORM_ADDRESSES = {
    "aws_secretsmanager_secret.b6_client_keys[0]",
    "aws_secretsmanager_secret_policy.b6_client_keys[0]",
    "aws_iam_role_policy.b6_client_keys_kms[0]",
}
ZERO_BOUNDARY = {
    "cpu_desired": 0,
    "cpu_instances": 0,
    "gpu_desired": 0,
    "gpu_instances": 0,
    "production_serving_pointer": "ABSENT",
}
RECEIPT_STATUSES = {
    "preflight": "PASS_EXACT_PENDING_STATE",
    "restore": "PASS_RESTORED_AWAITING_TERRAFORM",
    "terraform_import": "PASS_EXACT_SECRET_IMPORTED",
    "terraform_normalization": "PASS_TERRAFORM_NORMALIZED_OR_ALREADY_EXACT",
    "terraform_reconciliation": "PASS_TERRAFORM_RECONCILED",
    "rotation": "PASS_ROTATED_AWAITING_VERIFICATION",
    "verification": "VERIFIED_COMPLETE",
    "cleanup": "PASS_RECOVERABLE_ZERO_STATE",
}


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
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def require_receipt(directory: Path, stage: str) -> dict[str, Any]:
    path = directory / f"{stage}.json"
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise RestorationRefusal(f"durable {stage} receipt is absent") from exc
    if value.get("stage") != stage or value.get("status") != RECEIPT_STATUSES[stage]:
        raise RestorationRefusal(f"durable {stage} receipt differs")
    return value


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
                    "StringEquals": {
                        "kms:ViaService": f"secretsmanager.{REGION}.amazonaws.com"
                    },
                    "StringLike": {
                        "kms:EncryptionContext:SecretARN": (
                            f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:medzen/client-api-keys*"
                        )
                    },
                },
            },
        ],
    }


def _error_code(exc: Exception) -> str | None:
    return getattr(exc, "response", {}).get("Error", {}).get("Code")


def version_map(secret_client: Any) -> dict[str, list[str]]:
    versions = secret_client.list_secret_version_ids(
        SecretId=SECRET_ARN, IncludeDeprecated=True
    ).get("Versions", [])
    result: dict[str, list[str]] = {}
    for item in versions:
        version_id = item.get("VersionId")
        stages = item.get("VersionStages", [])
        if not isinstance(version_id, str) or not isinstance(stages, list):
            raise RestorationRefusal("secret version metadata is malformed")
        result[version_id] = sorted(stages)
    return result


def validate_secret(
    secret_client: Any,
    *,
    pending: bool,
    expected_versions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    description = secret_client.describe_secret(SecretId=SECRET_ARN)
    if (
        description.get("Name") != SECRET_NAME
        or description.get("ARN") != SECRET_ARN
        or description.get("KmsKeyId") != KMS_KEY
        or {item.get("Key"): item.get("Value") for item in description.get("Tags", [])}
        != expected_tags()
        or pending != (description.get("DeletedDate") is not None)
    ):
        raise RestorationRefusal("secret identity, tags, encryption or recovery state differs")
    versions = version_map(secret_client)
    if expected_versions is not None and versions != expected_versions:
        raise RestorationRefusal("secret version map differs")
    return {"description": description, "versions": versions}


def _require_policy_absent(secret_client: Any) -> None:
    try:
        value = secret_client.get_resource_policy(SecretId=SECRET_ARN)
    except ClientError as exc:
        if _error_code(exc) not in {"ResourceNotFoundException", "InvalidRequestException"}:
            raise
        return
    if value.get("ResourcePolicy"):
        raise RestorationRefusal("secret resource policy is unexpectedly present")


def _require_kms_policy_absent(iam_client: Any) -> None:
    try:
        iam_client.get_role_policy(
            RoleName="medzen-orch-role",
            PolicyName="medzen-orch-b6-client-secret-kms",
        )
    except ClientError as exc:
        if _error_code(exc) != "NoSuchEntity":
            raise
        return
    raise RestorationRefusal("orchestrator KMS inline policy is unexpectedly present")


def terraform_state_addresses() -> set[str]:
    process = subprocess.run(
        [str(ROOT / "scripts/terraform_medzen.sh"), "state", "list"],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(process.stdout.splitlines()) & TERRAFORM_ADDRESSES


def verify_zero_boundaries(session: Any) -> dict[str, Any]:
    eks = session.client("eks")
    values: dict[str, Any] = {}
    groups = {
        "cpu": ("eks-cpu-32cfd795-fa28-d1d9-1b8c-2ed678be1772", 4),
        "gpu": ("eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26", 1),
    }
    asg_names: list[str] = []
    for name, (asg_name, maximum) in groups.items():
        nodegroup = eks.describe_nodegroup(
            clusterName="medzen-speech", nodegroupName=name
        )["nodegroup"]
        if (
            nodegroup.get("status") != "ACTIVE"
            or nodegroup.get("scalingConfig")
            != {"minSize": 0, "maxSize": maximum, "desiredSize": 0}
            or nodegroup.get("health", {}).get("issues")
        ):
            raise RestorationRefusal(f"{name} node-group zero boundary differs")
        values[f"{name}_desired"] = 0
        asg_names.append(asg_name)
    autoscaling = session.client("autoscaling").describe_auto_scaling_groups(
        AutoScalingGroupNames=asg_names
    ).get("AutoScalingGroups", [])
    by_name = {item.get("AutoScalingGroupName"): item for item in autoscaling}
    if set(by_name) != set(asg_names):
        raise RestorationRefusal("worker auto-scaling group set differs")
    for name, (asg_name, maximum) in groups.items():
        group = by_name[asg_name]
        if (
            group.get("MinSize") != 0
            or group.get("MaxSize") != maximum
            or group.get("DesiredCapacity") != 0
            or group.get("Instances")
        ):
            raise RestorationRefusal(f"{name} auto-scaling zero boundary differs")
        values[f"{name}_instances"] = 0
    try:
        session.client("ssm").get_parameter(
            Name="/medzen/registry/serving/current", WithDecryption=True
        )
    except ClientError as exc:
        if _error_code(exc) != "ParameterNotFound":
            raise
    else:
        raise RestorationRefusal("production serving pointer unexpectedly exists")
    values["production_serving_pointer"] = "ABSENT"
    if values != ZERO_BOUNDARY:
        raise RestorationRefusal("zero boundary result differs")
    return values


def preflight(
    secret_client: Any,
    iam_client: Any,
    identity: dict[str, Any],
    addresses: set[str],
    zero_boundary: dict[str, Any],
    receipt: Path,
    authorization: dict[str, Any],
    current_time: datetime | None = None,
) -> dict[str, Any]:
    if (current_time or datetime.now(timezone.utc)) >= PACKET_EXPIRES_UTC:
        raise RestorationRefusal("packet recovery window has expired")
    if identity != {"Account": ACCOUNT, "Arn": OPERATOR}:
        raise RestorationRefusal("operator identity differs")
    if addresses:
        raise RestorationRefusal("synthetic secret Terraform addresses are unexpectedly present")
    if zero_boundary != ZERO_BOUNDARY:
        raise RestorationRefusal("compute or serving zero boundary differs")
    if TOKEN_PATH.exists():
        raise RestorationRefusal("local synthetic token unexpectedly exists")
    state = validate_secret(
        secret_client,
        pending=True,
        expected_versions={
            PRIOR_CURRENT_VERSION: ["AWSCURRENT"],
            OLDER_VERSION: [],
        },
    )
    _require_policy_absent(secret_client)
    _require_kms_policy_absent(iam_client)
    deleted = state["description"].get("DeletedDate")
    value = {
        "record": "B6_2026_015_CREDENTIAL_PREFLIGHT",
        "stage": "preflight",
        "status": RECEIPT_STATUSES["preflight"],
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "secret_arn": SECRET_ARN,
        "pending_deletion_timestamp_present": deleted is not None,
        "prior_current_version_id": PRIOR_CURRENT_VERSION,
        "older_version_id": OLDER_VERSION,
        "resource_policy": "ABSENT",
        "orchestrator_kms_inline_policy": "ABSENT",
        "terraform_addresses": 0,
        **zero_boundary,
        "local_token": "ABSENT",
        "plaintext_read": False,
    }
    persist(receipt, value)
    return value


def restore(
    secret_client: Any,
    receipt: Path,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    validate_secret(
        secret_client,
        pending=True,
        expected_versions={PRIOR_CURRENT_VERSION: ["AWSCURRENT"], OLDER_VERSION: []},
    )
    result = secret_client.restore_secret(SecretId=SECRET_ARN)
    validate_secret(
        secret_client,
        pending=False,
        expected_versions={PRIOR_CURRENT_VERSION: ["AWSCURRENT"], OLDER_VERSION: []},
    )
    if result.get("ARN") not in (None, SECRET_ARN):
        raise RestorationRefusal("restored secret ARN differs")
    value = {
        "record": "B6_2026_015_CREDENTIAL_RESTORE",
        "stage": "restore",
        "status": RECEIPT_STATUSES["restore"],
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "secret_arn": SECRET_ARN,
        "restore_secret_calls": 1,
        "plaintext_read": False,
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
        raise RestorationRefusal("local token file boundary differs")


def _validate_live_policies(secret_client: Any, iam_client: Any) -> None:
    resource = json.loads(
        secret_client.get_resource_policy(SecretId=SECRET_ARN)["ResourcePolicy"]
    )
    if canonical(resource) != canonical(expected_resource_policy()):
        raise RestorationRefusal("secret resource policy differs")
    validation = secret_client.validate_resource_policy(
        SecretId=SECRET_ARN,
        ResourcePolicy=json.dumps(expected_resource_policy(), separators=(",", ":")),
    )
    if validation.get("PolicyValidationPassed") is not True:
        raise RestorationRefusal("secret resource policy validation failed")
    kms = iam_client.get_role_policy(
        RoleName="medzen-orch-role",
        PolicyName="medzen-orch-b6-client-secret-kms",
    )["PolicyDocument"]
    if canonical(kms) != canonical(expected_kms_policy()):
        raise RestorationRefusal("orchestrator KMS inline policy differs")


def rotate(
    secret_client: Any,
    iam_client: Any,
    receipt: Path,
    authorization: dict[str, Any],
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> dict[str, Any]:
    validate_secret(
        secret_client,
        pending=False,
        expected_versions={PRIOR_CURRENT_VERSION: ["AWSCURRENT"], OLDER_VERSION: []},
    )
    _validate_live_policies(secret_client, iam_client)
    if TOKEN_PATH.exists():
        raise RestorationRefusal("local token already exists")
    token = token_factory(32)
    write_token(token)
    token_hash = sha(token.encode("ascii"))
    secret_value = {
        "schema_version": 1,
        "classification": "B6_6_SYNTHETIC_INTEGRATION_ONLY",
        "clients": [
            {
                "client_id": "b6-window-probe",
                "enabled": True,
                "key_sha256": token_hash,
            }
        ],
    }
    published = secret_client.put_secret_value(
        SecretId=SECRET_ARN,
        SecretString=canonical(secret_value).decode().rstrip("\n"),
        VersionStages=["AWSCURRENT"],
    )
    new_version = published.get("VersionId")
    if not isinstance(new_version, str) or not new_version:
        raise RestorationRefusal("new secret version ID is absent")
    value = {
        "record": "B6_2026_015_CREDENTIAL_ROTATION",
        "stage": "rotation",
        "status": RECEIPT_STATUSES["rotation"],
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "new_version_id": new_version,
        "prior_current_version_id": PRIOR_CURRENT_VERSION,
        "older_version_id": OLDER_VERSION,
        "bearer_token_sha256": token_hash,
        "secret_value_sha256": sha(canonical(secret_value).rstrip(b"\n")),
        "local_token_path": str(TOKEN_PATH),
        "local_token_mode": "0600",
        "plaintext_recorded": False,
    }
    persist(receipt, value)
    return value


def verify(
    secret_client: Any,
    rotation_receipt: Path,
    receipt: Path,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    rotation = json.loads(rotation_receipt.read_bytes())
    if (
        rotation.get("stage") != "rotation"
        or rotation.get("status") != RECEIPT_STATUSES["rotation"]
    ):
        raise RestorationRefusal("rotation receipt is absent or malformed")
    if not TOKEN_PATH.is_file() or stat.S_IMODE(TOKEN_PATH.stat().st_mode) != 0o600:
        raise RestorationRefusal("local token is absent or has wrong mode")
    raw = TOKEN_PATH.read_bytes()
    if (
        len(raw) != 44
        or raw[-1:] != b"\n"
        or sha(raw[:-1]) != rotation.get("bearer_token_sha256")
    ):
        raise RestorationRefusal("local token does not match rotation receipt")
    new_version = rotation.get("new_version_id")
    expected = {
        str(new_version): ["AWSCURRENT"],
        PRIOR_CURRENT_VERSION: ["AWSPREVIOUS"],
        OLDER_VERSION: [],
    }
    if version_map(secret_client) != expected:
        raise RestorationRefusal("secret version transition differs")
    secret_client.update_secret_version_stage(
        SecretId=SECRET_ARN,
        VersionStage="AWSPREVIOUS",
        RemoveFromVersionId=PRIOR_CURRENT_VERSION,
    )
    expected[PRIOR_CURRENT_VERSION] = []
    if version_map(secret_client) != expected:
        raise RestorationRefusal("prior current version still has a staging label")
    try:
        secret_client.get_secret_value(SecretId=SECRET_ARN)
    except ClientError as exc:
        if _error_code(exc) != "AccessDeniedException":
            raise
        operator_read = "EXPLICITLY_DENIED_AS_REQUIRED"
    else:
        raise RestorationRefusal("operator unexpectedly read secret plaintext")
    value = {
        "record": "B6_2026_015_CREDENTIAL_VERIFICATION",
        "stage": "verification",
        "status": RECEIPT_STATUSES["verification"],
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "new_version_id": new_version,
        "new_version_stages": ["AWSCURRENT"],
        "prior_current_version_id": PRIOR_CURRENT_VERSION,
        "prior_current_version_stages": [],
        "older_version_id": OLDER_VERSION,
        "older_version_stages": [],
        "operator_get_secret_value": operator_read,
        "only_allowed_reader": ORCHESTRATOR,
        "plaintext_recorded": False,
    }
    persist(receipt, value)
    return value


def record_terraform(
    stage: str,
    payload: dict[str, Any],
    receipt: Path,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    allowed = {
        "terraform_import": {
            "state_lineage",
            "state_serial",
            "address",
            "secret_arn",
        },
        "terraform_normalization": {
            "mode",
            "plan_sha256",
            "residual_plan_sha256",
            "state_lineage",
            "state_serial",
        },
        "terraform_reconciliation": {
            "plan_sha256",
            "residual_plan_sha256",
            "state_lineage",
            "state_serial",
            "resource_policy_sha256",
            "kms_policy_sha256",
        },
    }
    if stage not in allowed or set(payload) != allowed[stage]:
        raise RestorationRefusal("Terraform receipt payload boundary differs")
    if stage == "terraform_import" and payload != {
        **payload,
        "address": "aws_secretsmanager_secret.b6_client_keys[0]",
        "secret_arn": SECRET_ARN,
    }:
        raise RestorationRefusal("Terraform import receipt identity differs")
    if stage == "terraform_normalization" and payload.get("mode") not in {
        "APPLIED_EXACT_NORMALIZATION",
        "NO_NORMALIZATION_REQUIRED",
    }:
        raise RestorationRefusal("Terraform normalization mode differs")
    hash_fields = [key for key in payload if key.endswith("sha256")]
    if any(
        not isinstance(payload[key], str)
        or len(payload[key]) != 64
        or any(character not in "0123456789abcdef" for character in payload[key])
        for key in hash_fields
    ):
        raise RestorationRefusal("Terraform receipt hash is malformed")
    if not isinstance(payload.get("state_serial"), int) or payload["state_serial"] < 1:
        raise RestorationRefusal("Terraform state serial is malformed")
    if not isinstance(payload.get("state_lineage"), str) or not payload["state_lineage"]:
        raise RestorationRefusal("Terraform state lineage is malformed")
    value = {
        "record": "B6_2026_015_TERRAFORM_STAGE",
        "stage": stage,
        "status": RECEIPT_STATUSES[stage],
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        **payload,
        "plaintext_recorded": False,
    }
    persist(receipt, value)
    return value


def cleanup(
    secret_client: Any,
    iam_client: Any,
    addresses: set[str],
    receipt: Path,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    if addresses:
        raise RestorationRefusal("Terraform cleanup addresses remain")
    _require_policy_absent(secret_client)
    _require_kms_policy_absent(iam_client)
    description = secret_client.describe_secret(SecretId=SECRET_ARN)
    if description.get("DeletedDate") is None:
        secret_client.delete_secret(SecretId=SECRET_ARN, RecoveryWindowInDays=7)
    state = validate_secret(secret_client, pending=True, expected_versions=None)
    value = {
        "record": "B6_2026_015_CREDENTIAL_CLEANUP",
        "stage": "cleanup",
        "status": RECEIPT_STATUSES["cleanup"],
        "recorded_utc": now(),
        "authorization_id": authorization["id"],
        "secret_arn": SECRET_ARN,
        "pending_deletion_timestamp_present": (
            state["description"].get("DeletedDate") is not None
        ),
        "terraform_addresses": 0,
        "resource_policy": "ABSENT",
        "orchestrator_kms_inline_policy": "ABSENT",
        "local_token": "ABSENT",
        "force_delete_without_recovery": False,
        "plaintext_read": False,
    }
    persist(receipt, value)
    return value


def _session(profile: str) -> Any:
    import boto3

    return boto3.Session(profile_name=profile, region_name=REGION)


def _identity(session: Any) -> dict[str, str]:
    identity = session.client("sts").get_caller_identity()
    return {"Account": str(identity.get("Account", "")), "Arn": str(identity.get("Arn", ""))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=(
            "preflight",
            "restore",
            "record-terraform-import",
            "record-terraform-normalization",
            "record-terraform-reconciliation",
            "rotate",
            "verify",
            "cleanup",
        ),
    )
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--payload-json")
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("REFUSING: --apply is required for packet execution", file=sys.stderr)
        return 2
    try:
        authorization = validate_bindings(args.authorization.resolve(), ROOT)
        if args.phase.startswith("record-terraform-"):
            if not args.payload_json:
                raise RestorationRefusal("Terraform receipt payload is absent")
            stage = args.phase.removeprefix("record-").replace("-", "_")
            prerequisite = {
                "terraform_import": "restore",
                "terraform_normalization": "terraform_import",
                "terraform_reconciliation": "terraform_normalization",
            }[stage]
            require_receipt(args.receipts_dir, prerequisite)
            payload = json.loads(args.payload_json)
            result = record_terraform(
                stage,
                payload,
                args.receipts_dir / f"{stage}.json",
                authorization,
            )
        else:
            session = _session(args.profile)
            identity = _identity(session)
            if identity != {"Account": ACCOUNT, "Arn": OPERATOR}:
                raise RestorationRefusal("operator identity differs")
            secret_client = session.client("secretsmanager")
            iam_client = session.client("iam")
            if args.phase == "preflight":
                result = preflight(
                    secret_client,
                    iam_client,
                    identity,
                    terraform_state_addresses(),
                    verify_zero_boundaries(session),
                    args.receipts_dir / "preflight.json",
                    authorization,
                )
            elif args.phase == "restore":
                require_receipt(args.receipts_dir, "preflight")
                result = restore(
                    secret_client,
                    args.receipts_dir / "restore.json",
                    authorization,
                )
            elif args.phase == "rotate":
                for stage in (
                    "restore",
                    "terraform_import",
                    "terraform_normalization",
                    "terraform_reconciliation",
                ):
                    require_receipt(args.receipts_dir, stage)
                result = rotate(
                    secret_client,
                    iam_client,
                    args.receipts_dir / "rotation.json",
                    authorization,
                )
            elif args.phase == "verify":
                require_receipt(args.receipts_dir, "rotation")
                result = verify(
                    secret_client,
                    args.receipts_dir / "rotation.json",
                    args.receipts_dir / "verification.json",
                    authorization,
                )
            else:
                result = cleanup(
                    secret_client,
                    iam_client,
                    terraform_state_addresses(),
                    args.receipts_dir / "cleanup.json",
                    authorization,
                )
    except (
        BotoCoreError,
        ClientError,
        json.JSONDecodeError,
        OSError,
        KeyError,
        subprocess.SubprocessError,
        RestorationRefusal,
    ) as exc:
        print(
            json.dumps(
                {"status": "REFUSED", "reason_code": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {"phase": args.phase, "status": result["status"], "plaintext_recorded": False},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
