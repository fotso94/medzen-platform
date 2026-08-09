#!/usr/bin/env python3
"""Fail-closed local validator for the proposed B6.5A SSM write set."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "platform/manifests/B6-5A-SSM-TEST-REGISTRY-2026-001.json"
EXPECTED_ROOT = (
    "/medzen/registry/test/b6/"
    "a2486c03eb20b6fd3d30b5ea38eb4d29895c2e1ab26073d21282a9bbedacb8e6"
)
EXPECTED_TAGS = {
    "BudgetRegistry": "COST-REGISTRY-2026-001",
    "CostCenter": "speech-platform",
    "Environment": "dev",
    "Project": "medzen-speech",
    "Stage": "B6.5A",
    "Workstream": "ssm-test-registry",
}
KMS_ARN = (
    "arn:aws:kms:eu-central-1:558069890522:"
    "key/9c336116-c648-4548-95c6-1b926478ae57"
)


class PacketRefusal(RuntimeError):
    """The proposed write set is incomplete, ambiguous or unsafe."""


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
        raise PacketRefusal("packet manifest must remain unapproved before owner review")

    snapshot = request.get("snapshot", {})
    if snapshot.get("root") != EXPECTED_ROOT:
        raise PacketRefusal("snapshot root differs from the reviewed local fixture")
    if snapshot.get("maximum_parameter_count") != 3:
        raise PacketRefusal("maximum parameter count must remain exactly three")
    if snapshot.get("parameter_count") != 3:
        raise PacketRefusal("parameter count must remain exactly three")

    for field in ("source", "fixture", "generator"):
        path = ROOT / snapshot[f"{field}_path"]
        if sha256(path.read_bytes()) != snapshot[f"{field}_file_sha256" if field != "generator" else "generator_sha256"]:
            raise PacketRefusal(f"{field} source binding does not match repository bytes")

    fixture = json.loads((ROOT / snapshot["fixture_path"]).read_bytes())
    parameters = request.get("parameters")
    if not isinstance(parameters, list) or len(parameters) != 3:
        raise PacketRefusal("exactly three parameter requests are required")
    fixture_by_name = {item["Name"]: item for item in fixture["parameters"]}
    request_by_name = {item.get("Name"): item for item in parameters}
    if len(request_by_name) != 3 or set(request_by_name) != set(fixture_by_name):
        raise PacketRefusal("parameter names differ from the reviewed fixture")

    orders: set[int] = set()
    for name, item in request_by_name.items():
        expected = fixture_by_name[name]
        if not name.startswith(EXPECTED_ROOT + "/"):
            raise PacketRefusal("parameter escapes the exact versioned test root")
        if name == "/medzen/registry/serving/current" or "/snapshots/" in name:
            raise PacketRefusal("production registry path is prohibited")
        if item.get("Type") != "SecureString" or item.get("KeyId") != KMS_ARN:
            raise PacketRefusal("every parameter must use the exact KMS SecureString binding")
        if item.get("Tier") != "Standard" or item.get("DataType") != "text":
            raise PacketRefusal("parameter tier or data type changed")
        if item.get("Overwrite") is not False:
            raise PacketRefusal("overwrite must remain false")
        if item.get("ExpectedInitialVersion") != 1:
            raise PacketRefusal("new parameters must expect initial version one")
        if item.get("Value") != expected["Value"]:
            raise PacketRefusal("parameter value differs from the reviewed fixture")
        if sha256(item["Value"].encode("utf-8")) != item.get("ValueSHA256"):
            raise PacketRefusal("parameter value hash mismatch")
        decoded = json.loads(item["Value"])
        if canonical(decoded).decode("utf-8") != item["Value"]:
            raise PacketRefusal("parameter value is not canonical JSON")
        if len(item["Value"].encode("utf-8")) > 4096:
            raise PacketRefusal("parameter exceeds the Standard tier limit")
        orders.add(item.get("PublishOrder"))
    if orders != {1, 2, 3}:
        raise PacketRefusal("publish order must contain exactly stages one through three")
    manifest_item = next(item for item in parameters if item["Name"].endswith("/_manifest"))
    if manifest_item["PublishOrder"] != 3:
        raise PacketRefusal("completion manifest must be published last")

    allocation = request.get("allocation", {})
    if allocation.get("allocation_id") != "B6-SSM-TEST-REGISTRY":
        raise PacketRefusal("cost allocation identity changed")
    if allocation.get("tags") != EXPECTED_TAGS:
        raise PacketRefusal("allocation tags are incomplete or changed")
    if allocation.get("maximum_incremental_cost_usd") != 0.10:
        raise PacketRefusal("packet cost ceiling changed")

    repository = request.get("repository_binding", {})
    commit = repository.get("starting_commit", "")
    tree = repository.get("starting_tree", "")
    actual_tree = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{tree}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual_tree != tree:
        raise PacketRefusal("starting Git commit/tree binding is invalid")

    forbidden = ("approved_version", '"artifact"', "/serving/current")
    parameter_wire = "\n".join(
        item["Name"] + "\n" + item["Value"] for item in parameters
    )
    if any(value in parameter_wire for value in forbidden):
        raise PacketRefusal("parameter wire data contains a prohibited serving field")
    return request


def main() -> int:
    request = validate()
    print(
        "PASS_B6_5A_PACKET_LOCAL_VALIDATION "
        f"parameters={len(request['parameters'])} "
        f"snapshot={request['snapshot']['snapshot_material_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
