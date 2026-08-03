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
PROMOTION_SIGNATURE_PURPOSE = "B5_PROMOTION"
DRY_RUN_SIGNATURE_PURPOSE = "B5_DRY_RUN_NON_PROMOTABLE"

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
    outcome = manifest.get("decision", {}).get("outcome")
    destination = manifest.get("artifact", {}).get("destination_prefix", "")
    if require_pass:
        if outcome != "PASS":
            raise FailClosedError("promotion manifest must bind a signed PASS decision")
        if not destination.startswith("s3://medzen-speech/approved/asr/"):
            raise FailClosedError("promotion destination must be under approved/asr/")
        _validate_production_bindings(manifest)
    else:
        if outcome != "BLOCKED":
            raise FailClosedError("dry-run manifest must remain BLOCKED")
        if not destination.startswith(
                "s3://medzen-speech/candidates/b5-dry-run/"):
            raise FailClosedError("dry-run destination must be under candidates/b5-dry-run/")
    frozen = manifest["evaluations"]["frozen_manifests"]
    if not isinstance(frozen, list) or not frozen:
        raise FailClosedError("at least one frozen evaluation manifest is required")
    for index, row in enumerate(frozen):
        if not isinstance(row, dict) or not row.get("path") or not row.get("sha256"):
            raise FailClosedError(f"frozen evaluation binding {index} is incomplete")
    _validate_json_domain(manifest)


def _validate_production_bindings(manifest: dict[str, Any]) -> None:
    hashes = (
        "artifact.tree_sha256", "artifact.adapter_sha256",
        "artifact.adapter_tree_sha256", "dataset.fingerprint",
        "dataset.adoption_record.sha256", "evaluations.gate_report.sha256",
        "evaluations.lingala_holdout.sha256", "provenance.bundle_sha256",
        "provenance.spot_resume_evidence.sha256",
    )
    for binding in hashes:
        value = _path_value(manifest, binding)
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)):
            raise FailClosedError(f"manifest binding {binding} is not SHA-256")
    image = _path_value(manifest, "provenance.container_image_digest")
    if not isinstance(image, str) or not image.startswith("sha256:"):
        raise FailClosedError("container image digest is malformed")
    for binding in ("artifact.base_model_revision", "artifact.tokenizer_revision",
                    "artifact.processor_revision"):
        if not isinstance(_path_value(manifest, binding), str):
            raise FailClosedError(f"manifest binding {binding} is not an exact revision")
    decode = _path_value(manifest, "decode.configuration")
    if not isinstance(decode, dict) or decode.get("state") == "NOT_EVALUATED":
        raise FailClosedError("decode configuration is not promotion-grade")


def canonical_manifest_bytes(manifest: dict[str, Any], *, require_pass: bool = True) -> bytes:
    validate_manifest(manifest, require_pass=require_pass)
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def manifest_digest(manifest: dict[str, Any], *, require_pass: bool = True) -> bytes:
    return hashlib.sha256(canonical_manifest_bytes(
        manifest, require_pass=require_pass)).digest()


def _sign(kms_client: Any, manifest: dict[str, Any], key_arn: str, *,
          require_pass: bool, purpose: str) -> dict:
    if not key_arn.startswith("arn:aws:kms:"):
        raise FailClosedError("signing requires the exact asymmetric KMS key ARN")
    digest = manifest_digest(manifest, require_pass=require_pass)
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
        "purpose": purpose,
        "key_arn": key_arn,
        "signing_algorithm": SIGNING_ALGORITHM,
        "message_type": MESSAGE_TYPE,
        "canonicalization": CANONICALIZATION,
        "manifest_sha256": digest.hex(),
        "signature_encoding": "base64",
        "signature": base64.b64encode(bytes(signature)).decode("ascii"),
    }


def sign_manifest(kms_client: Any, manifest: dict[str, Any], key_arn: str) -> dict:
    """Call real KMS Sign for a promotion-grade signed PASS manifest."""
    return _sign(kms_client, manifest, key_arn, require_pass=True,
                 purpose=PROMOTION_SIGNATURE_PURPOSE)


def sign_dry_run_manifest(kms_client: Any, manifest: dict[str, Any],
                          key_arn: str) -> dict:
    """Sign only a BLOCKED candidate-prefix manifest for path testing."""
    return _sign(kms_client, manifest, key_arn, require_pass=False,
                 purpose=DRY_RUN_SIGNATURE_PURPOSE)


def _verify(kms_client: Any, manifest: dict[str, Any], envelope: dict, *,
            require_pass: bool, purpose: str) -> bool:
    if envelope.get("purpose") != purpose:
        return False
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
    try:
        digest = manifest_digest(manifest, require_pass=require_pass)
    except FailClosedError:
        return False
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


def verify_manifest(kms_client: Any, manifest: dict[str, Any], envelope: dict) -> bool:
    return _verify(kms_client, manifest, envelope, require_pass=True,
                   purpose=PROMOTION_SIGNATURE_PURPOSE)


def verify_dry_run_manifest(kms_client: Any, manifest: dict[str, Any],
                            envelope: dict) -> bool:
    return _verify(kms_client, manifest, envelope, require_pass=False,
                   purpose=DRY_RUN_SIGNATURE_PURPOSE)


def build_current_artifact_dry_run_manifest(report: dict[str, Any],
                                            report_path: Path,
                                            root: Path) -> dict[str, Any]:
    """Bind the current refusal, including evidence gaps, without promotion."""
    if report.get("overall") != "BLOCKED":
        raise FailClosedError("current-artifact dry-run requires a BLOCKED report")
    report_sha = sha256_file(report_path)
    if report_path.stem != report_sha:
        raise FailClosedError("gate report filename is not its content address")
    candidate = report["candidate"]
    conversion = json.loads((root / "platform/evidence/CAMPAIGNRUN-2026-013-passed.json").read_bytes())
    source = json.loads((root / "platform/evidence/CAMPAIGNRUN-2026-012-failed.json").read_bytes())
    auth = json.loads((root / "platform/decisions/B5-AUTH-2026-001-refusal-engineering.json").read_bytes())
    mlflow = json.loads((root / "platform/evidence/B5-MLFLOW-RUN-RESOLUTION-2026-001.json").read_bytes())
    val_path = root / "platform/evidence/VAL-2026-001-frozen-validation-sets.json"
    holdout_path = root / "platform/evidence/VAL-2026-003-lingala-post-selection-holdout.json"
    spot_path = root / "platform/evidence/CAMPAIGNRUN-2026-014-passed.json"
    scope_path = root / "platform/decisions/B4-SCOPE-2026-002-simplified-exit.json"
    missing_adoption_hash = {
        "state": "NOT_EVALUATED",
        "reason": "Exact adoption-object body SHA-256 is absent from immutable B4 evidence.",
    }
    def evidence_path(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    return {
        "schema_version": SCHEMA_VERSION,
        "canonicalization": CANONICALIZATION,
        "decision": {
            "outcome": "BLOCKED",
            "purpose": DRY_RUN_SIGNATURE_PURPOSE,
            "gate_report_sha256": report_sha,
        },
        "artifact": {
            "tree_sha256": candidate["artifact_tree_sha256"],
            "base_model_revision": "openai/whisper-large-v3@06f233fe06e710322aca913c1bc4249a0d71fce1",
            "tokenizer_revision": "06f233fe06e710322aca913c1bc4249a0d71fce1",
            "processor_revision": {
                "state": "NOT_EVALUATED",
                "reason": "An exact processor revision is not separately bound by B4 evidence.",
            },
            "precision": candidate["precision"],
            "selected_checkpoint": candidate["selected_checkpoint"],
            "adapter_sha256": source["source_training"]["adapter_sha256"],
            "adapter_tree_sha256": candidate["adapter_tree_sha256"],
            "destination_prefix": (
                "s3://medzen-speech/candidates/b5-dry-run/"
                f"{candidate['artifact_tree_sha256']}/"),
        },
        "dataset": {
            "fingerprint": "eed56700ceadd37ac1513e49cd1798a6cddc20b46c90d2a9b2ed6b439685769e",
            "adoption_record": {
                "path": "s3://medzen-speech/curated/_versions/v2/ADOPTION-B4-SIMPLIFIED-8LANG-R3.json",
                "sha256": missing_adoption_hash,
            },
        },
        "evaluations": {
            "frozen_manifests": [{"path": evidence_path(val_path),
                                   "sha256": sha256_file(val_path)}],
            "gate_report": {"path": evidence_path(report_path),
                            "sha256": report_sha},
            "lingala_holdout": {"path": evidence_path(holdout_path),
                                "sha256": sha256_file(holdout_path)},
        },
        "provenance": {
            "git_commit": report["engine"]["git_commit"],
            "bundle_sha256": conversion["executed_provenance"]["bundle_tar_sha256"],
            "container_image_digest": conversion["executed_provenance"]["image_digest"],
            "spot_resume_evidence": {"path": evidence_path(spot_path),
                                     "sha256": sha256_file(spot_path)},
        },
        "decode": {"configuration": {
            "state": "NOT_EVALUATED",
            "reason": "No promotion-grade decode configuration is uniquely supported by B4 evidence.",
        }},
        "scope": {"deviations": [{"path": evidence_path(scope_path),
                                    "sha256": sha256_file(scope_path)}]},
        "mlflow": {"run_id": mlflow["attachment_rule"]["target_run_id"]},
        "approval": {"identity": f"{auth['id']}/{auth['authorized_by_role']}",
                     "timestamp_utc": auth["authorized_utc"]},
    }


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
