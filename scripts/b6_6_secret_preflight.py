#!/usr/bin/env python3
"""Read-only verification of the exact synthetic secret bound to B6.6 attempt 4."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_b6_client_secret_restoration import (
    EXPECTED_ACCOUNT,
    EXPECTED_OPERATOR,
    EXPECTED_REGION,
    KMS_KEY,
    OLD_VERSION,
    SECRET_ARN,
    SECRET_NAME,
    canonical,
    expected_resource_policy,
    expected_tags,
    validate_kms_policy,
)


NEW_VERSION = "d09d567e-9bde-482a-b95a-3cab990a1006"
RESOURCE_POLICY_SHA256 = "318a323fe01349dca140c8eff48cfef9da1cda163b6cc7616d3da718c0d20cb1"
KMS_POLICY_SHA256 = "8a9c8064b7a66e8003e326b4ae02a1288c7d304fd471734146f70fbaacbd5dd4"


class SecretPreflightRefusal(RuntimeError):
    pass


def verify(secret_client: Any, iam_client: Any, identity: dict[str, Any]) -> dict[str, Any]:
    if identity.get("Account") != EXPECTED_ACCOUNT or identity.get("Arn") != EXPECTED_OPERATOR:
        raise SecretPreflightRefusal("operator identity differs")
    description = secret_client.describe_secret(SecretId=SECRET_ARN)
    if (
        description.get("ARN") != SECRET_ARN
        or description.get("Name") != SECRET_NAME
        or description.get("KmsKeyId") != KMS_KEY
        or description.get("DeletedDate") is not None
    ):
        raise SecretPreflightRefusal("secret identity or recovery state differs")
    tags = {item.get("Key"): item.get("Value") for item in description.get("Tags", [])}
    if tags != expected_tags():
        raise SecretPreflightRefusal("secret tags differ")
    versions = secret_client.list_secret_version_ids(
        SecretId=SECRET_ARN, IncludeDeprecated=True
    ).get("Versions", [])
    version_map = {
        item.get("VersionId"): sorted(item.get("VersionStages", [])) for item in versions
    }
    if version_map != {NEW_VERSION: ["AWSCURRENT"], OLD_VERSION: []}:
        raise SecretPreflightRefusal("secret version map differs")
    resource_policy = json.loads(
        secret_client.get_resource_policy(SecretId=SECRET_ARN)["ResourcePolicy"]
    )
    if resource_policy != expected_resource_policy():
        raise SecretPreflightRefusal("secret resource policy differs")
    resource_policy_sha = hashlib.sha256(canonical(resource_policy)).hexdigest()
    if resource_policy_sha != RESOURCE_POLICY_SHA256:
        raise SecretPreflightRefusal("secret resource-policy hash differs")
    kms_policy = iam_client.get_role_policy(
        RoleName="medzen-orch-role", PolicyName="medzen-orch-b6-client-secret-kms"
    )["PolicyDocument"]
    validate_kms_policy(kms_policy)
    kms_policy_sha = hashlib.sha256(canonical(kms_policy)).hexdigest()
    if kms_policy_sha != KMS_POLICY_SHA256:
        raise SecretPreflightRefusal("orchestrator KMS-policy hash differs")
    return {
        "status": "PASS",
        "secret_version_id": NEW_VERSION,
        "historical_version_unstaged": True,
        "resource_policy_sha256": resource_policy_sha,
        "kms_policy_sha256": kms_policy_sha,
        "plaintext_read": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="medzen")
    args = parser.parse_args()
    try:
        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=EXPECTED_REGION)
        result = verify(
            session.client("secretsmanager"),
            session.client("iam"),
            session.client("sts").get_caller_identity(),
        )
    except (
        BotoCoreError,
        ClientError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        SecretPreflightRefusal,
    ) as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
