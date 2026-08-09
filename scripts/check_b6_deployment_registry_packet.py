#!/usr/bin/env python3
"""Fail-closed validator for B6.5C deployment-registry publication."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "platform/manifests/B6-DEPLOYMENT-REGISTRY-2026-001.json"
EXPECTED_ROOT = (
    "/medzen/registry/test/b6/"
    "d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81"
)
EXPECTED_TAGS = {
    "BudgetRegistry": "COST-REGISTRY-2026-003",
    "CostCenter": "speech-platform",
    "Environment": "dev",
    "Project": "medzen-speech",
    "Stage": "B6.5C",
    "Workstream": "ssm-deployment-registry",
}
KMS_ARN = (
    "arn:aws:kms:eu-central-1:558069890522:"
    "key/9c336116-c648-4548-95c6-1b926478ae57"
)


class PacketRefusal(RuntimeError):
    """The requested write set is incomplete, ambiguous or unsafe."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def validate() -> dict[str, Any]:
    try:
        request = json.loads(MANIFEST.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PacketRefusal("publication manifest is missing or malformed") from exc
    if request.get("status") != "PROPOSED_NOT_AUTHORIZED":
        raise PacketRefusal("request manifest must remain proposed")

    snapshot = request.get("snapshot", {})
    if snapshot.get("root") != EXPECTED_ROOT:
        raise PacketRefusal("snapshot root differs from the reviewed source")
    if snapshot.get("snapshot_material_sha256") != EXPECTED_ROOT.rsplit("/", 1)[1]:
        raise PacketRefusal("snapshot material hash and path disagree")
    if snapshot.get("parameter_count") != 3 or snapshot.get("maximum_parameter_count") != 3:
        raise PacketRefusal("exactly three parameters are required")
    for field in ("source", "generated", "generator"):
        path = ROOT / snapshot[f"{field}_path"]
        hash_field = f"{field}_file_sha256" if field != "generator" else "generator_sha256"
        if sha256(path.read_bytes()) != snapshot[hash_field]:
            raise PacketRefusal(f"{field} binding mismatch")

    generated = json.loads((ROOT / snapshot["generated_path"]).read_bytes())
    generated_by_name = {item["Name"]: item for item in generated["parameters"]}
    parameters = request.get("parameters")
    if not isinstance(parameters, list) or len(parameters) != 3:
        raise PacketRefusal("parameter request must contain exactly three entries")
    requested_by_name = {item.get("Name"): item for item in parameters}
    if len(requested_by_name) != 3 or set(requested_by_name) != set(generated_by_name):
        raise PacketRefusal("parameter names differ from generated snapshot")

    orders: set[int] = set()
    for name, item in requested_by_name.items():
        expected = generated_by_name[name]
        if not name.startswith(EXPECTED_ROOT + "/"):
            raise PacketRefusal("parameter escapes exact content-addressed root")
        if name == "/medzen/registry/serving/current" or "/snapshots/" in name:
            raise PacketRefusal("serving registry path is prohibited")
        if item.get("Type") != "SecureString" or item.get("KeyId") != KMS_ARN:
            raise PacketRefusal("KMS SecureString binding changed")
        if item.get("Overwrite") is not False or item.get("ExpectedInitialVersion") != 1:
            raise PacketRefusal("publication must be create-only at version one")
        if item.get("Tier") != "Standard" or item.get("DataType") != "text":
            raise PacketRefusal("parameter metadata changed")
        if item.get("Value") != expected["Value"]:
            raise PacketRefusal("requested value differs from generated snapshot")
        if sha256(item["Value"].encode("utf-8")) != item.get("ValueSHA256"):
            raise PacketRefusal("parameter value hash mismatch")
        if canonical(json.loads(item["Value"])).decode("utf-8") != item["Value"]:
            raise PacketRefusal("parameter value is not canonical JSON")
        if len(item["Value"].encode("utf-8")) > 4096:
            raise PacketRefusal("parameter exceeds Standard tier limit")
        orders.add(item.get("PublishOrder"))
    if orders != {1, 2, 3}:
        raise PacketRefusal("publish order must be exactly one through three")
    if requested_by_name[f"{EXPECTED_ROOT}/_manifest"]["PublishOrder"] != 3:
        raise PacketRefusal("completion manifest must be last")

    if request.get("allocation", {}).get("tags") != EXPECTED_TAGS:
        raise PacketRefusal("allocation tags changed")
    if request["allocation"].get("maximum_incremental_cost_usd") != 0.1:
        raise PacketRefusal("registry packet cost ceiling changed")
    repository = request.get("repository_binding", {})
    commit = repository.get("preparation_authorization_commit", "")
    tree = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=ROOT,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if tree != repository.get("preparation_authorization_tree"):
        raise PacketRefusal("preparation commit/tree binding mismatch")
    wire = "\n".join(item["Name"] + "\n" + item["Value"] for item in parameters)
    for forbidden in ("approved_version", '"artifact"', "/serving/current"):
        if forbidden in wire:
            raise PacketRefusal("publication contains a prohibited serving field")
    return request


def main() -> int:
    request = validate()
    print(
        "PASS_B6_DEPLOYMENT_REGISTRY_PACKET "
        f"parameters={len(request['parameters'])} "
        f"snapshot={request['snapshot']['snapshot_material_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
