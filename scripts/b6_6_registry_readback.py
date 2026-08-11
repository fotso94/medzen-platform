#!/usr/bin/env python3
"""Verify the existing B6 deployment registry exactly, without writing AWS state."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "platform/generated/registry-ssm/b6-v0-synthetic.json"
REGISTRY_ROOT = (
    "/medzen/registry/test/b6/"
    "d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81"
)
SERVING_ROOT = "/medzen/registry/serving"
PRODUCTION_POINTER = f"{SERVING_ROOT}/current"
EXPECTED_ACCOUNT = "558069890522"
EXPECTED_OPERATOR = f"arn:aws:iam::{EXPECTED_ACCOUNT}:user/s.fotso"
EXPECTED_REGION = "eu-central-1"
RAG_INDEX_SHA256 = "6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160"

sys.path.insert(0, str(ROOT / "services/speech-orchestrator"))
from medzen_speech_orchestrator.registry import (  # noqa: E402
    DEPLOYED_CLASSIFICATION,
    Parameter,
    RegistryRouter,
)


class RegistryReadbackRefusal(RuntimeError):
    """Live registry state is absent, ambiguous, stale or serving-visible."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _all_parameters(client: Any, path: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        request: dict[str, Any] = {
            "Path": path,
            "Recursive": True,
            "WithDecryption": True,
            "MaxResults": 10,
        }
        if token is not None:
            request["NextToken"] = token
        response = client.get_parameters_by_path(**request)
        raw = response.get("Parameters", [])
        if not isinstance(raw, list):
            raise RegistryReadbackRefusal("SSM_PARAMETER_LIST_MALFORMED")
        values.extend(raw)
        if len(values) > 32:
            raise RegistryReadbackRefusal("SSM_PARAMETER_LIST_UNBOUNDED")
        token = response.get("NextToken")
        if token is None:
            return values
        if not isinstance(token, str) or not token:
            raise RegistryReadbackRefusal("SSM_PAGINATION_TOKEN_MALFORMED")


class _ReadbackStore:
    def __init__(self, parameters: list[dict[str, Any]]):
        self.values = {
            item["Name"]: Parameter(
                Name=item["Name"],
                Type=item["Type"],
                Value=item["Value"],
                Version=item["Version"],
            )
            for item in parameters
        }

    def get_parameter(self, name: str) -> Parameter:
        try:
            return self.values[name]
        except KeyError as exc:
            raise RegistryReadbackRefusal("SSM_REQUIRED_PARAMETER_MISSING") from exc

    def get_parameters_by_path(self, path: str) -> tuple[Parameter, ...]:
        prefix = path.rstrip("/") + "/"
        return tuple(
            self.values[name] for name in sorted(self.values) if name.startswith(prefix)
        )


def verify(ssm: Any) -> dict[str, Any]:
    expected_fixture = json.loads(FIXTURE.read_bytes())
    expected = {
        item["Name"]: {
            key: item[key] for key in ("Name", "Type", "Value", "Version")
        }
        for item in expected_fixture["parameters"]
    }
    observed_raw = _all_parameters(ssm, REGISTRY_ROOT)
    observed: dict[str, dict[str, Any]] = {}
    for item in observed_raw:
        try:
            minimal = {
                key: item[key] for key in ("Name", "Type", "Value", "Version")
            }
        except (KeyError, TypeError) as exc:
            raise RegistryReadbackRefusal("SSM_PARAMETER_IDENTITY_MALFORMED") from exc
        if minimal["Name"] in observed:
            raise RegistryReadbackRefusal("SSM_PARAMETER_DUPLICATE")
        observed[minimal["Name"]] = minimal
    if observed != expected:
        raise RegistryReadbackRefusal("SSM_CONTENT_ADDRESSED_SNAPSHOT_DIFFERS")

    serving = _all_parameters(ssm, SERVING_ROOT)
    if any(item.get("Name") == PRODUCTION_POINTER for item in serving):
        raise RegistryReadbackRefusal("PRODUCTION_SERVING_POINTER_PRESENT")
    if serving:
        raise RegistryReadbackRefusal("PRODUCTION_SERVING_PATH_NOT_EMPTY")

    router = RegistryRouter(
        _ReadbackStore(observed_raw),
        REGISTRY_ROOT,
        expected_classification=DEPLOYED_CLASSIFICATION,
    )
    route = router.resolve("en")
    if route.rag_snapshot_sha256 != RAG_INDEX_SHA256:
        raise RegistryReadbackRefusal("LIVE_RAG_INDEX_IDENTITY_DIFFERS")
    parameter_hashes = {
        name: _sha256(value["Value"].encode("utf-8"))
        for name, value in sorted(observed.items())
    }
    return {
        "status": "PASS_REUSE_IDENTICAL_COMPLETE",
        "publication_mode": "READ_ONLY_REUSE_NO_WRITE",
        "registry_root": REGISTRY_ROOT,
        "registry_snapshot_sha256": router.snapshot_sha256,
        "rag_index_sha256": route.rag_snapshot_sha256,
        "parameter_count": len(observed),
        "parameter_value_sha256": parameter_hashes,
        "production_serving_pointer_present": False,
        "aws_read_calls": 2,
        "aws_write_calls": 0,
        "parameters_created": 0,
        "parameters_changed": 0,
        "parameters_deleted": 0,
    }


def execute(profile: str) -> dict[str, Any]:
    import boto3

    session = boto3.Session(profile_name=profile, region_name=EXPECTED_REGION)
    identity = session.client("sts").get_caller_identity()
    if (
        identity.get("Account") != EXPECTED_ACCOUNT
        or identity.get("Arn") != EXPECTED_OPERATOR
    ):
        raise RegistryReadbackRefusal("AWS_OPERATOR_IDENTITY_DIFFERS")
    result = verify(session.client("ssm"))
    return {
        **result,
        "aws_account": identity["Account"],
        "aws_operator": identity["Arn"],
        "aws_region": EXPECTED_REGION,
        "aws_read_calls": result["aws_read_calls"] + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="medzen")
    args = parser.parse_args()
    try:
        result = execute(args.profile)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RegistryReadbackRefusal) else type(exc).__name__
        print(json.dumps({"status": "REFUSED", "reason_code": reason}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
