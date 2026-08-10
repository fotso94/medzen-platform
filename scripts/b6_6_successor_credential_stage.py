#!/usr/bin/env python3
"""Restore and rotate the B6 synthetic credential as successor stage 0."""
from __future__ import annotations

import argparse
import json
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

from scripts import run_b6_client_secret_restoration_2026_015 as proven
from scripts.b6_6_successor_bindings import validate as validate_bindings


ACCOUNT = proven.ACCOUNT
REGION = proven.REGION
PROFILE = proven.PROFILE
OPERATOR = proven.OPERATOR
ORCHESTRATOR = proven.ORCHESTRATOR
SECRET_ARN = proven.SECRET_ARN
TOKEN_PATH = proven.TOKEN_PATH
CURRENT_VERSION = "daacb67e-fcd1-41e1-bf62-47a3f18c8d0b"
LEGACY_VERSIONS = (
    "d09d567e-9bde-482a-b95a-3cab990a1006",
    "f78c8aa8-2765-4788-9928-dd1ba7c406bf",
)
PACKET_EXPIRES_UTC = datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc)
STATUSES = proven.RECEIPT_STATUSES
RestorationRefusal = proven.RestorationRefusal


def expected_before(*, pending: bool) -> dict[str, list[str]]:
    del pending
    return {
        CURRENT_VERSION: ["AWSCURRENT"],
        LEGACY_VERSIONS[0]: [],
        LEGACY_VERSIONS[1]: [],
    }


def _persist(
    stage: str,
    status: str,
    receipt: Path,
    authorization: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    value = {
        "record": "B6_2026_017_CREDENTIAL_STAGE_0",
        "stage": stage,
        "status": status,
        "recorded_utc": proven.now(),
        "authorization_id": authorization["id"],
        **payload,
        "plaintext_recorded": False,
    }
    proven.persist(receipt, value)
    return value


def require_receipt(directory: Path, stage: str) -> dict[str, Any]:
    try:
        value = json.loads((directory / f"{stage}.json").read_bytes())
    except Exception as exc:
        raise RestorationRefusal(f"durable {stage} receipt is absent") from exc
    if value.get("stage") != stage or value.get("authorization_id") != "B6-AWS-AUTH-2026-017":
        raise RestorationRefusal(f"durable {stage} receipt differs")
    return value


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
        raise RestorationRefusal("successor recovery window has expired")
    if identity != {"Account": ACCOUNT, "Arn": OPERATOR}:
        raise RestorationRefusal("operator identity differs")
    if addresses or zero_boundary != proven.ZERO_BOUNDARY or TOKEN_PATH.exists():
        raise RestorationRefusal("stage-0 zero boundary differs")
    state = proven.validate_secret(
        secret_client,
        pending=True,
        expected_versions=expected_before(pending=True),
    )
    proven._require_policy_absent(secret_client)
    proven._require_kms_policy_absent(iam_client)
    return _persist(
        "preflight",
        STATUSES["preflight"],
        receipt,
        authorization,
        {
            "secret_arn": SECRET_ARN,
            "pending_deletion_timestamp_present": (
                state["description"].get("DeletedDate") is not None
            ),
            "prior_current_version_id": CURRENT_VERSION,
            "legacy_version_ids": list(LEGACY_VERSIONS),
            "terraform_addresses": 0,
            **zero_boundary,
            "local_material": "ABSENT",
            "plaintext_read": False,
        },
    )


def restore(secret_client: Any, receipt: Path, authorization: dict[str, Any]) -> dict[str, Any]:
    proven.validate_secret(
        secret_client,
        pending=True,
        expected_versions=expected_before(pending=True),
    )
    result = secret_client.restore_secret(SecretId=SECRET_ARN)
    proven.validate_secret(
        secret_client,
        pending=False,
        expected_versions=expected_before(pending=False),
    )
    if result.get("ARN") not in (None, SECRET_ARN):
        raise RestorationRefusal("restored secret ARN differs")
    return _persist(
        "restore",
        STATUSES["restore"],
        receipt,
        authorization,
        {"secret_arn": SECRET_ARN, "restore_secret_calls": 1, "plaintext_read": False},
    )


def record_terraform(
    stage: str,
    payload: dict[str, Any],
    receipt: Path,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    allowed = {
        "terraform_import": {"state_lineage", "state_serial", "address", "secret_arn"},
        "terraform_normalization": {
            "mode", "plan_sha256", "residual_plan_sha256", "state_lineage", "state_serial"
        },
        "terraform_reconciliation": {
            "plan_sha256", "residual_plan_sha256", "state_lineage", "state_serial",
            "resource_policy_sha256", "kms_policy_sha256",
        },
    }
    if stage not in allowed or set(payload) != allowed[stage]:
        raise RestorationRefusal("Terraform receipt payload boundary differs")
    if stage == "terraform_import" and (
        payload.get("address") != "aws_secretsmanager_secret.b6_client_keys[0]"
        or payload.get("secret_arn") != SECRET_ARN
    ):
        raise RestorationRefusal("Terraform import receipt identity differs")
    if stage == "terraform_normalization" and payload.get("mode") not in {
        "APPLIED_EXACT_NORMALIZATION", "NO_NORMALIZATION_REQUIRED"
    }:
        raise RestorationRefusal("Terraform normalization mode differs")
    for key, value in payload.items():
        if key.endswith("sha256") and (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise RestorationRefusal("Terraform receipt hash is malformed")
    return _persist(stage, STATUSES[stage], receipt, authorization, payload)


def rotate(
    secret_client: Any,
    iam_client: Any,
    receipt: Path,
    authorization: dict[str, Any],
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> dict[str, Any]:
    proven.validate_secret(
        secret_client,
        pending=False,
        expected_versions=expected_before(pending=False),
    )
    proven._validate_live_policies(secret_client, iam_client)
    if TOKEN_PATH.exists():
        raise RestorationRefusal("local token already exists")
    token = token_factory(32)
    proven.write_token(token)
    material_sha = proven.sha(token.encode("ascii"))
    secret_value = {
        "schema_version": 1,
        "classification": "B6_6_SYNTHETIC_INTEGRATION_ONLY",
        "clients": [{
            "client_id": "b6-window-probe",
            "enabled": True,
            "key_sha256": material_sha,
        }],
    }
    published = secret_client.put_secret_value(
        SecretId=SECRET_ARN,
        SecretString=proven.canonical(secret_value).decode().rstrip("\n"),
        VersionStages=["AWSCURRENT"],
    )
    version = published.get("VersionId")
    if not isinstance(version, str) or not version:
        raise RestorationRefusal("fresh secret version ID is absent")
    return _persist(
        "rotation",
        STATUSES["rotation"],
        receipt,
        authorization,
        {
            "fresh_version_id": version,
            "prior_current_version_id": CURRENT_VERSION,
            "legacy_version_ids": list(LEGACY_VERSIONS),
            "bearer_token_sha256": material_sha,
            "secret_value_sha256": proven.sha(proven.canonical(secret_value).rstrip(b"\n")),
            "local_token_path": str(TOKEN_PATH),
            "local_token_mode": "0600",
        },
    )


def verify(
    secret_client: Any,
    rotation_receipt: Path,
    receipt: Path,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    rotation = json.loads(rotation_receipt.read_bytes())
    if rotation.get("stage") != "rotation" or rotation.get("status") != STATUSES["rotation"]:
        raise RestorationRefusal("rotation receipt is absent or malformed")
    raw = TOKEN_PATH.read_bytes()
    if (
        stat.S_IMODE(TOKEN_PATH.stat().st_mode) != 0o600
        or len(raw) != 44
        or raw[-1:] != b"\n"
        or proven.sha(raw[:-1]) != rotation.get("bearer_token_sha256")
    ):
        raise RestorationRefusal("local token does not match rotation receipt")
    fresh = str(rotation.get("fresh_version_id", ""))
    expected = {
        fresh: ["AWSCURRENT"],
        CURRENT_VERSION: ["AWSPREVIOUS"],
        LEGACY_VERSIONS[0]: [],
        LEGACY_VERSIONS[1]: [],
    }
    if proven.version_map(secret_client) != expected:
        raise RestorationRefusal("secret version transition differs")
    secret_client.update_secret_version_stage(
        SecretId=SECRET_ARN,
        VersionStage="AWSPREVIOUS",
        RemoveFromVersionId=CURRENT_VERSION,
    )
    expected[CURRENT_VERSION] = []
    if proven.version_map(secret_client) != expected:
        raise RestorationRefusal("prior versions are not all unstaged")
    try:
        secret_client.get_secret_value(SecretId=SECRET_ARN)
    except ClientError as exc:
        if proven._error_code(exc) != "AccessDeniedException":
            raise
        operator_read = "EXPLICITLY_DENIED_AS_REQUIRED"
    else:
        raise RestorationRefusal("operator unexpectedly read secret plaintext")
    return _persist(
        "verification",
        STATUSES["verification"],
        receipt,
        authorization,
        {
            "fresh_version_id": fresh,
            "fresh_version_stages": ["AWSCURRENT"],
            "all_prior_version_ids": [CURRENT_VERSION, *LEGACY_VERSIONS],
            "all_prior_versions_unstaged": True,
            "bearer_token_sha256": rotation["bearer_token_sha256"],
            "secret_value_sha256": rotation["secret_value_sha256"],
            "operator_get_secret_value": operator_read,
            "only_allowed_reader": ORCHESTRATOR,
        },
    )


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
    proven._require_policy_absent(secret_client)
    proven._require_kms_policy_absent(iam_client)
    description = secret_client.describe_secret(SecretId=SECRET_ARN)
    if description.get("DeletedDate") is None:
        secret_client.delete_secret(SecretId=SECRET_ARN, RecoveryWindowInDays=7)
    state = proven.validate_secret(secret_client, pending=True, expected_versions=None)
    return _persist(
        "cleanup",
        STATUSES["cleanup"],
        receipt,
        authorization,
        {
            "secret_arn": SECRET_ARN,
            "pending_deletion_timestamp_present": (
                state["description"].get("DeletedDate") is not None
            ),
            "terraform_addresses": 0,
            "resource_policy": "ABSENT",
            "orchestrator_kms_inline_policy": "ABSENT",
            "local_material": "ABSENT",
            "force_delete_without_recovery": False,
            "plaintext_read": False,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=(
            "preflight", "restore", "record-terraform-import",
            "record-terraform-normalization", "record-terraform-reconciliation",
            "rotate", "verify", "cleanup",
        ),
    )
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--payload-json")
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("REFUSING: --apply is required for packet execution", file=sys.stderr)
        return 2
    try:
        authorization = validate_bindings(
            args.authorization.resolve(), args.packet_sha256, ROOT
        )
        if args.phase.startswith("record-terraform-"):
            stage = args.phase.removeprefix("record-").replace("-", "_")
            prerequisite = {
                "terraform_import": "restore",
                "terraform_normalization": "terraform_import",
                "terraform_reconciliation": "terraform_normalization",
            }[stage]
            require_receipt(args.receipts_dir, prerequisite)
            result = record_terraform(
                stage,
                json.loads(args.payload_json or "{}"),
                args.receipts_dir / f"{stage}.json",
                authorization,
            )
        else:
            session = proven._session(args.profile)
            identity = proven._identity(session)
            secret_client = session.client("secretsmanager")
            iam_client = session.client("iam")
            if args.phase == "preflight":
                result = preflight(
                    secret_client,
                    iam_client,
                    identity,
                    proven.terraform_state_addresses(),
                    proven.verify_zero_boundaries(session),
                    args.receipts_dir / "preflight.json",
                    authorization,
                )
            elif args.phase == "restore":
                require_receipt(args.receipts_dir, "preflight")
                result = restore(secret_client, args.receipts_dir / "restore.json", authorization)
            elif args.phase == "rotate":
                for stage in (
                    "restore", "terraform_import", "terraform_normalization",
                    "terraform_reconciliation",
                ):
                    require_receipt(args.receipts_dir, stage)
                result = rotate(secret_client, iam_client, args.receipts_dir / "rotation.json", authorization)
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
                    proven.terraform_state_addresses(),
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
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
