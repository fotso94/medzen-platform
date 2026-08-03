"""Dry-run B5 promotion boundary, manifest signing and external-state guards."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.b5_gates import FailClosedError, GateState, parse_state, sha256_file

SCHEMA_VERSION = "medzen-signed-promotion-manifest-v1"
CANONICALIZATION = "medzen-canonical-json-v1"
SIGNING_ALGORITHM = "RSASSA_PSS_SHA_256"
MESSAGE_TYPE = "DIGEST"

REQUIRED_BINDINGS = (
    "artifact.tree_sha256",
    "artifact.base_model_revision",
    "artifact.tokenizer_revision",
    "artifact.processor_revision",
    "artifact.precision",
    "artifact.selected_checkpoint",
    "artifact.adapter_sha256",
    "artifact.adapter_tree_sha256",
    "dataset.fingerprint",
    "dataset.adoption_record.path",
    "dataset.adoption_record.sha256",
    "evaluations.frozen_manifests",
    "evaluations.gate_report.path",
    "evaluations.gate_report.sha256",
    "evaluations.lingala_holdout.path",
    "evaluations.lingala_holdout.sha256",
    "provenance.git_commit",
    "provenance.bundle_sha256",
    "provenance.container_image_digest",
    "provenance.spot_resume_evidence.path",
    "provenance.spot_resume_evidence.sha256",
    "decode.configuration",
    "scope.deviations",
    "mlflow.run_id",
    "approval.identity",
    "approval.timestamp_utc",
)


def _path_value(doc: dict[str, Any], dotted: str) -> Any:
    value: Any = doc
    for key in dotted.split("."):
        if not isinstance(value, dict) or key not in value:
            raise FailClosedError(f"manifest missing binding {dotted}")
        value = value[key]
    if value in (None, "", [], {}):
        raise FailClosedError(f"manifest binding {dotted} is empty")
    return value


def _validate_json_domain(value: Any, path: str = "manifest") -> None:
    """Canonical v1 excludes floats so language/runtime encoders cannot drift."""
    if value is None or isinstance(value, float):
        raise FailClosedError(f"{path}: null and floating-point values are forbidden")
    if isinstance(value, (str, int, bool)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_domain(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise FailClosedError(f"{path}: object keys must be strings")
        for key in sorted(value):
            _validate_json_domain(value[key], f"{path}.{key}")
        return
    raise FailClosedError(f"{path}: unsupported canonical value {type(value).__name__}")


def validate_manifest(manifest: dict[str, Any], *, require_pass: bool = True) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise FailClosedError("unknown signed-manifest schema version")
    if manifest.get("canonicalization") != CANONICALIZATION:
        raise FailClosedError("unknown manifest canonicalization")
    for binding in REQUIRED_BINDINGS:
        _path_value(manifest, binding)
    if require_pass and manifest.get("decision", {}).get("outcome") != "PASS":
        raise FailClosedError("promotion manifest must bind a signed PASS decision")
    if manifest.get("artifact", {}).get("destination_prefix", "").startswith(
            "s3://medzen-speech/approved/asr/") is False:
        raise FailClosedError("promotion destination must be under approved/asr/")
    frozen = manifest["evaluations"]["frozen_manifests"]
    if not isinstance(frozen, list) or not frozen:
        raise FailClosedError("at least one frozen evaluation manifest is required")
    for index, row in enumerate(frozen):
        if not isinstance(row, dict) or not row.get("path") or not row.get("sha256"):
            raise FailClosedError(f"frozen evaluation binding {index} is incomplete")
    _validate_json_domain(manifest)


def canonical_manifest_bytes(manifest: dict[str, Any], *, require_pass: bool = True) -> bytes:
    validate_manifest(manifest, require_pass=require_pass)
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def manifest_digest(manifest: dict[str, Any], *, require_pass: bool = True) -> bytes:
    return hashlib.sha256(canonical_manifest_bytes(
        manifest, require_pass=require_pass)).digest()


def sign_manifest(kms_client: Any, manifest: dict[str, Any], key_arn: str) -> dict:
    """Call real KMS Sign; callers provide either boto3 KMS or a strict test fake."""
    if not key_arn.startswith("arn:aws:kms:"):
        raise FailClosedError("signing requires the exact asymmetric KMS key ARN")
    digest = manifest_digest(manifest)
    response = kms_client.sign(
        KeyId=key_arn, Message=digest, MessageType=MESSAGE_TYPE,
        SigningAlgorithm=SIGNING_ALGORITHM)
    used_key = response.get("KeyId")
    if used_key != key_arn:
        raise FailClosedError("KMS signed with an unexpected key")
    signature = response.get("Signature")
    if not isinstance(signature, (bytes, bytearray)) or not signature:
        raise FailClosedError("KMS returned no signature bytes")
    return {
        "key_arn": key_arn,
        "signing_algorithm": SIGNING_ALGORITHM,
        "message_type": MESSAGE_TYPE,
        "canonicalization": CANONICALIZATION,
        "manifest_sha256": digest.hex(),
        "signature_encoding": "base64",
        "signature": base64.b64encode(bytes(signature)).decode("ascii"),
    }


def verify_manifest(kms_client: Any, manifest: dict[str, Any], envelope: dict) -> bool:
    key_arn = envelope.get("key_arn")
    if not isinstance(key_arn, str) or not key_arn.startswith("arn:aws:kms:"):
        return False
    if envelope.get("signing_algorithm") != SIGNING_ALGORITHM:
        return False
    if envelope.get("message_type") != MESSAGE_TYPE:
        return False
    if envelope.get("canonicalization") != CANONICALIZATION:
        return False
    if envelope.get("signature_encoding") != "base64":
        return False
    digest = manifest_digest(manifest)
    if envelope.get("manifest_sha256") != digest.hex():
        return False
    try:
        signature = base64.b64decode(envelope.get("signature", ""), validate=True)
        response = kms_client.verify(
            KeyId=key_arn, Message=digest, MessageType=MESSAGE_TYPE,
            Signature=signature, SigningAlgorithm=SIGNING_ALGORITHM)
    except Exception:
        return False
    return response.get("KeyId") == key_arn and response.get("SignatureValid") is True


def write_dry_run_manifest(manifest: dict[str, Any], directory: Path) -> dict:
    """Local create-only output. This function has no S3 or AWS client."""
    body = canonical_manifest_bytes(manifest, require_pass=False)
    digest = hashlib.sha256(body).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    if path.exists() and path.read_bytes() != body:
        raise FailClosedError("dry-run manifest content-address collision")
    if not path.exists():
        with path.open("xb") as stream:
            stream.write(body)
    return {"path": str(path), "sha256": digest, "bytes": len(body)}


def promotion_preflight(manifest: dict[str, Any], gate_report: dict[str, Any],
                        signature_valid: bool, *, destination_exists: bool,
                        if_none_match: str | None) -> dict[str, Any]:
    """Application boundary that IAM alone cannot express completely."""
    reasons: list[str] = []
    try:
        validate_manifest(manifest, require_pass=True)
    except FailClosedError as exc:
        reasons.append(str(exc))
    if gate_report.get("overall") != "PASS":
        reasons.append("gate report outcome is not PASS")
    if not signature_valid:
        reasons.append("manifest signature is absent or invalid")
    if destination_exists:
        reasons.append("destination already exists; overwrite forbidden")
    if if_none_match != "*":
        reasons.append("immutable write requires If-None-Match: *")
    return {"authorized": not reasons, "reasons": reasons,
            "outcome": "PASS" if not reasons else "BLOCKED"}


def attach_blocked_report_to_mlflow(client: Any, run_id: str, report_path: Path,
                                    expected_report_sha256: str) -> dict[str, Any]:
    """Attach one BLOCKED report and prove registry count did not change."""
    if sha256_file(report_path) != expected_report_sha256:
        raise FailClosedError("MLflow report attachment hash mismatch")
    report = json.loads(report_path.read_bytes())
    if report.get("overall") != "BLOCKED":
        raise FailClosedError("this refusal-only attachment requires BLOCKED")
    run = client.get_run(run_id)
    if getattr(getattr(run, "info", None), "run_id", None) != run_id:
        raise FailClosedError("MLflow run identity mismatch")
    before = list(client.search_registered_models())
    client.log_artifact(run_id, str(report_path), artifact_path="b5/gate-reports")
    after = list(client.search_registered_models())
    if len(after) != len(before):
        raise FailClosedError("report attachment changed registered-model count")
    return {"run_id": run_id, "report_sha256": expected_report_sha256,
            "registered_models_before": len(before),
            "registered_models_after": len(after), "attached": True}


def ssm_dry_run_plan(desired: dict[str, str], current: dict[str, str],
                     namespace: str = "/medzen/b5-test") -> dict[str, Any]:
    if namespace == "/medzen/registry" or namespace.startswith("/medzen/registry/"):
        raise FailClosedError("production serving namespace is forbidden in B5 dry-run")
    changes = []
    for suffix, value in sorted(desired.items()):
        name = f"{namespace.rstrip('/')}/{suffix.lstrip('/')}"
        prior = current.get(name)
        changes.append({"name": name, "action": "CREATE" if prior is None else
                        ("UNCHANGED" if prior == value else "UPDATE")})
    return {"mode": "DRY_RUN", "namespace": namespace, "writes_performed": 0,
            "production_namespace_changes": 0, "changes": changes}


def report_attachment_does_not_register_source() -> str:
    """Auditable source hook used by tests: no registry API exists here."""
    return attach_blocked_report_to_mlflow.__name__
