#!/usr/bin/env python3
"""Persistent-secret, rotate-in-place credential stage for B6.6."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
SECRET_NAME = "medzen/client-api-keys"
SECRET_ARN = (
    "arn:aws:secretsmanager:eu-central-1:558069890522:"
    "secret:medzen/client-api-keys-NxZGxE"
)
KMS_KEY = "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57"
TOKEN_LENGTH_WITH_LF = 44


class CredentialRefusal(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _error_code(exc: Exception) -> str | None:
    return getattr(exc, "response", {}).get("Error", {}).get("Code")


def encode_token(material: bytes) -> bytes:
    if len(material) != 32:
        raise CredentialRefusal("credential material must contain exactly 32 bytes")
    token = base64.urlsafe_b64encode(material).rstrip(b"=")
    if len(token) != 43 or b"\n" in token or b"\r" in token:
        raise CredentialRefusal("credential token encoding differs")
    return token


def write_token(path: Path, token: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(token + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _verify_persistent_secret(secret_client: Any) -> None:
    description = secret_client.describe_secret(SecretId=SECRET_ARN)
    if (
        description.get("Name") != SECRET_NAME
        or description.get("ARN") != SECRET_ARN
        or description.get("KmsKeyId") != KMS_KEY
        or description.get("DeletedDate") is not None
    ):
        raise CredentialRefusal("persistent synthetic secret identity differs")


def _version_map(secret_client: Any) -> dict[str, list[str]]:
    response = secret_client.list_secret_version_ids(
        SecretId=SECRET_ARN,
        IncludeDeprecated=True,
    )
    result: dict[str, list[str]] = {}
    for item in response.get("Versions", []):
        version_id = item.get("VersionId")
        stages = item.get("VersionStages", [])
        if isinstance(version_id, str) and isinstance(stages, list):
            result[version_id] = sorted(str(stage) for stage in stages)
    return result


def _verify_operator_denied(secret_client: Any) -> str:
    try:
        secret_client.get_secret_value(SecretId=SECRET_ARN)
    except ClientError as exc:
        if _error_code(exc) != "AccessDeniedException":
            raise
        return "EXPLICITLY_DENIED_AS_REQUIRED"
    except Exception as exc:
        if _error_code(exc) != "AccessDeniedException":
            raise
        return "EXPLICITLY_DENIED_AS_REQUIRED"
    raise CredentialRefusal("operator unexpectedly read synthetic secret plaintext")


def rotate_and_verify(
    secret_client: Any,
    token_path: Path,
    *,
    material_factory: Callable[[int], bytes] = secrets.token_bytes,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    _verify_persistent_secret(secret_client)
    if token_path.exists():
        raise CredentialRefusal("local token path already exists")
    started = now()
    material = material_factory(32)
    token = encode_token(material)
    write_token(token_path, token)
    bearer_sha256 = sha256(token)
    secret_value = {
        "schema_version": 1,
        "classification": "B6_6_SYNTHETIC_INTEGRATION_ONLY",
        "clients": [
            {
                "client_id": "b6-window-probe",
                "enabled": True,
                "key_sha256": bearer_sha256,
            }
        ],
    }
    encoded_value = canonical(secret_value)
    value_sha256 = sha256(encoded_value)
    published = secret_client.put_secret_value(
        SecretId=SECRET_ARN,
        SecretString=encoded_value.decode(),
        ClientRequestToken=value_sha256,
        VersionStages=["AWSCURRENT"],
    )
    if published.get("VersionId") != value_sha256:
        raise CredentialRefusal("fresh version identity differs")
    versions = _version_map(secret_client)
    if "AWSCURRENT" not in set(versions.get(value_sha256, [])):
        raise CredentialRefusal("fresh version is not AWSCURRENT")
    raw = token_path.read_bytes()
    if (
        stat.S_IMODE(token_path.stat().st_mode) != 0o600
        or len(raw) != TOKEN_LENGTH_WITH_LF
        or raw[-1:] != b"\n"
        or raw[:-1] != token
        or sha256(raw[:-1]) != bearer_sha256
        or value_sha256 != sha256(canonical(secret_value))
    ):
        raise CredentialRefusal("fresh token or version binding differs")
    operator_read = _verify_operator_denied(secret_client)
    completed = now()
    if completed < started:
        raise CredentialRefusal("credential stage clock moved backwards")
    return {
        "status": "PASS",
        "fresh_random_bytes": 32,
        "token_file_mode": "0600",
        "token_file_bytes": TOKEN_LENGTH_WITH_LF,
        "token_line_feeds": 1,
        "fresh_version_id": value_sha256,
        "fresh_version_stage": "AWSCURRENT",
        "bearer_sha256": bearer_sha256,
        "secret_value_sha256": value_sha256,
        "version_id_binds_canonical_secret_value_sha256": True,
        "operator_get_secret_value": operator_read,
        "historical_version_count_evaluated": False,
        "secret_tag_count_evaluated": False,
        "plaintext_read": False,
        "plaintext_recorded": False,
    }


def session(profile: str):
    return boto3.Session(profile_name=profile, region_name=REGION)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--profile", default=PROFILE)
    args = parser.parse_args()
    try:
        result = rotate_and_verify(
            session(args.profile).client("secretsmanager"),
            args.token_file,
        )
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
