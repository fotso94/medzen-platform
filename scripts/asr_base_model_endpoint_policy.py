#!/usr/bin/env python3
"""Derive private-endpoint policies from the exact offline-pilot call inventory.

The inventory is data, not an independently maintained IAM action list.  S3
authorization is derived from the request parameters, including whether an
explicit VersionId is present.  This prevents an executor call from silently
outgrowing the private-endpoint policy that contains it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any


PILOT_BUCKET = "medzen-speech"
PILOT_PREFIX_TEMPLATE = "research/asr-base-model/pilot/{bundle_sha256}/"
WHISPER_PREFIX = (
    "b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/"
)
STARPORT_RESOURCE = "arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*"

ECR_PULL_OPERATIONS = (
    "BatchCheckLayerAvailability",
    "BatchGetImage",
    "GetDownloadUrlForLayer",
)
ECR_PULL_ACTIONS = {
    "BatchCheckLayerAvailability": "ecr:BatchCheckLayerAvailability",
    "BatchGetImage": "ecr:BatchGetImage",
    "GetDownloadUrlForLayer": "ecr:GetDownloadUrlForLayer",
}


class EndpointPolicyRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise EndpointPolicyRefusal(
            "ENDPOINT_INVENTORY_IDENTITY_MALFORMED", f"{name} is not a SHA-256"
        )
    return value


def _s3_action(parameters: dict[str, Any]) -> str:
    if parameters.get("operation") != "GetObject":
        raise EndpointPolicyRefusal(
            "ENDPOINT_S3_OPERATION_UNMAPPED",
            f"S3 operation is not mapped: {parameters.get('operation')!r}",
        )
    version_present = parameters.get("version_id_present")
    if not isinstance(version_present, bool):
        raise EndpointPolicyRefusal(
            "ENDPOINT_S3_VERSION_FLAG_ABSENT",
            "S3 GetObject inventory row must state version_id_present",
        )
    return "s3:GetObjectVersion" if version_present else "s3:GetObject"


def build_call_inventory(
    *,
    bundle_sha256: str,
    pilot_bundle: dict[str, Any],
    model_bindings: dict[str, Any],
    account: str,
    region: str,
    ecr_repositories: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    """Build the exact private-path call inventory from verified inputs."""

    bundle_sha256 = _require_sha256(bundle_sha256, "bundle_sha256")
    prefix = PILOT_PREFIX_TEMPLATE.format(bundle_sha256=bundle_sha256)
    objects = pilot_bundle.get("objects")
    if not isinstance(objects, list):
        raise EndpointPolicyRefusal(
            "ENDPOINT_PILOT_OBJECTS_ABSENT", "verified pilot bundle has no objects"
        )

    calls: list[dict[str, Any]] = []
    for item in objects:
        key = item.get("key") if isinstance(item, dict) else None
        version_id = item.get("version_id") if isinstance(item, dict) else None
        if not isinstance(key, str) or not key.startswith(prefix):
            raise EndpointPolicyRefusal(
                "ENDPOINT_PILOT_KEY_OUTSIDE_PREFIX",
                "pilot object key is outside the exact content-addressed prefix",
            )
        if not (
            key.endswith(("runtime-rows.json", "model-bindings.json"))
            or "/bundles/" in key
        ):
            continue
        if not isinstance(version_id, str) or not version_id:
            raise EndpointPolicyRefusal(
                "ENDPOINT_PILOT_VERSION_ABSENT",
                "every pilot staging object must bind a non-empty VersionId",
            )
        parameters = {
            "bucket": PILOT_BUCKET,
            "key": key,
            "operation": "GetObject",
            "version_id_present": True,
            "version_id_sha256": hashlib.sha256(version_id.encode()).hexdigest(),
        }
        calls.append(
            {
                "service": "s3",
                "execution_path": "gpu_node_presigned_download",
                "parameters": parameters,
                "required_action": _s3_action(parameters),
                "resource": f"arn:aws:s3:::{PILOT_BUCKET}/{key}",
            }
        )

    whisper_files = model_bindings.get("whisper_files")
    if not isinstance(whisper_files, dict) or not whisper_files:
        raise EndpointPolicyRefusal(
            "ENDPOINT_WHISPER_FILES_ABSENT", "Whisper staging inventory is absent"
        )
    for relative in sorted(whisper_files):
        if (
            not isinstance(relative, str)
            or relative.startswith("/")
            or ".." in relative.split("/")
        ):
            raise EndpointPolicyRefusal(
                "ENDPOINT_WHISPER_PATH_MALFORMED", "Whisper relative path is unsafe"
            )
        key = WHISPER_PREFIX + relative
        parameters = {
            "bucket": PILOT_BUCKET,
            "key": key,
            "operation": "GetObject",
            "version_id_present": False,
        }
        calls.append(
            {
                "service": "s3",
                "execution_path": "gpu_node_presigned_download",
                "parameters": parameters,
                "required_action": _s3_action(parameters),
                "resource": f"arn:aws:s3:::{PILOT_BUCKET}/{key}",
            }
        )

    repositories = sorted(set(ecr_repositories))
    if not repositories:
        raise EndpointPolicyRefusal(
            "ENDPOINT_ECR_REPOSITORIES_ABSENT", "private image-pull inventory is absent"
        )
    calls.append(
        {
            "service": "ecr",
            "execution_path": "gpu_node_image_pull",
            "parameters": {"operation": "GetAuthorizationToken", "repository": None},
            "required_action": "ecr:GetAuthorizationToken",
            "resource": "*",
        }
    )
    for repository_account, repository in repositories:
        if not re.fullmatch(r"[0-9]{12}", repository_account) or not repository:
            raise EndpointPolicyRefusal(
                "ENDPOINT_ECR_REPOSITORY_MALFORMED", "ECR repository identity is malformed"
            )
        resource = (
            f"arn:aws:ecr:{region}:{repository_account}:repository/{repository}"
        )
        for operation in ECR_PULL_OPERATIONS:
            calls.append(
                {
                    "service": "ecr",
                    "execution_path": "gpu_node_image_pull",
                    "parameters": {
                        "operation": operation,
                        "repository": repository,
                        "repository_account": repository_account,
                    },
                    "required_action": ECR_PULL_ACTIONS[operation],
                    "resource": resource,
                }
            )

    calls.sort(
        key=lambda row: (
            row["service"],
            row["required_action"],
            row["resource"],
            json.dumps(row["parameters"], sort_keys=True),
        )
    )
    inventory = {
        "schema_version": 1,
        "status": "PASS_MACHINE_DERIVED_ENDPOINT_CALL_INVENTORY",
        "account": account,
        "region": region,
        "pilot_prefix": prefix,
        "whisper_prefix": WHISPER_PREFIX,
        "calls": calls,
    }
    inventory["inventory_sha256"] = hashlib.sha256(_canonical(inventory)).hexdigest()
    return inventory


def derive_policy(inventory: dict[str, Any], service: str) -> dict[str, Any]:
    """Derive the endpoint policy; no independent action list is accepted."""

    calls = inventory.get("calls")
    if service not in {"s3", "ecr"} or not isinstance(calls, list):
        raise EndpointPolicyRefusal(
            "ENDPOINT_INVENTORY_MALFORMED", "endpoint inventory or service is malformed"
        )
    selected = [row for row in calls if row.get("service") == service]
    if not selected:
        raise EndpointPolicyRefusal(
            "ENDPOINT_SERVICE_CALLS_ABSENT", f"no calls recorded for {service}"
        )
    if service == "s3":
        pilot_resource = (
            f"arn:aws:s3:::{PILOT_BUCKET}/{inventory.get('pilot_prefix', '')}*"
        )
        whisper_resource = (
            f"arn:aws:s3:::{PILOT_BUCKET}/{inventory.get('whisper_prefix', '')}*"
        )
        grouped: dict[str, set[str]] = {}
        for row in selected:
            derived = _s3_action(row.get("parameters", {}))
            if row.get("required_action") != derived:
                raise EndpointPolicyRefusal(
                    "ENDPOINT_RECORDED_ACTION_DIFFERS",
                    "recorded S3 action differs from request-parameter derivation",
                )
            if row["resource"].startswith(pilot_resource[:-1]):
                scoped_resource = pilot_resource
            elif row["resource"].startswith(whisper_resource[:-1]):
                scoped_resource = whisper_resource
            else:
                raise EndpointPolicyRefusal(
                    "ENDPOINT_S3_RESOURCE_OUTSIDE_BOUND_PREFIXES",
                    "S3 inventory resource is outside both exact bound prefixes",
                )
            grouped.setdefault(derived, set()).add(scoped_resource)
        statements = [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": [action],
                "Resource": sorted(resources),
            }
            for action, resources in sorted(grouped.items())
        ]
        # ECR redirects layer payload reads to this AWS-owned bucket.  It is
        # unversioned and therefore deliberately has GetObject only.
        statements.append(
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["s3:GetObject"],
                "Resource": [STARPORT_RESOURCE],
            }
        )
    else:
        token = [row for row in selected if row["required_action"] == "ecr:GetAuthorizationToken"]
        repository_calls = [row for row in selected if row not in token]
        if len(token) != 1 or token[0]["resource"] != "*":
            raise EndpointPolicyRefusal(
                "ENDPOINT_ECR_TOKEN_INVENTORY_DIFFERS",
                "ECR token inventory must contain exactly one global call",
            )
        statements = [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": sorted({row["required_action"] for row in repository_calls}),
                "Resource": sorted({row["resource"] for row in repository_calls}),
            },
        ]
    policy = {"Version": "2012-10-17", "Statement": statements}
    validate_policy_coverage(inventory, policy, service)
    return policy


def validate_policy_coverage(
    inventory: dict[str, Any], policy: dict[str, Any], service: str
) -> dict[str, Any]:
    statements = policy.get("Statement")
    if not isinstance(statements, list):
        raise EndpointPolicyRefusal(
            "ENDPOINT_POLICY_STATEMENTS_ABSENT", "endpoint policy statements are absent"
        )
    pairs: set[tuple[str, str]] = set()
    for statement in statements:
        actions = statement.get("Action", [])
        resources = statement.get("Resource", [])
        if isinstance(actions, str):
            actions = [actions]
        if isinstance(resources, str):
            resources = [resources]
        pairs.update((action, resource) for action in actions for resource in resources)
    selected = [row for row in inventory.get("calls", []) if row.get("service") == service]
    def covered(action: str, resource: str) -> bool:
        return any(
            actual_action == action
            and (
                actual_resource == resource
                or (
                    actual_resource.endswith("*")
                    and resource.startswith(actual_resource[:-1])
                )
            )
            for actual_action, actual_resource in pairs
        )

    missing = sorted(
        {
            (row["required_action"], row["resource"])
            for row in selected
            if not covered(row["required_action"], row["resource"])
        }
    )
    if missing:
        raise EndpointPolicyRefusal(
            "ENDPOINT_POLICY_CALL_UNCOVERED",
            "endpoint policy does not cover every recorded call: "
            + ",".join(f"{action}@{resource}" for action, resource in missing),
        )
    versioned = [
        row for row in selected
        if row.get("parameters", {}).get("version_id_present") is True
    ]
    version_actions = sorted({row["required_action"] for row in versioned})
    if service == "s3" and version_actions not in ([], ["s3:GetObjectVersion"]):
        raise EndpointPolicyRefusal(
            "ENDPOINT_VERSION_ACTION_AMBIGUOUS",
            "versioned S3 inventory requires an unexpected action variant",
        )
    return {
        "status": "PASS_ENDPOINT_POLICY_COVERS_RECORDED_CALLS",
        "service": service,
        "recorded_call_count": len(selected),
        "covered_call_count": len(selected),
        "required_actions": sorted({row["required_action"] for row in selected}),
        "versioned_call_count": len(versioned),
        "version_variant_actions": version_actions,
        "uncovered_calls": 0,
    }


def validate_observed_s3_calls(
    inventory: dict[str, Any], observed: list[dict[str, Any]]
) -> dict[str, Any]:
    """Cross-check the exact requests emitted by node staging before SSM."""

    expected = {
        (
            row["parameters"]["bucket"],
            row["parameters"]["key"],
            row["parameters"]["version_id_present"],
        )
        for row in inventory.get("calls", [])
        if row.get("service") == "s3"
    }
    actual = set()
    for call in observed:
        if call.get("operation") != "GetObject":
            raise EndpointPolicyRefusal(
                "ENDPOINT_OBSERVED_S3_OPERATION_UNMAPPED",
                "node staging emitted an unmapped S3 operation",
            )
        actual.add(
            (
                call.get("bucket"),
                call.get("key"),
                bool(call.get("version_id_present")),
            )
        )
    if actual != expected or len(observed) != len(expected):
        raise EndpointPolicyRefusal(
            "ENDPOINT_OBSERVED_S3_CALLS_DIFFER",
            "node staging S3 requests differ from the policy-generating inventory",
        )
    return {
        "status": "PASS_OBSERVED_S3_CALLS_MATCH_POLICY_INVENTORY",
        "observed_call_count": len(observed),
        "unique_call_count": len(actual),
        "versioned_call_count": sum(bool(row[2]) for row in actual),
        "unversioned_call_count": sum(not bool(row[2]) for row in actual),
    }
