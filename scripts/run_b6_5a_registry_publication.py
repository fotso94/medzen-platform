#!/usr/bin/env python3
"""Execute the owner-approved B6.5A create-only test-registry publication."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/B6-AWS-AUTH-2026-001-b6-5a-ssm-test-registry.json"
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-001-b6-5a-ssm-test-registry.md"
MANIFEST = ROOT / "platform/manifests/B6-5A-SSM-TEST-REGISTRY-2026-001.json"
EXPECTED_PACKET_SHA = "b9c496152dfae322e5d6f74dd5396a1041a3c213f46b8dc0d13c086c48167350"
EXPECTED_MANIFEST_SHA = "75ec85328d1424acc80a0db55d5f407571c3ffcdc0e4f9e8a4ebe8962075edb3"
EXPECTED_ACCOUNT = "558069890522"
EXPECTED_REGION = "eu-central-1"
EXPECTED_OPERATOR = f"arn:aws:iam::{EXPECTED_ACCOUNT}:user/s.fotso"
PUBLISHER_ROLE = f"arn:aws:iam::{EXPECTED_ACCOUNT}:role/medzen-registry-publisher-role"
ORCHESTRATOR_ROLE = f"arn:aws:iam::{EXPECTED_ACCOUNT}:role/medzen-orch-role"
PRODUCTION_POINTER = "/medzen/registry/serving/current"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/speech-orchestrator"))

from scripts.check_b6_5a_ssm_packet import validate as validate_manifest  # noqa: E402
from medzen_speech_orchestrator.registry import (  # noqa: E402
    Parameter,
    RegistryRouter,
)


class PublicationRefusal(RuntimeError):
    """An approved identity, immutable input or AWS result disagreed."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def load_controls() -> tuple[dict[str, Any], dict[str, Any]]:
    if sha256(PACKET.read_bytes()) != EXPECTED_PACKET_SHA:
        raise PublicationRefusal("approved packet hash mismatch")
    if sha256(MANIFEST.read_bytes()) != EXPECTED_MANIFEST_SHA:
        raise PublicationRefusal("approved request-manifest hash mismatch")
    authorization = json.loads(AUTH.read_bytes())
    if authorization.get("status") != "OWNER_APPROVED_FOR_EXECUTION":
        raise PublicationRefusal("owner authorization is absent")
    if authorization.get("packet", {}).get("sha256") != EXPECTED_PACKET_SHA:
        raise PublicationRefusal("authorization packet binding mismatch")
    if authorization.get("request_manifest", {}).get("sha256") != EXPECTED_MANIFEST_SHA:
        raise PublicationRefusal("authorization manifest binding mismatch")
    request = validate_manifest()
    if request["aws"] != {
        "account": EXPECTED_ACCOUNT,
        "region": EXPECTED_REGION,
        "operator_principal_arn": EXPECTED_OPERATOR,
        "publisher_role_arn": PUBLISHER_ROLE,
        "kms_key_arn": authorization["aws_scope"]["kms_key"],
    }:
        raise PublicationRefusal("request AWS identity binding mismatch")
    return authorization, request


def parameter_arn(name: str) -> str:
    return f"arn:aws:ssm:{EXPECTED_REGION}:{EXPECTED_ACCOUNT}:parameter{name}"


def expected_tags(request: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"Key": key, "Value": value}
        for key, value in sorted(request["allocation"]["tags"].items())
    ]


def _get_optional(ssm: Any, name: str) -> dict[str, Any] | None:
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise


def pointer_receipt(ssm: Any) -> dict[str, Any]:
    parameter = _get_optional(ssm, PRODUCTION_POINTER)
    if parameter is None:
        return {"exists": False, "value_sha256": None, "version": None}
    return {
        "exists": True,
        "value_sha256": sha256(parameter["Value"].encode("utf-8")),
        "version": parameter["Version"],
        "type": parameter["Type"],
    }


def _tag_map(ssm: Any, name: str) -> dict[str, str]:
    response = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name)
    return {item["Key"]: item["Value"] for item in response.get("TagList", [])}


def _description(operator_ssm: Any, name: str) -> dict[str, Any]:
    response = operator_ssm.describe_parameters(ParameterFilters=[{
        "Key": "Name", "Option": "Equals", "Values": [name]
    }])
    values = [item for item in response.get("Parameters", []) if item.get("Name") == name]
    if len(values) != 1:
        raise PublicationRefusal(f"parameter description is missing or ambiguous: {name}")
    return values[0]


def inspect_snapshot(
    publisher_ssm: Any,
    operator_ssm: Any,
    request: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    tags = request["allocation"]["tags"]
    receipts: list[dict[str, Any]] = []
    present = 0
    for expected in sorted(request["parameters"], key=lambda item: item["PublishOrder"]):
        parameter = _get_optional(publisher_ssm, expected["Name"])
        if parameter is None:
            receipts.append({"name": expected["Name"], "state": "ABSENT"})
            continue
        present += 1
        description = _description(operator_ssm, expected["Name"])
        actual_tags = _tag_map(publisher_ssm, expected["Name"])
        if (
            parameter.get("Value") != expected["Value"]
            or parameter.get("Type") != "SecureString"
            or parameter.get("Version") != expected["ExpectedInitialVersion"]
            or description.get("KeyId") != expected["KeyId"]
            or description.get("Tier") != "Standard"
            or description.get("DataType") != "text"
            or actual_tags != tags
        ):
            raise PublicationRefusal(f"immutable parameter mismatch: {expected['Name']}")
        receipts.append({
            "name": expected["Name"],
            "state": "PRESENT_IDENTICAL",
            "value_sha256": expected["ValueSHA256"],
            "version": parameter["Version"],
            "type": parameter["Type"],
            "key_id": description["KeyId"],
            "tier": description["Tier"],
            "data_type": description["DataType"],
            "tags": actual_tags,
        })
    if present == 0:
        return "CREATE", receipts
    if present == len(request["parameters"]):
        return "REUSE_IDENTICAL_COMPLETE", receipts
    raise PublicationRefusal("partial test-registry snapshot refuses publication")


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
        return tuple(self.values[name] for name in sorted(self.values) if name.startswith(prefix))


def publish(
    publisher_ssm: Any,
    operator_ssm: Any,
    request: dict[str, Any],
    mode: str,
) -> tuple[str, list[dict[str, Any]]]:
    if mode == "CREATE":
        tags = expected_tags(request)
        for item in sorted(request["parameters"], key=lambda value: value["PublishOrder"]):
            publisher_ssm.put_parameter(
                Name=item["Name"],
                Description="MedZen B6.5A non-serving content-addressed test registry",
                Value=item["Value"],
                Type=item["Type"],
                KeyId=item["KeyId"],
                Overwrite=False,
                Tier=item["Tier"],
                DataType=item["DataType"],
                Tags=tags,
            )
    result, receipts = inspect_snapshot(publisher_ssm, operator_ssm, request)
    expected_result = "REUSE_IDENTICAL_COMPLETE"
    if result != expected_result:
        raise PublicationRefusal("post-publication snapshot is not complete and identical")
    parameters = [
        publisher_ssm.get_parameter(Name=item["Name"], WithDecryption=True)["Parameter"]
        for item in request["parameters"]
    ]
    router = RegistryRouter(ReadbackStore(parameters), request["snapshot"]["root"])
    if router.snapshot_sha256 != request["snapshot"]["snapshot_material_sha256"]:
        raise PublicationRefusal("RegistryRouter snapshot identity mismatch")
    outcome = "PUBLISHED_VERIFIED_NON_SERVING" if mode == "CREATE" else result
    return outcome, receipts


def simulation(iam: Any, request: dict[str, Any]) -> dict[str, Any]:
    resources = [parameter_arn(item["Name"]) for item in request["parameters"]]
    operator = iam.simulate_principal_policy(
        PolicySourceArn=EXPECTED_OPERATOR,
        ActionNames=["ssm:DeleteParameters"],
        ResourceArns=resources,
    )["EvaluationResults"]
    if not operator or any(item["EvalDecision"] != "allowed" for item in operator):
        raise PublicationRefusal("operator lacks exact rollback deletion permission")
    orchestrator = iam.simulate_principal_policy(
        PolicySourceArn=ORCHESTRATOR_ROLE,
        ActionNames=["ssm:GetParameter", "ssm:GetParametersByPath", "ssm:PutParameter"],
        ResourceArns=resources,
    )["EvaluationResults"]
    reads = [item for item in orchestrator if item["EvalActionName"] != "ssm:PutParameter"]
    writes = [item for item in orchestrator if item["EvalActionName"] == "ssm:PutParameter"]
    if not reads or any(item["EvalDecision"] != "allowed" for item in reads):
        raise PublicationRefusal("orchestrator registry read simulation failed")
    if not writes or any(item["EvalDecision"] == "allowed" for item in writes):
        raise PublicationRefusal("orchestrator registry write simulation did not deny")
    return {
        "operator_exact_rollback_delete": "allowed",
        "orchestrator_registry_reads": "allowed",
        "orchestrator_registry_writes": "denied",
    }


def execute(receipt_path: Path) -> dict[str, Any]:
    import boto3

    started = now()
    authorization, request = load_controls()
    session = boto3.Session(profile_name="medzen", region_name=EXPECTED_REGION)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    if identity.get("Account") != EXPECTED_ACCOUNT or identity.get("Arn") != EXPECTED_OPERATOR:
        raise PublicationRefusal("operator account or caller identity mismatch")
    policy_results = simulation(session.client("iam"), request)
    assumed = sts.assume_role(
        RoleArn=PUBLISHER_ROLE,
        RoleSessionName="medzen-b6-5a-2026-001",
        DurationSeconds=3600,
    )["Credentials"]
    publisher_session = boto3.Session(
        aws_access_key_id=assumed["AccessKeyId"],
        aws_secret_access_key=assumed["SecretAccessKey"],
        aws_session_token=assumed["SessionToken"],
        region_name=EXPECTED_REGION,
    )
    publisher_sts = publisher_session.client("sts").get_caller_identity()
    if not publisher_sts.get("Arn", "").startswith(
        f"arn:aws:sts::{EXPECTED_ACCOUNT}:assumed-role/medzen-registry-publisher-role/"
    ):
        raise PublicationRefusal("publisher assumed-role identity mismatch")
    publisher_ssm = publisher_session.client("ssm")
    operator_ssm = session.client("ssm")
    pointer_before = pointer_receipt(publisher_ssm)
    mode, preflight = inspect_snapshot(publisher_ssm, operator_ssm, request)
    outcome, parameter_receipts = publish(publisher_ssm, operator_ssm, request, mode)
    pointer_after = pointer_receipt(publisher_ssm)
    if pointer_before != pointer_after:
        raise PublicationRefusal("production serving pointer changed")
    receipt = {
        "record": "B6_5A_SSM_TEST_REGISTRY_AWS_EXECUTION",
        "id": "B6-SSM-TEST-REGISTRY-2026-001",
        "revision": 1,
        "status": "VERIFIED_COMPLETE",
        "started_utc": started,
        "completed_utc": now(),
        "authorization": {
            "path": str(AUTH.relative_to(ROOT)),
            "sha256": sha256(AUTH.read_bytes()),
            "id": authorization["id"],
        },
        "packet": {"path": str(PACKET.relative_to(ROOT)), "sha256": EXPECTED_PACKET_SHA},
        "request_manifest": {
            "path": str(MANIFEST.relative_to(ROOT)),
            "sha256": EXPECTED_MANIFEST_SHA,
        },
        "aws": {
            "account": identity["Account"],
            "region": EXPECTED_REGION,
            "operator": identity["Arn"],
            "publisher_session_arn": publisher_sts["Arn"],
        },
        "policy_simulation": policy_results,
        "preflight": {"mode": mode, "parameters": preflight},
        "publication": {
            "outcome": outcome,
            "snapshot_sha256": request["snapshot"]["snapshot_material_sha256"],
            "root": request["snapshot"]["root"],
            "parameter_count": len(parameter_receipts),
            "parameters": parameter_receipts,
        },
        "production_pointer": {
            "before": pointer_before,
            "after": pointer_after,
            "changes": 0,
        },
        "cost": {
            "allocation_id": request["allocation"]["allocation_id"],
            "maximum_incremental_cost_usd": request["allocation"]["maximum_incremental_cost_usd"],
            "compute_started": False,
        },
        "explicit_non_events": {
            "parameters_outside_exact_test_root": 0,
            "production_alias_changes": 0,
            "new_iam_roles": 0,
            "new_kms_keys": 0,
            "cpu_or_gpu_scale_changes": 0,
            "deployments": 0,
            "approved_model_changes": 0,
        },
    }
    receipt_path.write_bytes(canonical(receipt))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("REFUSING: --apply is required for the approved AWS execution", file=sys.stderr)
        return 2
    try:
        receipt = execute(args.receipt)
    except (OSError, KeyError, ValueError, ClientError, PublicationRefusal) as exc:
        print(f"REFUSING OR STOPPED B6.5A PUBLICATION: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": receipt["status"],
        "outcome": receipt["publication"]["outcome"],
        "snapshot_sha256": receipt["publication"]["snapshot_sha256"],
        "parameter_count": receipt["publication"]["parameter_count"],
        "production_alias_changes": receipt["production_pointer"]["changes"],
        "receipt": str(args.receipt),
        "receipt_sha256": sha256(args.receipt.read_bytes()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
