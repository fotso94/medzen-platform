from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.upload_b6a_003c_b_artifact import (
    ACCOUNT,
    ArtifactPublisher,
    ArtifactPublishRefusal,
    BUCKET,
    KMS_KEY,
    PREFIX,
    verify_local,
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def local_artifact(tmp_path: Path):
    root = tmp_path / "artifact"
    root.mkdir(parents=True)
    (root / "model.bin").write_bytes(b"model")
    files = {"model.bin": {"bytes": 5, "sha256": hashlib.sha256(b"model").hexdigest()}}
    tree = hashlib.sha256(_canonical(files)).hexdigest()
    manifest = {
        "classification": "PLATFORM_PROOF_ONLY",
        "quality_disclosure": {"production_approved": False, "quality_gate_outcome": "FAIL"},
        "artifact": {
            "tree_sha256": tree,
            "s3_prefix": f"s3://{BUCKET}/b6a/asr/v0/{tree}/",
            "files": files,
        },
    }
    raw = _canonical(manifest) + b"\n"
    (root / "MANIFEST.json").write_bytes(raw)
    return root, tree, hashlib.sha256(raw).hexdigest()


class S3:
    def __init__(self):
        self.objects = {}
        self.order = []

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled"}

    def get_bucket_encryption(self, **kwargs):
        return {"ServerSideEncryptionConfiguration": {"Rules": [{
            "ApplyServerSideEncryptionByDefault": {
                "SSEAlgorithm": "aws:kms", "KMSMasterKeyID": KMS_KEY
            }
        }]}}

    def list_objects_v2(self, **kwargs):
        return {"KeyCount": 0}

    def put_object(self, **kwargs):
        body = kwargs["Body"].read()
        version = f"version-{len(self.objects) + 1}"
        self.objects[kwargs["Key"]] = {**kwargs, "Body": body, "VersionId": version}
        self.order.append(kwargs["Key"])
        return {"VersionId": version}

    def head_object(self, **kwargs):
        item = self.objects[kwargs["Key"]]
        return {
            "ContentLength": item["ContentLength"],
            "ChecksumSHA256": item["ChecksumSHA256"],
            "ServerSideEncryption": item["ServerSideEncryption"],
            "SSEKMSKeyId": item["SSEKMSKeyId"],
            "VersionId": item["VersionId"],
            "Metadata": item["Metadata"],
        }


def test_local_verification_recomputes_manifest_file_and_tree_hashes(tmp_path):
    root, tree, manifest_sha = local_artifact(tmp_path)
    result = verify_local(root, expected_tree=tree, expected_manifest_sha=manifest_sha)
    assert result["tree_sha256"] == tree
    assert result["artifact_bytes"] == 5
    assert [item["name"] for item in result["objects"]] == ["model.bin", "MANIFEST.json"]


def test_local_verification_refuses_tamper_and_unexpected_files(tmp_path):
    root, tree, manifest_sha = local_artifact(tmp_path)
    (root / "model.bin").write_bytes(b"tampered")
    with pytest.raises(ArtifactPublishRefusal, match="binding differs"):
        verify_local(root, expected_tree=tree, expected_manifest_sha=manifest_sha)
    root, tree, manifest_sha = local_artifact(tmp_path / "second")
    (root / "unexpected").write_text("no")
    with pytest.raises(ArtifactPublishRefusal, match="unexpected files"):
        verify_local(root, expected_tree=tree, expected_manifest_sha=manifest_sha)


def test_publisher_requires_empty_versioned_kms_bucket_and_manifest_last(tmp_path):
    root, tree, manifest_sha = local_artifact(tmp_path)
    local = verify_local(root, expected_tree=tree, expected_manifest_sha=manifest_sha)
    s3 = S3()
    result = ArtifactPublisher(s3).publish(local)
    assert result["status"] == "PUBLISHED_NONAPPROVED_AND_VERIFIED"
    assert s3.order[-1].endswith("/MANIFEST.json")
    for item in s3.objects.values():
        assert item["Bucket"] == BUCKET
        assert item["ExpectedBucketOwner"] == ACCOUNT
        assert item["IfNoneMatch"] == "*"
        assert item["ServerSideEncryption"] == "aws:kms"
        assert item["SSEKMSKeyId"] == KMS_KEY
        assert item["ChecksumSHA256"] == base64.b64encode(
            bytes.fromhex(item["Metadata"]["sha256"])
        ).decode()


def test_publisher_refuses_nonempty_prefix_before_any_write(tmp_path):
    root, tree, manifest_sha = local_artifact(tmp_path)
    local = verify_local(root, expected_tree=tree, expected_manifest_sha=manifest_sha)
    s3 = S3()
    s3.list_objects_v2 = lambda **kwargs: {"KeyCount": 1, "Contents": [{"Key": PREFIX}]}
    with pytest.raises(ArtifactPublishRefusal, match="not empty"):
        ArtifactPublisher(s3).publish(local)
    assert s3.objects == {}


def test_receipt_overwrite_refusal_precedes_publish_call():
    text = (ROOT / "scripts/upload_b6a_003c_b_artifact.py").read_text()
    refusal = text.index("if args.receipt and args.receipt.exists()")
    publish = text.index("ArtifactPublisher(s3).publish(local)")
    assert refusal < publish
