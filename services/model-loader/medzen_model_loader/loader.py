from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse


BUCKET = "medzen-speech"
ALLOWED_PREFIX = "b6a/asr/v0/"
MAX_ARTIFACT_BYTES = 8_000_000_000
REQUIRED_FILES = {
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
}
BASE_REVISION = "06f233fe06e710322aca913c1bc4249a0d71fce1"


class LoaderRefusal(RuntimeError):
    """The artifact is not exactly the authorized B6A platform-test input."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise LoaderRefusal("manifest URI must be an exact s3:// URI")
    return parsed.netloc, parsed.path.lstrip("/")


def _safe_relative_path(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if (path.is_absolute() or ".." in path.parts or "." in path.parts
            or path.as_posix() != raw or not raw):
        raise LoaderRefusal(f"unsafe artifact path: {raw!r}")
    return path


def validate_manifest(manifest: Mapping[str, Any], manifest_uri: str) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        raise LoaderRefusal("unsupported B6A manifest schema")
    if manifest.get("classification") != "PLATFORM_PROOF_ONLY":
        raise LoaderRefusal("artifact is not classified as a platform proof")
    if manifest.get("serving_label") != "v0":
        raise LoaderRefusal("B6A loader accepts serving label v0 only")

    artifact = manifest.get("artifact")
    if not isinstance(artifact, Mapping):
        raise LoaderRefusal("manifest artifact binding is missing")
    if artifact.get("model_id") != "openai/whisper-large-v3":
        raise LoaderRefusal("B6A requires exact zero-shot Whisper large-v3")
    for field in ("base_model_revision", "tokenizer_revision", "processor_revision"):
        if artifact.get(field) != BASE_REVISION:
            raise LoaderRefusal(f"{field} is not pinned to the authorized revision")
    if artifact.get("precision") != "CTranslate2_float16":
        raise LoaderRefusal("B6A requires the CTranslate2 float16 artifact")
    if artifact.get("fine_tuned") is not False or artifact.get("adapter_sha256") is not None:
        raise LoaderRefusal("fine-tuned or adapter-derived artifacts are forbidden in B6A")

    disclosure = manifest.get("quality_disclosure")
    if (not isinstance(disclosure, Mapping)
            or disclosure.get("production_approved") is not False
            or disclosure.get("quality_gate_outcome") != "FAIL"
            or disclosure.get("absolute_wer_max") != 0.2
            or disclosure.get("zero_shot_base_wer") != {
                "lingala": 0.9207, "luganda": 1.0659, "oromo": 1.1749}):
        raise LoaderRefusal("zero-shot quality failure disclosure is incomplete")

    decode = manifest.get("decode_configuration")
    expected_decode = {
        "purpose": "B6A_PLATFORM_TEST_ONLY_NOT_PROMOTION_GRADE",
        "task": "transcribe",
        "beam_size": 1,
        "best_of": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "word_timestamps": False,
    }
    if decode != expected_decode:
        raise LoaderRefusal("B6A test decode configuration differs")

    provenance = manifest.get("provenance")
    if (not isinstance(provenance, Mapping)
            or re.fullmatch(r"[0-9a-f]{40}", str(
                provenance.get("git_commit", ""))) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(
                provenance.get("container_image_digest", ""))) is None
            or provenance.get("converter") != "ct2-transformers-converter@4.8.1"
            or not str(provenance.get("converted_at_utc", "")).endswith("Z")
            or re.fullmatch(r"[0-9a-f]{64}", str(
                provenance.get("source_tree_sha256", ""))) is None
            or not isinstance(provenance.get("source_bytes"), int)
            or isinstance(provenance.get("source_bytes"), bool)
            or provenance["source_bytes"] <= 0
            or not isinstance(provenance.get("source_files"), Mapping)
            or not {"config.json", "model.safetensors",
                    "preprocessor_config.json", "tokenizer.json"} <= set(
                        provenance["source_files"])):
        raise LoaderRefusal("conversion provenance is incomplete")
    normalized_source: dict[str, dict[str, Any]] = {}
    source_total = 0
    for raw, binding in sorted(provenance["source_files"].items()):
        if not isinstance(raw, str) or not isinstance(binding, Mapping):
            raise LoaderRefusal("conversion source binding is malformed")
        _safe_relative_path(raw)
        digest, size = binding.get("sha256"), binding.get("bytes")
        if (not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise LoaderRefusal("conversion source binding is malformed")
        normalized_source[raw] = {"sha256": digest, "bytes": size}
        source_total += size
    if (source_total != provenance["source_bytes"]
            or _sha256_bytes(_canonical(normalized_source))
            != provenance["source_tree_sha256"]):
        raise LoaderRefusal("conversion source provenance hash mismatch")

    bucket, key = parse_s3_uri(manifest_uri)
    if bucket != BUCKET or not key.startswith(ALLOWED_PREFIX) or not key.endswith(
            "/MANIFEST.json"):
        raise LoaderRefusal("manifest is outside the non-approved B6A path")
    if "/approved/" in f"/{key}":
        raise LoaderRefusal("approved artifact paths are forbidden in B6A")
    artifact_prefix = artifact.get("s3_prefix")
    expected_prefix = f"s3://{bucket}/{key[:-len('MANIFEST.json')]}"
    if artifact_prefix != expected_prefix:
        raise LoaderRefusal("artifact prefix and manifest location differ")

    files = artifact.get("files")
    if not isinstance(files, Mapping) or not files:
        raise LoaderRefusal("artifact file bindings are missing")
    if not REQUIRED_FILES <= set(files):
        raise LoaderRefusal("CTranslate2 artifact is missing required files")
    normalized: dict[str, dict[str, Any]] = {}
    total = 0
    for raw, binding in sorted(files.items()):
        if not isinstance(raw, str) or not isinstance(binding, Mapping):
            raise LoaderRefusal("malformed artifact file binding")
        _safe_relative_path(raw)
        digest, size = binding.get("sha256"), binding.get("bytes")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise LoaderRefusal("malformed artifact hash or byte count")
        normalized[raw] = {"sha256": digest, "bytes": size}
        total += size
    if total > MAX_ARTIFACT_BYTES:
        raise LoaderRefusal("artifact exceeds the B6A 8 GB boundary")
    tree = _sha256_bytes(_canonical(normalized))
    if artifact.get("tree_sha256") != tree:
        raise LoaderRefusal("artifact tree SHA-256 mismatch")
    if not key.startswith(f"{ALLOWED_PREFIX}{tree}/"):
        raise LoaderRefusal("artifact path is not content-addressed by its tree hash")
    return {"bucket": bucket, "manifest_key": key,
            "artifact_key_prefix": key[:-len("MANIFEST.json")],
            "tree_sha256": tree, "files": normalized, "bytes": total}


def _stream_object_to_path(s3_client: Any, bucket: str, key: str, path: Path,
                           expected: Mapping[str, Any]) -> None:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    digest = hashlib.sha256()
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        while True:
            chunk = body.read(8 * 1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
            digest.update(chunk)
            count += len(chunk)
            if count > expected["bytes"]:
                raise LoaderRefusal(f"artifact object exceeds bound: {key}")
    if count != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
        raise LoaderRefusal(f"artifact object hash/size mismatch: {key}")


def load_artifact(s3_client: Any, manifest_uri: str, manifest_sha256: str,
                  destination: Path) -> dict[str, Any]:
    if (not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64
            or any(c not in "0123456789abcdef" for c in manifest_sha256)):
        raise LoaderRefusal("exact manifest SHA-256 is required")
    bucket, manifest_key = parse_s3_uri(manifest_uri)
    raw = s3_client.get_object(Bucket=bucket, Key=manifest_key)["Body"].read()
    if _sha256_bytes(raw) != manifest_sha256:
        raise LoaderRefusal("manifest SHA-256 mismatch")
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoaderRefusal("manifest is not valid UTF-8 JSON") from exc
    validated = validate_manifest(manifest, manifest_uri)

    destination.mkdir(parents=True, exist_ok=True)
    staging = destination / ".loading"
    if staging.exists():
        shutil.rmtree(staging)
    if any(destination.iterdir()):
        raise LoaderRefusal("model destination is not empty")
    staging.mkdir()
    try:
        for rel, expected in sorted(validated["files"].items()):
            path = staging.joinpath(*PurePosixPath(rel).parts)
            _stream_object_to_path(
                s3_client, validated["bucket"],
                validated["artifact_key_prefix"] + rel, path, expected)
        marker = {
            "schema_version": 2,
            "artifact_verified": True,
            "classification": "PLATFORM_PROOF_ONLY",
            "serving_label": "v0",
            "manifest_uri": manifest_uri,
            "manifest_sha256": manifest_sha256,
            "artifact_tree_sha256": validated["tree_sha256"],
            "artifact_bytes": validated["bytes"],
            "model_id": "openai/whisper-large-v3",
            "base_model_revision": BASE_REVISION,
            "precision": "CTranslate2_float16",
            "production_approved": False,
            "quality_gate_outcome": "FAIL",
            "decode_configuration": manifest["decode_configuration"],
            "verification": {
                "manifest_sha256": True,
                "artifact_file_hashes": True,
                "artifact_tree_sha256": True,
            },
        }
        for child in sorted(staging.iterdir()):
            child.replace(destination / child.name)
        staging.rmdir()
        marker_path = destination / ".medzen-ready.json"
        marker_path.write_bytes(_canonical(marker) + b"\n")
        return marker
    except Exception:
        # A failed init container leaves no misleading readiness marker.
        (destination / ".medzen-ready.json").unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main() -> int:
    import boto3

    manifest_uri = os.environ.get("MODEL_MANIFEST_S3_URI", "")
    manifest_sha = os.environ.get("MODEL_MANIFEST_SHA256", "")
    destination = Path(os.environ.get("MODEL_DESTINATION", "/models"))
    try:
        marker = load_artifact(
            boto3.client("s3", region_name=os.environ.get(
                "AWS_REGION", "eu-central-1")),
            manifest_uri, manifest_sha, destination)
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    print(json.dumps({"status": "VERIFIED", "serving_label": marker["serving_label"],
                      "artifact_tree_sha256": marker["artifact_tree_sha256"]}))
    return 0
