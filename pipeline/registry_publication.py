"""Fail-closed publication boundary for the production serving registry.

The immutable snapshot is written first.  A small mutable pointer is activated
only after readback verification and a separate signed-PASS/manual-approval
check.  Parameter Store versions preserve the previous pointer for rollback.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


PRODUCTION_PREFIX = "/medzen/registry"
SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class RegistryPublicationError(RuntimeError):
    """A required publication proof is absent or inconsistent."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(bindings: Mapping[str, Any], field: str) -> str:
    value = bindings.get(field)
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RegistryPublicationError(f"{field} must be an exact SHA-256")
    return value


def _require_production_approval(bindings: Mapping[str, Any]) -> None:
    for field in ("gate_report_sha256", "signed_manifest_sha256",
                  "approval_record_sha256", "registry_source_sha256",
                  "generated_registry_tree_sha256"):
        _require_sha256(bindings, field)
    commit = bindings.get("git_commit")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        raise RegistryPublicationError("git_commit must be an exact 40-character commit")
    if bindings.get("gate_outcome") != "PASS":
        raise RegistryPublicationError("production snapshot requires a PASS gate report")
    if bindings.get("signature_verified") is not True:
        raise RegistryPublicationError("production snapshot requires a verified signature")
    if bindings.get("manual_approval_recorded") is not True:
        raise RegistryPublicationError("production snapshot requires manual approval")
    if not isinstance(bindings.get("approval_identity"), str) or not bindings[
            "approval_identity"].strip():
        raise RegistryPublicationError("approval_identity is required")
    if not isinstance(bindings.get("approval_timestamp_utc"), str) or not bindings[
            "approval_timestamp_utc"].endswith("Z"):
        raise RegistryPublicationError("approval_timestamp_utc must be UTC")


def _validate_alias(alias: str) -> None:
    if re.fullmatch(r"[a-z][a-z0-9-]{1,31}", alias) is None:
        raise RegistryPublicationError(f"invalid registry language alias: {alias!r}")


@dataclass(frozen=True)
class SnapshotPlan:
    prefix: str
    snapshot_sha256: str
    manifest_name: str
    parameters: Mapping[str, str]
    actions: Mapping[str, str]


@dataclass(frozen=True)
class ActivationPlan:
    parameter_name: str
    value: str
    expected_previous_value: str | None


def plan_production_snapshot(
        registry: Mapping[str, Mapping[str, Any]],
        bindings: Mapping[str, Any],
        existing: Mapping[str, str] | None = None) -> SnapshotPlan:
    """Build deterministic create-only parameters for one approved snapshot."""
    _require_production_approval(bindings)
    if not registry:
        raise RegistryPublicationError("registry snapshot cannot be empty")

    language_values: dict[str, str] = {}
    language_hashes: dict[str, str] = {}
    for alias, record in sorted(registry.items()):
        _validate_alias(alias)
        if not isinstance(record, Mapping):
            raise RegistryPublicationError(f"registry record for {alias} is not an object")
        body = canonical_json_bytes(record)
        # Standard SecureString parameters are capped at 4 KiB. Refuse instead
        # of silently changing tiers and cost characteristics.
        if len(body) > 4096:
            raise RegistryPublicationError(
                f"registry record for {alias} exceeds the 4096-byte standard tier")
        language_values[alias] = body.decode("utf-8")
        language_hashes[alias] = _sha256(body)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bindings": dict(bindings),
        "language_value_sha256": language_hashes,
    }
    manifest_body = canonical_json_bytes(manifest)
    if len(manifest_body) > 4096:
        raise RegistryPublicationError("snapshot manifest exceeds the standard tier")
    snapshot_sha = _sha256(manifest_body)
    base = f"{PRODUCTION_PREFIX}/snapshots/{snapshot_sha}"
    parameters = {
        f"{base}/languages/{alias}": value
        for alias, value in language_values.items()
    }
    manifest_name = f"{base}/_manifest"
    parameters[manifest_name] = manifest_body.decode("utf-8")

    existing = existing or {}
    actions: dict[str, str] = {}
    for name, value in sorted(parameters.items()):
        current = existing.get(name)
        if current is None:
            actions[name] = "CREATE"
        elif current == value:
            actions[name] = "REUSE_IDENTICAL"
        else:
            raise RegistryPublicationError(
                f"immutable snapshot collision at {name}")
    return SnapshotPlan(PRODUCTION_PREFIX, snapshot_sha, manifest_name,
                        parameters, actions)


def publish_snapshot(ssm_client: Any, plan: SnapshotPlan, kms_key_arn: str,
                     *, dry_run: bool = True) -> dict[str, Any]:
    """Create and read back a snapshot. This never changes the serving pointer."""
    if plan.prefix != PRODUCTION_PREFIX:
        raise RegistryPublicationError("unexpected production registry prefix")
    if not isinstance(kms_key_arn, str) or not kms_key_arn.startswith("arn:aws:kms:"):
        raise RegistryPublicationError("exact KMS key ARN is required")
    if dry_run:
        return {"mode": "DRY_RUN", "writes_performed": 0,
                "snapshot_sha256": plan.snapshot_sha256,
                "actions": dict(plan.actions)}

    written = 0
    reused = 0
    for name, expected in sorted(plan.parameters.items()):
        if plan.actions[name] == "CREATE":
            ssm_client.put_parameter(
                Name=name, Description="MedZen immutable serving registry snapshot",
                Value=expected, Type="SecureString", KeyId=kms_key_arn,
                Overwrite=False, Tier="Standard", DataType="text")
            written += 1
        else:
            reused += 1
        actual = ssm_client.get_parameter(Name=name, WithDecryption=True)[
            "Parameter"]["Value"]
        if actual != expected:
            raise RegistryPublicationError(f"Parameter Store readback mismatch at {name}")
    return {"mode": "APPLY", "writes_performed": written,
            "identical_parameters_reused": reused,
            "snapshot_sha256": plan.snapshot_sha256,
            "serving_pointer_changes": 0}


def plan_activation(plan: SnapshotPlan, bindings: Mapping[str, Any],
                    current_pointer: str | None) -> ActivationPlan:
    """Plan the separately approved, mutable serving-pointer change."""
    _require_production_approval(bindings)
    if plan.prefix != PRODUCTION_PREFIX:
        raise RegistryPublicationError("unexpected production registry prefix")
    if not plan.manifest_name.endswith(f"/{plan.snapshot_sha256}/_manifest"):
        raise RegistryPublicationError("snapshot manifest does not match snapshot hash")
    pointer = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_sha256": plan.snapshot_sha256,
        "manifest_parameter": plan.manifest_name,
        "approval_record_sha256": bindings["approval_record_sha256"],
        "activated_by": bindings["approval_identity"],
        "activated_at_utc": bindings["approval_timestamp_utc"],
    }
    return ActivationPlan(f"{PRODUCTION_PREFIX}/serving/current",
                          canonical_json_bytes(pointer).decode("utf-8"),
                          current_pointer)


def activate_snapshot(ssm_client: Any, activation: ActivationPlan,
                      kms_key_arn: str, *, dry_run: bool = True) -> dict[str, Any]:
    """Activate after a last current-value check; return rollback information."""
    if activation.parameter_name != f"{PRODUCTION_PREFIX}/serving/current":
        raise RegistryPublicationError("activation target is not the serving pointer")
    if not isinstance(kms_key_arn, str) or not kms_key_arn.startswith("arn:aws:kms:"):
        raise RegistryPublicationError("exact KMS key ARN is required")
    if dry_run:
        return {"mode": "DRY_RUN", "writes_performed": 0,
                "production_namespace_changes": 0,
                "rollback_value": activation.expected_previous_value}

    try:
        observed = ssm_client.get_parameter(
            Name=activation.parameter_name, WithDecryption=True)["Parameter"]["Value"]
    except ssm_client.exceptions.ParameterNotFound:
        observed = None
    if observed != activation.expected_previous_value:
        raise RegistryPublicationError("serving pointer changed after approval")
    response = ssm_client.put_parameter(
        Name=activation.parameter_name,
        Description="MedZen active serving registry snapshot",
        Value=activation.value, Type="SecureString", KeyId=kms_key_arn,
        Overwrite=True, Tier="Standard", DataType="text")
    readback = ssm_client.get_parameter(
        Name=activation.parameter_name, WithDecryption=True)["Parameter"]["Value"]
    if readback != activation.value:
        raise RegistryPublicationError("serving pointer readback mismatch")
    return {"mode": "APPLY", "writes_performed": 1,
            "production_namespace_changes": 1,
            "parameter_version": response.get("Version"),
            "rollback_value": activation.expected_previous_value}
