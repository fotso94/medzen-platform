#!/usr/bin/env python3
"""Verify an OCI layout and publish its exact blobs through bounded ECR parts.

The module is import-safe and has no implicit network behavior.  Callers must
provide an ECR client explicitly.  It exists because a Docker registry push in
ASR pilot attempt 3 truncated a 4.33 GB layer before ECR verified its digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, Iterable


OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
SHA256_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
ECR_PART_BYTES = 20 * 1024 * 1024


class OciPublicationRefusal(RuntimeError):
    """A fail-closed OCI verification or publication refusal."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def _hex_digest(value: str) -> str:
    matched = SHA256_RE.fullmatch(value)
    if matched is None:
        raise OciPublicationRefusal("OCI_DIGEST_MALFORMED", "OCI descriptor digest is malformed")
    return matched.group(1)


def _stream_sha256(stream: BinaryIO) -> tuple[str, int]:
    measured = hashlib.sha256()
    size = 0
    while True:
        block = stream.read(8 * 1024 * 1024)
        if not block:
            break
        measured.update(block)
        size += len(block)
    return measured.hexdigest(), size


class OciLayout:
    """A fully verified OCI image layout rooted at an exported directory."""

    def __init__(
        self,
        root: Path,
        *,
        expected_index: str,
        expected_child: str,
        expected_config: str,
        expected_attestation: str,
    ):
        self.root = root
        self.expected_index = expected_index
        self.expected_child = expected_child
        self.expected_config = expected_config
        self.expected_attestation = expected_attestation
        self.descriptors: dict[str, dict[str, Any]] = {}
        self.index_manifest: dict[str, Any] = {}
        self.child_manifest: dict[str, Any] = {}
        self.attestation_manifest: dict[str, Any] = {}

    def blob_path(self, digest: str) -> Path:
        return self.root / "blobs" / "sha256" / _hex_digest(digest)

    def _verify_descriptor(self, descriptor: dict[str, Any]) -> Path:
        digest = descriptor.get("digest")
        size = descriptor.get("size")
        if not isinstance(digest, str) or isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise OciPublicationRefusal("OCI_DESCRIPTOR_MALFORMED", "OCI descriptor digest or size is malformed")
        path = self.blob_path(digest)
        if not path.is_file():
            raise OciPublicationRefusal("OCI_BLOB_ABSENT", f"OCI blob is absent: {digest}")
        with path.open("rb") as stream:
            measured, measured_size = _stream_sha256(stream)
        if measured != _hex_digest(digest) or measured_size != size:
            raise OciPublicationRefusal("OCI_BLOB_BYTES_DIFFER", f"OCI blob bytes differ: {digest}")
        prior = self.descriptors.get(digest)
        normalized = {
            "digest": digest,
            "size": size,
            "mediaType": descriptor.get("mediaType"),
        }
        if prior is not None and (prior["size"], prior["mediaType"]) != (size, normalized["mediaType"]):
            raise OciPublicationRefusal("OCI_DESCRIPTOR_AMBIGUOUS", f"OCI descriptor is ambiguous: {digest}")
        self.descriptors[digest] = normalized
        return path

    def _load_json_descriptor(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        path = self._verify_descriptor(descriptor)
        try:
            value = json.loads(path.read_bytes())
        except Exception as exc:
            raise OciPublicationRefusal("OCI_MANIFEST_MALFORMED", "OCI manifest is not valid JSON") from exc
        if not isinstance(value, dict):
            raise OciPublicationRefusal("OCI_MANIFEST_MALFORMED", "OCI manifest is not an object")
        return value

    def verify(self) -> dict[str, Any]:
        layout_path = self.root / "oci-layout"
        index_path = self.root / "index.json"
        try:
            layout = json.loads(layout_path.read_bytes())
            root_index = json.loads(index_path.read_bytes())
        except Exception as exc:
            raise OciPublicationRefusal("OCI_LAYOUT_MALFORMED", "OCI layout metadata is absent or malformed") from exc
        if layout != {"imageLayoutVersion": "1.0.0"} or root_index.get("schemaVersion") != 2:
            raise OciPublicationRefusal("OCI_LAYOUT_MALFORMED", "OCI layout version or root index differs")
        roots = [item for item in root_index.get("manifests", []) if item.get("digest") == self.expected_index]
        if len(roots) != 1 or roots[0].get("mediaType") != OCI_INDEX:
            raise OciPublicationRefusal("OCI_INDEX_BINDING_DIFFERS", "bound OCI index is not the unique export root")
        self.index_manifest = self._load_json_descriptor(roots[0])
        children = self.index_manifest.get("manifests", [])
        child = [item for item in children if item.get("digest") == self.expected_child]
        attestation = [item for item in children if item.get("digest") == self.expected_attestation]
        if len(child) != 1 or child[0].get("mediaType") != OCI_MANIFEST:
            raise OciPublicationRefusal("OCI_CHILD_BINDING_DIFFERS", "bound linux/amd64 child is absent or ambiguous")
        platform = child[0].get("platform", {})
        if platform.get("os") != "linux" or platform.get("architecture") != "amd64":
            raise OciPublicationRefusal("OCI_CHILD_PLATFORM_DIFFERS", "bound child is not linux/amd64")
        if len(attestation) != 1 or attestation[0].get("mediaType") != OCI_MANIFEST:
            raise OciPublicationRefusal("OCI_ATTESTATION_BINDING_DIFFERS", "bound attestation manifest is absent or ambiguous")
        self.child_manifest = self._load_json_descriptor(child[0])
        self.attestation_manifest = self._load_json_descriptor(attestation[0])
        if self.child_manifest.get("config", {}).get("digest") != self.expected_config:
            raise OciPublicationRefusal("OCI_CONFIG_BINDING_DIFFERS", "bound config digest differs")
        for manifest in (self.child_manifest, self.attestation_manifest):
            if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != OCI_MANIFEST:
                raise OciPublicationRefusal("OCI_MANIFEST_MALFORMED", "OCI child manifest type differs")
            config = manifest.get("config")
            layers = manifest.get("layers")
            if not isinstance(config, dict) or not isinstance(layers, list):
                raise OciPublicationRefusal("OCI_MANIFEST_MALFORMED", "OCI config or layer descriptors are absent")
            self._verify_descriptor(config)
            for descriptor in layers:
                if not isinstance(descriptor, dict):
                    raise OciPublicationRefusal("OCI_DESCRIPTOR_MALFORMED", "OCI layer descriptor is not an object")
                self._verify_descriptor(descriptor)
        layer_descriptors = self.child_manifest["layers"]
        largest = max(layer_descriptors, key=lambda item: item["size"])
        return {
            "status": "PASS_EXACT_OCI_LAYOUT",
            "oci_index_digest": self.expected_index,
            "linux_amd64_digest": self.expected_child,
            "config_digest": self.expected_config,
            "attestation_digest": self.expected_attestation,
            "verified_content_addressed_objects": len(self.descriptors),
            "child_layer_count": len(layer_descriptors),
            "child_compressed_bytes": sum(item["size"] for item in layer_descriptors),
            "largest_layer": {"digest": largest["digest"], "bytes": largest["size"]},
        }

    def content_descriptors(self) -> list[dict[str, Any]]:
        manifest_digests = {self.expected_index, self.expected_child, self.expected_attestation}
        return [self.descriptors[key] for key in sorted(self.descriptors) if key not in manifest_digests]

    def manifest_sequence(self) -> list[tuple[str, dict[str, Any], str | None]]:
        return [
            (self.expected_child, self.child_manifest, None),
            (self.expected_attestation, self.attestation_manifest, None),
            (self.expected_index, self.index_manifest, "index"),
        ]


def extract_oci_archive(archive: Path, destination: Path) -> None:
    """Extract only safe relative members from a Docker OCI archive."""
    with tarfile.open(archive, "r") as stream:
        members = stream.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise OciPublicationRefusal("OCI_ARCHIVE_MEMBER_UNSAFE", "OCI archive contains an unsafe member")
        stream.extractall(destination, members=members, filter="data")


def export_exact_image(local_tag: str, destination: Path, *, runner=subprocess.run) -> None:
    completed = runner(
        ["docker", "image", "save", "--output", str(destination), local_tag],
        capture_output=True,
        check=False,
        timeout=3600,
    )
    if completed.returncode != 0:
        stderr = " ".join(completed.stderr.decode(errors="replace").split())[:512]
        raise OciPublicationRefusal("OCI_EXPORT_REFUSED", f"docker image save exited {completed.returncode}: {stderr}")


def _chunks(stream: BinaryIO, size: int = ECR_PART_BYTES) -> Iterable[tuple[int, bytes]]:
    offset = 0
    while True:
        block = stream.read(size)
        if not block:
            return
        yield offset, block
        offset += len(block)


def publish_exact_layout(
    ecr: Any,
    repository: str,
    layout: OciLayout,
    *,
    tag: str,
) -> dict[str, Any]:
    """Publish verified blobs in bounded parts, then exact manifests."""
    verified = layout.verify()
    descriptors = layout.content_descriptors()
    availability = ecr.batch_check_layer_availability(
        repositoryName=repository,
        layerDigests=[item["digest"] for item in descriptors],
    )
    failures = availability.get("failures", [])
    if failures:
        raise OciPublicationRefusal("ECR_LAYER_AVAILABILITY_REFUSED", "ECR layer availability returned failures")
    available = {
        item["layerDigest"]
        for item in availability.get("layers", [])
        if item.get("layerAvailability") == "AVAILABLE"
    }
    uploaded: list[dict[str, Any]] = []
    reused: list[str] = []
    for descriptor in descriptors:
        digest = descriptor["digest"]
        if digest in available:
            reused.append(digest)
            continue
        initiated = ecr.initiate_layer_upload(repositoryName=repository)
        upload_id = initiated.get("uploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise OciPublicationRefusal("ECR_UPLOAD_ID_ABSENT", "ECR did not return a layer upload ID")
        part_count = 0
        last_byte = -1
        with layout.blob_path(digest).open("rb") as stream:
            for first, block in _chunks(stream):
                last = first + len(block) - 1
                response = ecr.upload_layer_part(
                    repositoryName=repository,
                    uploadId=upload_id,
                    partFirstByte=first,
                    partLastByte=last,
                    layerPartBlob=block,
                )
                if response.get("uploadId") != upload_id or response.get("lastByteReceived") != last:
                    raise OciPublicationRefusal("ECR_PART_CONTINUITY_DIFFERS", f"ECR part continuity differs: {digest}")
                part_count += 1
                last_byte = last
        if last_byte + 1 != descriptor["size"]:
            raise OciPublicationRefusal("ECR_SOURCE_SIZE_DIFFERS", f"source byte count differs before completion: {digest}")
        completed = ecr.complete_layer_upload(
            repositoryName=repository,
            uploadId=upload_id,
            layerDigests=[digest],
        )
        if completed.get("layerDigest") != digest:
            raise OciPublicationRefusal("ECR_COMPLETED_DIGEST_DIFFERS", f"ECR completed digest differs: {digest}")
        uploaded.append({"digest": digest, "bytes": descriptor["size"], "parts": part_count})

    manifests: list[dict[str, Any]] = []
    for digest, manifest, kind in layout.manifest_sequence():
        # Content-addressing is over the exact exported bytes, not re-serialized JSON.
        body = layout.blob_path(digest).read_text(encoding="utf-8")
        parameters: dict[str, Any] = {
            "repositoryName": repository,
            "imageManifest": body,
            "imageManifestMediaType": manifest["mediaType"],
            "imageDigest": digest,
        }
        if kind == "index":
            parameters["imageTag"] = tag
        response = ecr.put_image(**parameters)
        returned = response.get("image", {}).get("imageId", {}).get("imageDigest")
        if returned != digest:
            raise OciPublicationRefusal("ECR_MANIFEST_DIGEST_DIFFERS", f"ECR manifest digest differs: {digest}")
        manifests.append({"digest": digest, "tagged": kind == "index"})

    readback = ecr.batch_get_image(
        repositoryName=repository,
        imageIds=[{"imageTag": tag}],
        acceptedMediaTypes=[OCI_INDEX],
    )
    images = readback.get("images", [])
    if len(images) != 1 or images[0].get("imageId", {}).get("imageDigest") != layout.expected_index:
        raise OciPublicationRefusal("ECR_INDEX_READBACK_DIFFERS", "ECR index read-back differs")
    if hashlib.sha256(images[0].get("imageManifest", "").encode()).hexdigest() != _hex_digest(layout.expected_index):
        raise OciPublicationRefusal("ECR_INDEX_BYTES_DIFFER", "ECR index read-back bytes differ")
    return {
        "status": "PASS_EXACT_MULTIPART_ECR_PUBLICATION",
        "oci_verification": verified,
        "part_size_bytes": ECR_PART_BYTES,
        "uploaded_blob_count": len(uploaded),
        "uploaded_bytes": sum(item["bytes"] for item in uploaded),
        "uploaded": uploaded,
        "reused_blob_count": len(reused),
        "reused_digests": sorted(reused),
        "manifest_count": len(manifests),
        "manifests": manifests,
        "index_readback_byte_identical": True,
    }


def verify_distribution_roundtrip(
    registry: str,
    repository: str,
    layout: OciLayout,
) -> dict[str, Any]:
    """Read every object back from an OCI Distribution registry and re-hash it."""
    verified = layout.verify()
    checked: list[dict[str, Any]] = []
    manifest_digests = {layout.expected_index, layout.expected_child, layout.expected_attestation}
    accept = f"{OCI_INDEX}, {OCI_MANIFEST}"
    for digest, descriptor in sorted(layout.descriptors.items()):
        kind = "manifest" if digest in manifest_digests else "blob"
        suffix = f"manifests/{digest}" if kind == "manifest" else f"blobs/{digest}"
        request = urllib.request.Request(
            f"{registry.rstrip('/')}/v2/{repository}/{suffix}",
            headers={"Accept": accept},
        )
        measured = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(request, timeout=120) as response:
            while True:
                block = response.read(8 * 1024 * 1024)
                if not block:
                    break
                measured.update(block)
                size += len(block)
            header_digest = response.headers.get("Docker-Content-Digest")
        if measured.hexdigest() != _hex_digest(digest) or size != descriptor["size"]:
            raise OciPublicationRefusal("REGISTRY_ROUNDTRIP_BYTES_DIFFER", f"registry round-trip bytes differ: {digest}")
        if kind == "manifest" and header_digest != digest:
            raise OciPublicationRefusal("REGISTRY_MANIFEST_HEADER_DIFFERS", f"registry manifest header differs: {digest}")
        checked.append({"digest": digest, "bytes": size, "kind": kind})
    return {
        "status": "PASS_EXACT_LOCAL_REGISTRY_ROUNDTRIP",
        "registry": registry,
        "repository": repository,
        "oci_verification": verified,
        "objects_read_back": len(checked),
        "bytes_read_back": sum(item["bytes"] for item in checked),
        "all_content_digests_match": True,
        "objects": checked,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--child", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--attestation", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--repository")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        layout = OciLayout(
            args.layout,
            expected_index=args.index,
            expected_child=args.child,
            expected_config=args.config,
            expected_attestation=args.attestation,
        )
        if args.registry or args.repository:
            if not args.registry or not args.repository:
                raise OciPublicationRefusal("REGISTRY_ARGUMENTS_INCOMPLETE", "registry and repository are both required")
            result = verify_distribution_roundtrip(args.registry, args.repository, layout)
        else:
            result = layout.verify()
        encoded = canonical_json(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(encoded)
        print(encoded.decode(), end="")
        return 0
    except Exception as exc:
        code = getattr(exc, "reason_code", "UNEXPECTED_OCI_EXCEPTION")
        print(json.dumps({"status": "REFUSED", "reason_code": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
