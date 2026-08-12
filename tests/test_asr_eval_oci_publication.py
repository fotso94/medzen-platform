from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_eval_oci_publication import (
    ECR_PART_BYTES,
    OCI_INDEX,
    OCI_MANIFEST,
    OciLayout,
    OciPublicationRefusal,
    publish_exact_layout,
    publish_exact_image,
)


def _write_blob(root: Path, content: bytes) -> dict:
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    path = root / "blobs/sha256" / digest.removeprefix("sha256:")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {"digest": digest, "size": len(content)}


def _json_blob(root: Path, value: dict, media_type: str) -> dict:
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return {**_write_blob(root, content), "mediaType": media_type}


def layout_fixture(tmp_path: Path, *, layer_bytes: bytes = b"verified-layer") -> tuple[OciLayout, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    config = _json_blob(tmp_path, {"architecture": "amd64", "os": "linux"}, "application/vnd.oci.image.config.v1+json")
    layer = {**_write_blob(tmp_path, layer_bytes), "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}
    child_value = {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": config, "layers": [layer]}
    child = {**_json_blob(tmp_path, child_value, OCI_MANIFEST), "platform": {"os": "linux", "architecture": "amd64"}}
    attestation_config = _json_blob(tmp_path, {"architecture": "unknown", "os": "unknown"}, "application/vnd.oci.image.config.v1+json")
    predicate = {**_write_blob(tmp_path, b"attestation"), "mediaType": "application/vnd.in-toto+json"}
    attestation_value = {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": attestation_config, "layers": [predicate]}
    attestation = {**_json_blob(tmp_path, attestation_value, OCI_MANIFEST), "platform": {"os": "unknown", "architecture": "unknown"}}
    index_value = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [child, attestation]}
    index = _json_blob(tmp_path, index_value, OCI_INDEX)
    (tmp_path / "index.json").write_text(json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [index]}))
    layout = OciLayout(
        tmp_path,
        expected_index=index["digest"],
        expected_child=child["digest"],
        expected_config=config["digest"],
        expected_attestation=attestation["digest"],
    )
    return layout, {"index": index, "child": child, "config": config, "attestation": attestation, "layer": layer}


class FakeEcr:
    def __init__(self, *, available: set[str] | None = None, truncate_part: bool = False):
        self.available = set(available or ())
        self.truncate_part = truncate_part
        self.uploads: dict[str, bytearray] = {}
        self.manifests: dict[str, str] = {}
        self.tag: dict[str, str] = {}

    def batch_check_layer_availability(self, **kwargs):
        return {"layers": [{"layerDigest": value, "layerAvailability": "AVAILABLE"} for value in kwargs["layerDigests"] if value in self.available], "failures": []}

    def initiate_layer_upload(self, **kwargs):
        upload_id = f"upload-{len(self.uploads)}"
        self.uploads[upload_id] = bytearray()
        return {"uploadId": upload_id, "partSize": ECR_PART_BYTES}

    def upload_layer_part(self, **kwargs):
        body = kwargs["layerPartBlob"]
        assert kwargs["partFirstByte"] == len(self.uploads[kwargs["uploadId"]])
        self.uploads[kwargs["uploadId"]].extend(body)
        last = kwargs["partLastByte"] - (1 if self.truncate_part else 0)
        return {"uploadId": kwargs["uploadId"], "lastByteReceived": last}

    def complete_layer_upload(self, **kwargs):
        digest = "sha256:" + hashlib.sha256(self.uploads[kwargs["uploadId"]]).hexdigest()
        assert kwargs["layerDigests"] == [digest]
        self.available.add(digest)
        return {"layerDigest": digest}

    def put_image(self, **kwargs):
        measured = "sha256:" + hashlib.sha256(kwargs["imageManifest"].encode()).hexdigest()
        assert measured == kwargs["imageDigest"]
        self.manifests[measured] = kwargs["imageManifest"]
        if "imageTag" in kwargs:
            self.tag[kwargs["imageTag"]] = measured
        return {"image": {"imageId": {"imageDigest": measured}}}

    def batch_get_image(self, **kwargs):
        image_id = kwargs["imageIds"][0]
        digest = self.tag.get(image_id["imageTag"]) if "imageTag" in image_id else image_id.get("imageDigest")
        if digest is None:
            return {"images": [], "failures": []}
        return {"images": [{"imageId": {"imageDigest": digest}, "imageManifest": self.manifests[digest]}], "failures": []}


def test_layout_verifies_every_reachable_content_addressed_object(tmp_path: Path) -> None:
    layout, refs = layout_fixture(tmp_path)
    result = layout.verify()
    assert result["status"] == "PASS_EXACT_OCI_LAYOUT"
    assert result["verified_content_addressed_objects"] == 7
    assert result["largest_layer"] == {"digest": refs["layer"]["digest"], "bytes": refs["layer"]["size"]}


def test_layout_refuses_one_corrupt_layer_byte(tmp_path: Path) -> None:
    layout, refs = layout_fixture(tmp_path)
    path = layout.blob_path(refs["layer"]["digest"])
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(OciPublicationRefusal) as captured:
        layout.verify()
    assert captured.value.reason_code == "OCI_BLOB_BYTES_DIFFER"


def test_multipart_publication_preserves_exact_index_and_blob_bytes(tmp_path: Path) -> None:
    layout, refs = layout_fixture(tmp_path, layer_bytes=b"x" * (ECR_PART_BYTES + 7))
    ecr = FakeEcr()
    result = publish_exact_layout(ecr, "medzen-asr-eval-runtime", layout, tag="pilot-exact")
    assert result["status"] == "PASS_EXACT_MULTIPART_ECR_PUBLICATION"
    uploaded_layer = next(item for item in result["uploaded"] if item["digest"] == refs["layer"]["digest"])
    assert uploaded_layer["parts"] == 2
    assert ecr.tag["pilot-exact"] == refs["index"]["digest"]
    assert result["all_manifest_readbacks_byte_identical"] is True
    assert result["readback_manifest_digests"] == [
        refs["child"]["digest"],
        refs["attestation"]["digest"],
        refs["index"]["digest"],
    ]


def test_multipart_publication_refuses_a_truncated_part_before_completion(tmp_path: Path) -> None:
    layout, _ = layout_fixture(tmp_path)
    with pytest.raises(OciPublicationRefusal) as captured:
        publish_exact_layout(FakeEcr(truncate_part=True), "medzen-asr-eval-runtime", layout, tag="pilot-exact")
    assert captured.value.reason_code == "ECR_PART_CONTINUITY_DIFFERS"


def test_multipart_publication_reuses_only_ecr_proven_available_blobs(tmp_path: Path) -> None:
    layout, refs = layout_fixture(tmp_path)
    ecr = FakeEcr(available={refs["config"]["digest"]})
    result = publish_exact_layout(ecr, "medzen-asr-eval-runtime", layout, tag="pilot-exact")
    assert result["reused_digests"] == [refs["config"]["digest"]]
    assert refs["config"]["digest"] not in {item["digest"] for item in result["uploaded"]}


def test_exact_image_wrapper_deletes_export_after_success(tmp_path: Path) -> None:
    source = tmp_path / "source"
    layout, refs = layout_fixture(source)

    def exporter(local_tag: str, archive: Path) -> None:
        assert local_tag == "image:bound"
        import tarfile
        with tarfile.open(archive, "w") as stream:
            for path in source.rglob("*"):
                if path.is_file():
                    stream.add(path, arcname=str(path.relative_to(source)))

    result = publish_exact_image(
        FakeEcr(),
        "medzen-asr-eval-runtime",
        {
            "local_tag": "image:bound",
            "tag": "pilot-exact",
            "oci_index_digest": refs["index"]["digest"],
            "linux_amd64_digest": refs["child"]["digest"],
            "config_digest": refs["config"]["digest"],
            "attestation_digest": refs["attestation"]["digest"],
        },
        work_parent=tmp_path / "work",
        exporter=exporter,
    )
    assert result["temporary_export_removed_after_verification"] is True
    assert list((tmp_path / "work").iterdir()) == []
