#!/usr/bin/env python3
"""Verify and, only after 003C-B approval, publish the exact B6A artifact."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlencode


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
BUCKET = "medzen-speech"
TREE = "5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e"
PREFIX = f"b6a/asr/v0/{TREE}/"
MANIFEST_SHA = "c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850"
KMS_KEY = "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57"


class ArtifactPublishRefusal(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _safe(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or path.as_posix() != raw:
        raise ArtifactPublishRefusal("unsafe artifact path")
    return path


def verify_local(root: Path, *, expected_tree: str = TREE,
                 expected_manifest_sha: str = MANIFEST_SHA) -> dict[str, Any]:
    root = root.resolve(strict=True)
    manifest_path = root / "MANIFEST.json"
    manifest_sha, manifest_bytes = _sha(manifest_path)
    if manifest_sha != expected_manifest_sha:
        raise ArtifactPublishRefusal("manifest SHA-256 differs")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactPublishRefusal("manifest is malformed") from exc
    if (
        manifest.get("classification") != "PLATFORM_PROOF_ONLY"
        or manifest.get("quality_disclosure", {}).get("production_approved") is not False
        or manifest.get("quality_disclosure", {}).get("quality_gate_outcome") != "FAIL"
    ):
        raise ArtifactPublishRefusal("platform-test failure disclosure differs")
    artifact = manifest.get("artifact", {})
    if artifact.get("tree_sha256") != expected_tree:
        raise ArtifactPublishRefusal("artifact tree binding differs")
    expected_prefix = f"s3://{BUCKET}/b6a/asr/v0/{expected_tree}/"
    if artifact.get("s3_prefix") != expected_prefix or "/approved/" in expected_prefix:
        raise ArtifactPublishRefusal("artifact S3 prefix differs")
    files = artifact.get("files")
    if not isinstance(files, dict) or not files:
        raise ArtifactPublishRefusal("artifact file bindings are absent")
    normalized: dict[str, dict[str, Any]] = {}
    objects = []
    total = 0
    for raw, binding in sorted(files.items()):
        path = root.joinpath(*_safe(raw).parts)
        digest, size = _sha(path)
        if binding != {"bytes": size, "sha256": digest}:
            raise ArtifactPublishRefusal(f"artifact binding differs: {raw}")
        normalized[raw] = {"bytes": size, "sha256": digest}
        objects.append({"name": raw, "path": path, "sha256": digest, "bytes": size})
        total += size
    if hashlib.sha256(_canonical(normalized)).hexdigest() != expected_tree:
        raise ArtifactPublishRefusal("recomputed artifact tree differs")
    actual_names = {item.name for item in root.iterdir() if item.is_file()}
    if actual_names != set(files) | {"MANIFEST.json"}:
        raise ArtifactPublishRefusal("artifact directory contains unexpected files")
    objects.append({
        "name": "MANIFEST.json",
        "path": manifest_path,
        "sha256": manifest_sha,
        "bytes": manifest_bytes,
    })
    return {
        "root": str(root),
        "tree_sha256": expected_tree,
        "manifest_sha256": manifest_sha,
        "artifact_bytes": total,
        "publish_bytes": total + manifest_bytes,
        "objects": objects,
    }


def _validate_authorization(path: Path, packet_sha256: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise ArtifactPublishRefusal("exact packet SHA-256 is required")
    try:
        record = json.loads(path.read_bytes())
    except Exception as exc:
        raise ArtifactPublishRefusal("003C-B authorization is unreadable") from exc
    if record.get("id") != "B6A-AWS-AUTH-2026-003C-B":
        raise ArtifactPublishRefusal("authorization id differs")
    if record.get("status") != "owner-approved":
        raise ArtifactPublishRefusal("003C-B is not owner-approved")
    if record.get("packet", {}).get("sha256") != packet_sha256:
        raise ArtifactPublishRefusal("authorization packet binding differs")


class ArtifactPublisher:
    def __init__(self, s3: Any):
        self.s3 = s3

    def preflight(self) -> None:
        versioning = self.s3.get_bucket_versioning(
            Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT
        )
        if versioning.get("Status") != "Enabled":
            raise ArtifactPublishRefusal("bucket versioning is not enabled")
        encryption = self.s3.get_bucket_encryption(
            Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT
        )
        rules = encryption.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
        if len(rules) != 1:
            raise ArtifactPublishRefusal("bucket encryption rule is ambiguous")
        default = rules[0].get("ApplyServerSideEncryptionByDefault", {})
        if default != {"SSEAlgorithm": "aws:kms", "KMSMasterKeyID": KMS_KEY}:
            raise ArtifactPublishRefusal("bucket KMS default differs")
        listed = self.s3.list_objects_v2(
            Bucket=BUCKET, Prefix=PREFIX, MaxKeys=1, ExpectedBucketOwner=ACCOUNT
        )
        if listed.get("KeyCount", 0) != 0 or listed.get("Contents"):
            raise ArtifactPublishRefusal("content-addressed B6A prefix is not empty")

    def publish(self, local: dict[str, Any]) -> dict[str, Any]:
        self.preflight()
        receipts = []
        # MANIFEST.json is deliberately last so a partial upload cannot look ready.
        for item in local["objects"]:
            checksum = base64.b64encode(bytes.fromhex(item["sha256"])).decode()
            with Path(item["path"]).open("rb") as body:
                response = self.s3.put_object(
                    Bucket=BUCKET,
                    Key=PREFIX + item["name"],
                    Body=body,
                    ContentLength=item["bytes"],
                    ContentType=(
                        "application/json" if item["name"] == "MANIFEST.json"
                        else "application/octet-stream"
                    ),
                    ChecksumSHA256=checksum,
                    ServerSideEncryption="aws:kms",
                    SSEKMSKeyId=KMS_KEY,
                    BucketKeyEnabled=True,
                    Metadata={
                        "sha256": item["sha256"],
                        "classification": "platform-proof-only",
                        "production-approved": "false",
                    },
                    Tagging=urlencode({
                        "medzen-classification": "platform-proof-only",
                        "medzen-serving-label": "v0",
                        "medzen-production-approved": "false",
                    }),
                    IfNoneMatch="*",
                    ExpectedBucketOwner=ACCOUNT,
                )
            version = response.get("VersionId")
            if not version:
                raise ArtifactPublishRefusal("S3 object version id is absent")
            head = self.s3.head_object(
                Bucket=BUCKET,
                Key=PREFIX + item["name"],
                VersionId=version,
                ChecksumMode="ENABLED",
                ExpectedBucketOwner=ACCOUNT,
            )
            if (
                head.get("ContentLength") != item["bytes"]
                or head.get("ChecksumSHA256") != checksum
                or head.get("ServerSideEncryption") != "aws:kms"
                or head.get("SSEKMSKeyId") != KMS_KEY
                or head.get("VersionId") != version
                or head.get("Metadata", {}).get("sha256") != item["sha256"]
            ):
                raise ArtifactPublishRefusal("uploaded S3 object verification differs")
            receipts.append({
                "key": PREFIX + item["name"],
                "sha256": item["sha256"],
                "bytes": item["bytes"],
                "checksum_sha256_base64": checksum,
                "version_id": version,
            })
        return {
            "status": "PUBLISHED_NONAPPROVED_AND_VERIFIED",
            "bucket": BUCKET,
            "prefix": PREFIX,
            "objects": receipts,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check-local", "publish"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--packet-sha256")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.receipt and args.receipt.exists():
            raise ArtifactPublishRefusal("refusing to overwrite artifact receipt")
        local = verify_local(args.root)
        if args.mode == "check-local":
            result = {
                "status": "LOCAL_ARTIFACT_VERIFIED_NOT_PUBLISHED",
                **{key: value for key, value in local.items() if key != "objects"},
                "objects": [
                    {key: value for key, value in item.items() if key != "path"}
                    for item in local["objects"]
                ],
            }
        else:
            if args.authorization is None or args.packet_sha256 is None:
                raise ArtifactPublishRefusal("publish requires authorization and packet hash")
            _validate_authorization(args.authorization, args.packet_sha256)
            import boto3

            s3 = boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")
            result = ArtifactPublisher(s3).publish(local)
        encoded = json.dumps(result, sort_keys=True) + "\n"
        if args.receipt:
            args.receipt.write_text(encoded)
        print(encoded, end="")
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
