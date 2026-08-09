#!/usr/bin/env python3
"""Publish the owner-approved B6.5C content-addressed registry snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "platform/manifests/B6-DEPLOYMENT-REGISTRY-2026-001.json"
EXPECTED_ACCOUNT = "558069890522"
EXPECTED_REGION = "eu-central-1"
EXPECTED_OPERATOR = f"arn:aws:iam::{EXPECTED_ACCOUNT}:user/s.fotso"
PUBLISHER_ROLE = f"arn:aws:iam::{EXPECTED_ACCOUNT}:role/medzen-registry-publisher-role"
PRODUCTION_POINTER = "/medzen/registry/serving/current"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/speech-orchestrator"))

from scripts.check_b6_deployment_registry_packet import validate as validate_manifest  # noqa: E402
from medzen_speech_orchestrator.registry import Parameter, RegistryRouter  # noqa: E402


class PublicationRefusal(RuntimeError):
    """An authorization, identity, immutable input or AWS result disagreed."""


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
        raise PublicationRefusal("owner authorization is absent")
    request = validate_manifest()
    bound_manifest = authorization.get("request_manifest", {})
    if (
        bound_manifest.get("path") != str(MANIFEST.relative_to(ROOT))
        or bound_manifest.get("sha256") != sha256(MANIFEST.read_bytes())
    ):
        raise PublicationRefusal("authorization manifest binding mismatch")
    bound_packet = authorization.get("packet", {})
    packet = ROOT / str(bound_packet.get("path", ""))
    if not packet.is_file() or bound_packet.get("sha256") != sha256(packet.read_bytes()):
        raise PublicationRefusal("authorization packet binding mismatch")
    return authorization, request, packet


def optional_parameter(ssm: Any, name: str) -> dict[str, Any] | None:
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise


def pointer_receipt(ssm: Any) -> dict[str, Any]:
    item = optional_parameter(ssm, PRODUCTION_POINTER)
    return (
        {"exists": False, "version": None, "value_sha256": None}
        if item is None
        else {
            "exists": True,
            "version": item["Version"],
            "value_sha256": sha256(item["Value"].encode("utf-8")),
        }
    )


def tag_map(ssm: Any, name: str) -> dict[str, str]:
    response = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)
    return {item["Key"]: item["Value"] for item in response.get("TagList", [])}


def description(operator_ssm: Any, name: str) -> dict[str, Any]:
    response = operator_ssm.describe_parameters(ParameterFilters=[{
        "Key": "Name", "Option": "Equals", "Values": [name]
    }])
    matches = [item for item in response.get("Parameters", []) if item.get("Name") == name]
    if len(matches) != 1:
        raise PublicationRefusal(f"parameter description missing or ambiguous: {name}")
    return matches[0]


def inspect(
    publisher_ssm: Any, operator_ssm: Any, request: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    present = 0
    for expected in sorted(request["parameters"], key=lambda item: item["PublishOrder"]):
        item = optional_parameter(publisher_ssm, expected["Name"])
        if item is None:
            receipts.append({"name": expected["Name"], "state": "ABSENT"})
            continue
        present += 1
        meta = description(operator_ssm, expected["Name"])
        tags = tag_map(publisher_ssm, expected["Name"])
        if (
            item.get("Value") != expected["Value"]
            or item.get("Type") != "SecureString"
            or item.get("Version") != 1
            or meta.get("KeyId") != expected["KeyId"]
            or meta.get("Tier") != "Standard"
            or meta.get("DataType") != "text"
            or tags != request["allocation"]["tags"]
        ):
            raise PublicationRefusal(f"immutable parameter mismatch: {expected['Name']}")
        receipts.append({
            "name": expected["Name"],
            "state": "PRESENT_IDENTICAL",
            "version": item["Version"],
            "value_sha256": expected["ValueSHA256"],
            "tags": tags,
        })
    if present == 0:
        return "CREATE", receipts
    if present == len(request["parameters"]):
        return "REUSE_IDENTICAL_COMPLETE", receipts
    raise PublicationRefusal("partial deployment registry refuses publication")


class ReadbackStore:
    def __init__(self, parameters: list[dict[str, Any]]):
        self.values = {
            item["Name"]: Parameter(
                Name=item["Name"], Type=item["Type"], Value=item["Value"],
                Version=item["Version"],
            )
            for item in parameters
        }

    def get_parameter(self, name: str) -> Parameter:
        return self.values[name]

    def get_parameters_by_path(self, path: str) -> tuple[Parameter, ...]:
        prefix = path.rstrip("/") + "/"
        return tuple(
            self.values[name] for name in sorted(self.values) if name.startswith(prefix)
        )


def expected_tags(request: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"Key": key, "Value": value}
        for key, value in sorted(request["allocation"]["tags"].items())
    ]


def execute(authorization_path: Path, receipt_path: Path) -> dict[str, Any]:
    import boto3

    started = now()
    authorization, request, packet = load_authorization(authorization_path)
    session = boto3.Session(profile_name="medzen", region_name=EXPECTED_REGION)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    if identity.get("Account") != EXPECTED_ACCOUNT or identity.get("Arn") != EXPECTED_OPERATOR:
        raise PublicationRefusal("operator account or caller identity mismatch")
    assumed = sts.assume_role(
        RoleArn=PUBLISHER_ROLE,
        RoleSessionName="medzen-b6-5c-2026-001",
        DurationSeconds=3600,
    )["Credentials"]
    publisher_session = boto3.Session(
        aws_access_key_id=assumed["AccessKeyId"],
        aws_secret_access_key=assumed["SecretAccessKey"],
        aws_session_token=assumed["SessionToken"],
        region_name=EXPECTED_REGION,
    )
    publisher_arn = publisher_session.client("sts").get_caller_identity()["Arn"]
    if not publisher_arn.startswith(
        f"arn:aws:sts::{EXPECTED_ACCOUNT}:assumed-role/medzen-registry-publisher-role/"
    ):
        raise PublicationRefusal("publisher assumed-role identity mismatch")

    publisher_ssm = publisher_session.client("ssm")
    operator_ssm = session.client("ssm")
    pointer_before = pointer_receipt(publisher_ssm)
    if pointer_before["exists"]:
        raise PublicationRefusal("production serving pointer unexpectedly exists")
    mode, preflight = inspect(publisher_ssm, operator_ssm, request)
    created: list[str] = []
    try:
        if mode == "CREATE":
            tags = expected_tags(request)
            for item in sorted(request["parameters"], key=lambda value: value["PublishOrder"]):
                publisher_ssm.put_parameter(
                    Name=item["Name"],
                    Description="MedZen B6.5C non-serving deployment registry",
                    Value=item["Value"],
                    Type="SecureString",
                    KeyId=item["KeyId"],
                    Overwrite=False,
                    Tier="Standard",
                    DataType="text",
                    Tags=tags,
                )
                created.append(item["Name"])
        result, receipts = inspect(publisher_ssm, operator_ssm, request)
        if result != "REUSE_IDENTICAL_COMPLETE":
            raise PublicationRefusal("post-publication snapshot is incomplete")
        parameters = [
            publisher_ssm.get_parameter(Name=item["Name"], WithDecryption=True)["Parameter"]
            for item in request["parameters"]
        ]
        router = RegistryRouter(ReadbackStore(parameters), request["snapshot"]["root"])
        if router.snapshot_sha256 != request["snapshot"]["snapshot_material_sha256"]:
            raise PublicationRefusal("RegistryRouter snapshot identity mismatch")
        pointer_after = pointer_receipt(publisher_ssm)
        if pointer_after != pointer_before:
            raise PublicationRefusal("production pointer changed")
    except Exception:
        if created:
            operator_ssm.delete_parameters(Names=created)
        raise

    receipt = {
        "record": "B6_DEPLOYMENT_REGISTRY_AWS_EXECUTION",
        "id": "B6-DEPLOYMENT-REGISTRY-2026-001",
        "revision": 1,
        "status": "VERIFIED_COMPLETE",
        "started_utc": started,
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
            "publisher_session_arn": publisher_arn,
        },
        "publication": {
            "outcome": "PUBLISHED_VERIFIED_NON_SERVING" if mode == "CREATE" else mode,
            "snapshot_sha256": request["snapshot"]["snapshot_material_sha256"],
            "root": request["snapshot"]["root"],
            "parameter_count": len(receipts),
            "parameters": receipts,
        },
        "production_pointer": {
            "before": pointer_before,
            "after": pointer_after,
            "changes": 0,
        },
        "explicit_non_events": {
            "parameters_outside_exact_root": 0,
            "production_alias_changes": 0,
            "new_iam_roles": 0,
            "new_kms_keys": 0,
            "nodes_scaled": 0,
            "deployments": 0,
            "approved_model_changes": 0,
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
    except (OSError, KeyError, ValueError, ClientError, PublicationRefusal) as exc:
        print(f"REFUSING OR STOPPED B6.5C PUBLICATION: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": receipt["status"],
        "outcome": receipt["publication"]["outcome"],
        "snapshot_sha256": receipt["publication"]["snapshot_sha256"],
        "parameter_count": receipt["publication"]["parameter_count"],
        "production_alias_changes": 0,
        "receipt": str(args.receipt),
        "receipt_sha256": sha256(args.receipt.read_bytes()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
