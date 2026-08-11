#!/usr/bin/env python3
"""Persistent-secret, rotate-in-place credential stage for B6.6."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
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
VERSION_VISIBILITY_WAIT_SECONDS = 120
VERSION_VISIBILITY_POLL_SECONDS = 5
VERSION_VISIBILITY_STABLE_OBSERVATIONS = 3
LOCAL_VERIFICATION_WAIT_SECONDS = 10
LOCAL_VERIFICATION_POLL_SECONDS = 1
LOCAL_VERIFICATION_STABLE_OBSERVATIONS = 3


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
    result: dict[str, list[str]] = {}
    next_token: str | None = None
    seen_tokens: set[str] = set()
    for _ in range(10):
        arguments: dict[str, Any] = {
            "SecretId": SECRET_ARN,
            "IncludeDeprecated": True,
        }
        if next_token is not None:
            arguments["NextToken"] = next_token
        response = secret_client.list_secret_version_ids(**arguments)
        versions = response.get("Versions")
        if not isinstance(versions, list):
            raise CredentialRefusal("secret version response is malformed")
        for item in versions:
            if not isinstance(item, dict):
                raise CredentialRefusal("secret version item is malformed")
            version_id = item.get("VersionId")
            stages = item.get("VersionStages", [])
            if (
                not isinstance(version_id, str)
                or not isinstance(stages, list)
                or not all(isinstance(stage, str) for stage in stages)
                or version_id in result
            ):
                raise CredentialRefusal("secret version item is malformed")
            result[version_id] = sorted(stages)
        raw_next = response.get("NextToken")
        if raw_next is None:
            return result
        if (
            not isinstance(raw_next, str)
            or not raw_next
            or raw_next in seen_tokens
        ):
            raise CredentialRefusal("secret version pagination is malformed")
        seen_tokens.add(raw_next)
        next_token = raw_next
    raise CredentialRefusal("secret version pagination exceeds the bound")


def wait_for_exact_current_version(
    secret_client: Any,
    version_id: str,
    *,
    wait_seconds: int = VERSION_VISIBILITY_WAIT_SECONDS,
    stable_observations: int = VERSION_VISIBILITY_STABLE_OBSERVATIONS,
    poll_seconds: int = VERSION_VISIBILITY_POLL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Poll the exact created version until AWSCURRENT is stably visible."""
    if (
        re.fullmatch(r"[0-9a-f]{64}", version_id) is None
        or wait_seconds < 1
        or wait_seconds > VERSION_VISIBILITY_WAIT_SECONDS
        or stable_observations != VERSION_VISIBILITY_STABLE_OBSERVATIONS
        or poll_seconds != VERSION_VISIBILITY_POLL_SECONDS
    ):
        raise CredentialRefusal("credential visibility wait boundary differs")
    started = monotonic()
    deadline = started + wait_seconds
    consecutive = 0
    polls = 0
    while True:
        polls += 1
        versions = _version_map(secret_client)
        stages = set(versions.get(version_id, []))
        current_versions = {
            candidate
            for candidate, candidate_stages in versions.items()
            if "AWSCURRENT" in set(candidate_stages)
        }
        if stages == {"AWSCURRENT"} and current_versions == {version_id}:
            consecutive += 1
            if consecutive == stable_observations:
                return {
                    "visibility_polls": polls,
                    "stable_current_observations": consecutive,
                    "visibility_wait_seconds": int(monotonic() - started),
                }
        else:
            consecutive = 0
        if monotonic() >= deadline:
            raise CredentialRefusal(
                "fresh version did not become stably AWSCURRENT before timeout"
            )
        sleep(poll_seconds)


def wait_for_local_token_stable(
    token_path: Path,
    token: bytes,
    bearer_sha256: str,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    stop = monotonic() + LOCAL_VERIFICATION_WAIT_SECONDS
    consecutive = 0
    polls = 0
    while True:
        polls += 1
        try:
            raw = token_path.read_bytes()
            matches = (
                stat.S_IMODE(token_path.stat().st_mode) == 0o600
                and len(raw) == TOKEN_LENGTH_WITH_LF
                and raw[-1:] == b"\n"
                and raw[:-1] == token
                and sha256(raw[:-1]) == bearer_sha256
            )
        except OSError:
            matches = False
        if matches:
            consecutive += 1
            if consecutive == LOCAL_VERIFICATION_STABLE_OBSERVATIONS:
                return {
                    "local_token_stable_observations": consecutive,
                    "local_token_verification_polls": polls,
                }
        else:
            consecutive = 0
        if monotonic() >= stop:
            raise CredentialRefusal("fresh local token did not remain stable")
        sleep(LOCAL_VERIFICATION_POLL_SECONDS)


def wait_for_operator_denied(
    secret_client: Any,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    stop = monotonic() + LOCAL_VERIFICATION_WAIT_SECONDS
    consecutive = 0
    polls = 0
    while True:
        polls += 1
        try:
            secret_client.get_secret_value(SecretId=SECRET_ARN)
        except ClientError as exc:
            if _error_code(exc) != "AccessDeniedException":
                raise
            consecutive += 1
        except Exception as exc:
            if _error_code(exc) != "AccessDeniedException":
                raise
            consecutive += 1
        else:
            raise CredentialRefusal(
                "operator unexpectedly read synthetic secret plaintext"
            )
        if consecutive == LOCAL_VERIFICATION_STABLE_OBSERVATIONS:
            return {
                "operator_get_secret_value": "EXPLICITLY_DENIED_AS_REQUIRED",
                "operator_denial_stable_observations": consecutive,
                "operator_denial_verification_polls": polls,
            }
        if monotonic() >= stop:
            raise CredentialRefusal("operator denial did not remain stable")
        sleep(LOCAL_VERIFICATION_POLL_SECONDS)


def rotate_and_verify(
    secret_client: Any,
    token_path: Path,
    *,
    material_factory: Callable[[int], bytes] = secrets.token_bytes,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
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
    visibility = wait_for_exact_current_version(
        secret_client,
        value_sha256,
        monotonic=monotonic,
        sleep=sleep,
    )
    if value_sha256 != sha256(canonical(secret_value)):
        raise CredentialRefusal("fresh token or version binding differs")
    local_token = wait_for_local_token_stable(
        token_path,
        token,
        bearer_sha256,
        monotonic=monotonic,
        sleep=sleep,
    )
    operator_denial = wait_for_operator_denied(
        secret_client,
        monotonic=monotonic,
        sleep=sleep,
    )
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
        **visibility,
        **local_token,
        "bearer_sha256": bearer_sha256,
        "secret_value_sha256": value_sha256,
        "version_id_binds_canonical_secret_value_sha256": True,
        **operator_denial,
        "historical_version_count_evaluated": False,
        "secret_tag_count_evaluated": False,
        "plaintext_read": False,
        "plaintext_recorded": False,
    }


def session(profile: str):
    return boto3.Session(profile_name=profile, region_name=REGION)


def safe_exception_text(exc: Exception) -> str:
    """Bound pre-model diagnostic text; credential material is never included."""
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", str(exc)).strip()
    value = re.sub(r"(?i)bearer\s+[^\s,;]+", "[CREDENTIAL_REDACTED]", value)
    return value[:512] or type(exc).__name__


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
        print(
            json.dumps(
                {
                    "status": "REFUSED",
                    "reason_code": type(exc).__name__,
                    "safe_error_text": safe_exception_text(exc),
                    "pre_model_and_audio": True,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
