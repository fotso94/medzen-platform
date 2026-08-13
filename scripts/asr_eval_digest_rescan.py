#!/usr/bin/env python3
"""Reconstruct one exact ECR child image and enforce its two-part scan gate."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Any, Callable

from scripts.asr_external_tool import ExternalToolTimeout, run_external


OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
SCOUT_VERSION = "1.18.3"
SCOUT_GIT_COMMIT = "aa68fc25c596bea659d54867443238fd30218d23"
EXPECTED_HIGH_TUPLES = {
    ("CVE-2026-24747", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2026-4538", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2025-55552", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2025-55551", "torch", "2.8.0+cu128", "HIGH"),
}
SHA256_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
PURL_RE = re.compile(r"^pkg:pypi/([^@]+)@(.+)$")


class DigestRescanRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def validate_security_binding(value: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "registry_scanning_mutation_permitted": False,
        "inspector_enhanced_scanning_permitted": False,
        "docker_scout_version": SCOUT_VERSION,
        "docker_scout_git_commit": SCOUT_GIT_COMMIT,
        "accepted_high_tuples": ["|".join(item) for item in sorted(EXPECTED_HIGH_TUPLES)],
    }
    if value != expected:
        raise DigestRescanRefusal(
            "SECURITY_GATE_BINDING_DIFFERS",
            "packet security-gate binding differs from the executable gate",
        )
    return {"status": "PASS_EXACT_SECURITY_GATE_BINDING", **expected}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def _hex(digest: str) -> str:
    matched = SHA256_RE.fullmatch(digest)
    if matched is None:
        raise DigestRescanRefusal("ECR_RESCAN_DIGEST_MALFORMED", "bound ECR digest is malformed")
    return matched.group(1)


def _manifest_bytes(response: dict[str, Any], expected_digest: str) -> tuple[bytes, str]:
    images = response.get("images", [])
    if response.get("failures") or len(images) != 1:
        raise DigestRescanRefusal("ECR_RESCAN_MANIFEST_ABSENT", "exact ECR manifest is absent or ambiguous")
    image = images[0]
    returned = image.get("imageId", {}).get("imageDigest")
    body = image.get("imageManifest")
    media_type = image.get("imageManifestMediaType")
    if returned != expected_digest or not isinstance(body, str) or not isinstance(media_type, str):
        raise DigestRescanRefusal("ECR_RESCAN_MANIFEST_IDENTITY_DIFFERS", "ECR manifest identity differs")
    raw = body.encode()
    if hashlib.sha256(raw).hexdigest() != _hex(expected_digest):
        raise DigestRescanRefusal("ECR_RESCAN_MANIFEST_BYTES_DIFFER", "ECR manifest bytes do not hash to the bound digest")
    return raw, media_type


def _descriptor(descriptor: dict[str, Any]) -> tuple[str, int]:
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if not isinstance(digest, str) or isinstance(size, bool) or not isinstance(size, int):
        raise DigestRescanRefusal("ECR_RESCAN_DESCRIPTOR_MALFORMED", "ECR descriptor is malformed")
    _hex(digest)
    return digest, size


def _write_blob(root: Path, descriptor: dict[str, Any], raw: bytes) -> Path:
    digest, size = _descriptor(descriptor)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != _hex(digest):
        raise DigestRescanRefusal("ECR_RESCAN_BLOB_BYTES_DIFFER", f"downloaded ECR bytes differ: {digest}")
    path = root / "blobs" / "sha256" / _hex(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _default_download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=900) as response, destination.open("xb") as stream:
        while True:
            block = response.read(8 * 1024 * 1024)
            if not block:
                return
            stream.write(block)


def _download_blob(
    root: Path,
    descriptor: dict[str, Any],
    url: str,
    downloader: Callable[[str, Path], None],
) -> Path:
    digest, size = _descriptor(descriptor)
    path = root / "blobs" / "sha256" / _hex(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    downloader(url, path)
    measured = hashlib.sha256()
    measured_size = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(8 * 1024 * 1024)
            if not block:
                break
            measured.update(block)
            measured_size += len(block)
    if measured_size != size or measured.hexdigest() != _hex(digest):
        raise DigestRescanRefusal("ECR_RESCAN_BLOB_BYTES_DIFFER", f"downloaded ECR bytes differ: {digest}")
    return path


class _VerifiedStream:
    """Hash and count a descriptor while tarfile consumes it once."""

    def __init__(self, stream: Any, digest: str, size: int):
        self.stream = stream
        self.expected_digest = _hex(digest)
        self.expected_size = size
        self.measured = hashlib.sha256()
        self.measured_size = 0

    def read(self, size: int = -1) -> bytes:
        block = self.stream.read(size)
        if block:
            self.measured.update(block)
            self.measured_size += len(block)
        return block

    def verify(self, digest: str) -> None:
        if (
            self.measured_size != self.expected_size
            or self.measured.hexdigest() != self.expected_digest
        ):
            raise DigestRescanRefusal(
                "ECR_RESCAN_BLOB_BYTES_DIFFER",
                f"streamed ECR bytes differ: {digest}",
            )


def _default_opener(url: str) -> Any:
    return urllib.request.urlopen(url, timeout=900)


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.mtime = 0
    return info


def _add_bytes(archive: tarfile.TarFile, name: str, raw: bytes) -> None:
    archive.addfile(_tar_info(name, len(raw)), io.BytesIO(raw))


def create_verified_docker_archive_from_ecr(
    ecr: Any,
    repository: str,
    image: dict[str, Any],
    archive_path: Path,
    *,
    opener: Callable[[str], Any] = _default_opener,
) -> dict[str, Any]:
    """Stream one byte-verified ECR child directly into one Docker archive.

    No OCI layout is materialized. At all times the archive is the only full
    local image representation; each remote descriptor is consumed once and
    hash-verified as tarfile writes it.
    """
    if archive_path.exists():
        raise DigestRescanRefusal(
            "SCOUT_ARCHIVE_ALREADY_EXISTS", "exact-child archive path already exists"
        )
    tagged = ecr.batch_get_image(
        repositoryName=repository,
        imageIds=[{"imageTag": image["tag"]}],
        acceptedMediaTypes=[OCI_INDEX],
    )
    tagged_images = tagged.get("images", [])
    if (
        tagged.get("failures")
        or len(tagged_images) != 1
        or tagged_images[0].get("imageId", {}).get("imageDigest")
        != image["oci_index_digest"]
    ):
        raise DigestRescanRefusal(
            "ECR_RESCAN_TAG_BINDING_DIFFERS",
            "immutable ECR tag does not select the bound index",
        )
    index_raw, index_media = _manifest_bytes(
        ecr.batch_get_image(
            repositoryName=repository,
            imageIds=[{"imageDigest": image["oci_index_digest"]}],
            acceptedMediaTypes=[OCI_INDEX],
        ),
        image["oci_index_digest"],
    )
    if index_media != OCI_INDEX:
        raise DigestRescanRefusal(
            "ECR_RESCAN_INDEX_MEDIA_TYPE_DIFFERS", "ECR index media type differs"
        )
    source_index = json.loads(index_raw)
    children = source_index.get("manifests", [])
    linux = [item for item in children if item.get("digest") == image["linux_amd64_digest"]]
    attestation = [item for item in children if item.get("digest") == image["attestation_digest"]]
    if len(linux) != 1 or linux[0].get("platform") != {"architecture": "amd64", "os": "linux"}:
        raise DigestRescanRefusal(
            "ECR_RESCAN_CHILD_BINDING_DIFFERS", "bound linux/amd64 child is absent or ambiguous"
        )
    if len(attestation) != 1:
        raise DigestRescanRefusal(
            "ECR_RESCAN_ATTESTATION_BINDING_DIFFERS", "bound attestation is absent or ambiguous"
        )
    attestation_raw, attestation_media = _manifest_bytes(
        ecr.batch_get_image(
            repositoryName=repository,
            imageIds=[{"imageDigest": image["attestation_digest"]}],
            acceptedMediaTypes=[OCI_MANIFEST],
        ),
        image["attestation_digest"],
    )
    if attestation_media != OCI_MANIFEST or len(attestation_raw) != attestation[0].get("size"):
        raise DigestRescanRefusal(
            "ECR_RESCAN_ATTESTATION_BYTES_DIFFER", "bound attestation bytes or media type differ"
        )
    child_raw, child_media = _manifest_bytes(
        ecr.batch_get_image(
            repositoryName=repository,
            imageIds=[{"imageDigest": image["linux_amd64_digest"]}],
            acceptedMediaTypes=[OCI_MANIFEST],
        ),
        image["linux_amd64_digest"],
    )
    if child_media != OCI_MANIFEST or len(child_raw) != linux[0].get("size"):
        raise DigestRescanRefusal(
            "ECR_RESCAN_CHILD_BYTES_DIFFER", "bound child bytes or media type differ"
        )
    child = json.loads(child_raw)
    config = child.get("config")
    layers = child.get("layers")
    if not isinstance(config, dict) or not isinstance(layers, list) or any(
        not isinstance(item, dict) for item in layers
    ):
        raise DigestRescanRefusal(
            "ECR_RESCAN_DESCRIPTOR_MALFORMED", "child config or layers are absent"
        )
    if config.get("digest") != image["config_digest"]:
        raise DigestRescanRefusal(
            "ECR_RESCAN_CONFIG_BINDING_DIFFERS", "bound config digest differs"
        )
    descriptors = [config, *layers]
    root_index = json.dumps(
        {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [linux[0]]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    docker_manifest = json.dumps(
        [{
            "Config": f"blobs/sha256/{_hex(image['config_digest'])}",
            "RepoTags": [image["local_tag"]],
            "Layers": [f"blobs/sha256/{_hex(item['digest'])}" for item in layers],
        }],
        separators=(",", ":"),
    ).encode()
    downloaded: list[dict[str, Any]] = []
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive_path.open("xb") as raw_archive, tarfile.open(
            fileobj=raw_archive, mode="w|", format=tarfile.PAX_FORMAT
        ) as archive:
            _add_bytes(archive, "oci-layout", b'{"imageLayoutVersion":"1.0.0"}\n')
            _add_bytes(archive, "index.json", root_index)
            _add_bytes(archive, "manifest.json", docker_manifest)
            _add_bytes(archive, f"blobs/sha256/{_hex(image['linux_amd64_digest'])}", child_raw)
            for descriptor in descriptors:
                digest, size = _descriptor(descriptor)
                response = ecr.get_download_url_for_layer(
                    repositoryName=repository, layerDigest=digest
                )
                url = response.get("downloadUrl")
                if not isinstance(url, str) or not url:
                    raise DigestRescanRefusal(
                        "ECR_RESCAN_DOWNLOAD_URL_ABSENT", "ECR layer download URL is absent"
                    )
                with opener(url) as source:
                    verified = _VerifiedStream(source, digest, size)
                    archive.addfile(
                        _tar_info(f"blobs/sha256/{_hex(digest)}", size), verified
                    )
                    # Tar consumes exactly the descriptor size. Read once more
                    # so a server returning an unexpected trailing payload also
                    # fails the byte-identity gate.
                    verified.read(1)
                    verified.verify(digest)
                downloaded.append({"digest": digest, "bytes": size})
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return {
        "status": "PASS_SINGLE_REPRESENTATION_EXACT_DOCKER_ARCHIVE",
        "source_index_digest": image["oci_index_digest"],
        "child_digest": image["linux_amd64_digest"],
        "config_digest": image["config_digest"],
        "attestation_digest_bound_in_source_index": image["attestation_digest"],
        "attestation_manifest_byte_verified": True,
        "streamed_descriptor_count": len(downloaded),
        "streamed_descriptor_bytes": sum(item["bytes"] for item in downloaded),
        "all_streamed_descriptors_byte_verified": True,
        "archive_bytes": archive_path.stat().st_size,
        "simultaneous_full_image_representations": 1,
        "oci_layout_materialized": False,
    }


def reconstruct_exact_child(
    ecr: Any,
    repository: str,
    image: dict[str, Any],
    destination: Path,
    *,
    downloader: Callable[[str, Path], None] = _default_download,
) -> dict[str, Any]:
    """Create a verified OCI layout containing only the bound linux/amd64 child."""
    destination.mkdir(parents=True, exist_ok=False)
    tagged = ecr.batch_get_image(
        repositoryName=repository,
        imageIds=[{"imageTag": image["tag"]}],
        acceptedMediaTypes=[OCI_INDEX],
    )
    tagged_images = tagged.get("images", [])
    if tagged.get("failures") or len(tagged_images) != 1 or tagged_images[0].get("imageId", {}).get("imageDigest") != image["oci_index_digest"]:
        raise DigestRescanRefusal("ECR_RESCAN_TAG_BINDING_DIFFERS", "immutable ECR tag does not select the bound index")
    index_raw, index_media = _manifest_bytes(
        ecr.batch_get_image(
            repositoryName=repository,
            imageIds=[{"imageDigest": image["oci_index_digest"]}],
            acceptedMediaTypes=[OCI_INDEX],
        ),
        image["oci_index_digest"],
    )
    if index_media != OCI_INDEX:
        raise DigestRescanRefusal("ECR_RESCAN_INDEX_MEDIA_TYPE_DIFFERS", "ECR index media type differs")
    index = json.loads(index_raw)
    children = index.get("manifests", [])
    linux = [item for item in children if item.get("digest") == image["linux_amd64_digest"]]
    attestation = [item for item in children if item.get("digest") == image["attestation_digest"]]
    if len(linux) != 1 or linux[0].get("platform") != {"architecture": "amd64", "os": "linux"}:
        raise DigestRescanRefusal("ECR_RESCAN_CHILD_BINDING_DIFFERS", "bound linux/amd64 child is absent or ambiguous")
    if len(attestation) != 1:
        raise DigestRescanRefusal("ECR_RESCAN_ATTESTATION_BINDING_DIFFERS", "bound attestation is absent or ambiguous")
    attestation_raw, attestation_media = _manifest_bytes(
        ecr.batch_get_image(
            repositoryName=repository,
            imageIds=[{"imageDigest": image["attestation_digest"]}],
            acceptedMediaTypes=[OCI_MANIFEST],
        ),
        image["attestation_digest"],
    )
    if attestation_media != OCI_MANIFEST or len(attestation_raw) != attestation[0].get("size"):
        raise DigestRescanRefusal("ECR_RESCAN_ATTESTATION_BYTES_DIFFER", "bound attestation bytes or media type differ")
    child_raw, child_media = _manifest_bytes(
        ecr.batch_get_image(
            repositoryName=repository,
            imageIds=[{"imageDigest": image["linux_amd64_digest"]}],
            acceptedMediaTypes=[OCI_MANIFEST],
        ),
        image["linux_amd64_digest"],
    )
    if child_media != OCI_MANIFEST or len(child_raw) != linux[0].get("size"):
        raise DigestRescanRefusal("ECR_RESCAN_CHILD_BYTES_DIFFER", "bound child bytes or media type differ")
    child = json.loads(child_raw)
    if child.get("config", {}).get("digest") != image["config_digest"]:
        raise DigestRescanRefusal("ECR_RESCAN_CONFIG_BINDING_DIFFERS", "bound config digest differs")
    descriptors = [child.get("config"), *child.get("layers", [])]
    if not descriptors or any(not isinstance(item, dict) for item in descriptors):
        raise DigestRescanRefusal("ECR_RESCAN_DESCRIPTOR_MALFORMED", "child config or layers are absent")
    (destination / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n', encoding="utf-8")
    _write_blob(destination, linux[0], child_raw)
    downloaded = []
    for descriptor in descriptors:
        response = ecr.get_download_url_for_layer(
            repositoryName=repository,
            layerDigest=descriptor["digest"],
        )
        url = response.get("downloadUrl")
        if not isinstance(url, str) or not url:
            raise DigestRescanRefusal("ECR_RESCAN_DOWNLOAD_URL_ABSENT", "ECR layer download URL is absent")
        path = _download_blob(destination, descriptor, url, downloader)
        downloaded.append({"digest": descriptor["digest"], "bytes": path.stat().st_size})
    root = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": [linux[0]],
    }
    (destination / "index.json").write_text(
        json.dumps(root, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return {
        "status": "PASS_EXACT_ECR_CHILD_RECONSTRUCTION",
        "source_index_digest": image["oci_index_digest"],
        "child_digest": image["linux_amd64_digest"],
        "config_digest": image["config_digest"],
        "attestation_digest_bound_in_source_index": image["attestation_digest"],
        "attestation_manifest_byte_verified": True,
        "downloaded_descriptor_count": len(downloaded),
        "downloaded_bytes": sum(item["bytes"] for item in downloaded),
        "all_downloaded_descriptors_byte_verified": True,
    }


def validate_basic_scan(response: dict[str, Any]) -> dict[str, Any]:
    status = response.get("imageScanStatus", {}).get("status")
    findings = response.get("imageScanFindings", {})
    all_findings = (findings.get("enhancedFindings") or findings.get("findings") or [])
    severe = [item for item in all_findings if item.get("severity") in {"CRITICAL", "HIGH"}]
    counts = findings.get("findingSeverityCounts") or {}
    if status != "COMPLETE":
        raise DigestRescanRefusal("ECR_BASIC_SCAN_INCOMPLETE", "supplementary ECR Basic scan is not complete")
    if severe or int(counts.get("CRITICAL", 0)) or int(counts.get("HIGH", 0)):
        raise DigestRescanRefusal("ECR_BASIC_OS_FINDINGS_PRESENT", "supplementary ECR Basic scan has critical/high OS findings")
    return {
        "status": "PASS_ECR_BASIC_OS_GATE",
        "coverage": "OPERATING_SYSTEM_PACKAGES_ONLY",
        "critical": 0,
        "high": 0,
    }


def _pypi_tuple(rule: dict[str, Any]) -> tuple[str, str, str, str]:
    cve = rule.get("id")
    properties = rule.get("properties", {})
    severity = properties.get("cvssV3_severity")
    purls = properties.get("purls")
    if not isinstance(cve, str) or severity not in {"CRITICAL", "HIGH"} or not isinstance(purls, list) or len(purls) != 1:
        raise DigestRescanRefusal("SCOUT_FINDING_MALFORMED", "Docker Scout critical/high finding is malformed")
    match = PURL_RE.fullmatch(purls[0])
    if match is None:
        raise DigestRescanRefusal("SCOUT_FINDING_MALFORMED", "Docker Scout finding is not one exact PyPI package")
    package, version = match.groups()
    return cve, package, version.replace("%2B", "+"), severity


def validate_scout_sarif(value: dict[str, Any]) -> dict[str, Any]:
    runs = value.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise DigestRescanRefusal("SCOUT_SARIF_MALFORMED", "Docker Scout SARIF run is absent or ambiguous")
    run = runs[0]
    rules = run.get("tool", {}).get("driver", {}).get("rules", [])
    by_id = {rule.get("id"): rule for rule in rules if isinstance(rule, dict)}
    results = run.get("results") or []
    tuples = set()
    for result in results:
        if not isinstance(result, dict) or result.get("level") not in {"error", "warning", "note", "none"}:
            raise DigestRescanRefusal("SCOUT_SARIF_MALFORMED", "Docker Scout SARIF result is malformed")
        rule = by_id.get(result.get("ruleId"))
        if rule is None:
            raise DigestRescanRefusal("SCOUT_SARIF_RULE_ABSENT", "Docker Scout SARIF result rule is absent")
        severity = rule.get("properties", {}).get("cvssV3_severity")
        if severity in {"CRITICAL", "HIGH"}:
            tuples.add(_pypi_tuple(rule))
    if tuples != EXPECTED_HIGH_TUPLES:
        raise DigestRescanRefusal("SCOUT_FINDINGS_DIFFER", "Docker Scout critical/high tuple set differs")
    return {
        "status": "PASS_DOCKER_SCOUT_ACCEPTED_RISK_GATE",
        "scanner": "Docker Scout",
        "scanner_version": SCOUT_VERSION,
        "scanner_git_commit": SCOUT_GIT_COMMIT,
        "critical": 0,
        "high": 4,
        "high_tuples": ["|".join(item) for item in sorted(tuples)],
    }


def validate_scout_prerequisites(
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    command = ["docker", "scout", "version"]
    if runner is subprocess.run:
        version, _ = run_external(command, timeout=60)
    else:
        version = runner(command, capture_output=True, check=False, timeout=60)
    version_text = (version.stdout + version.stderr).decode(errors="replace")
    if (
        version.returncode != 0
        or f"version: v{SCOUT_VERSION} " not in version_text
        or f"git commit: {SCOUT_GIT_COMMIT}" not in version_text
    ):
        raise DigestRescanRefusal(
            "SCOUT_VERSION_DIFFERS",
            "pinned Docker Scout version and source commit are unavailable",
        )
    if not (
        os.environ.get("DOCKER_SCOUT_HUB_USER")
        and os.environ.get("DOCKER_SCOUT_HUB_PASSWORD")
    ):
        raise DigestRescanRefusal(
            "SCOUT_AUTHENTICATION_ABSENT",
            "pinned Docker Scout credentials are absent from the execution environment",
        )
    return {
        "status": "PASS_SCOUT_PREREQUISITES",
        "scanner_version": SCOUT_VERSION,
        "scanner_git_commit": SCOUT_GIT_COMMIT,
        "credentials_present": True,
        "credentials_persisted": False,
    }


def run_scout(
    layout: Path,
    output: Path,
    image: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    diagnostics_path: Path | None = None,
) -> dict[str, Any]:
    prerequisites = validate_scout_prerequisites(runner=runner)
    archive = output.parent / "exact-ecr-child.docker.tar"
    docker_archive = create_docker_archive(layout, archive, image)
    scan = scan_archive_with_scout(
        archive,
        output,
        runner=runner,
        diagnostics_path=diagnostics_path,
    )
    return {
        **scan,
        "scanned_oci_layout": str(layout),
        "artifact_mode": "DOCKER_ARCHIVE_OF_DIGEST_VERIFIED_ECR_CHILD",
        "remote_reconstruction": {
            "path": str(layout),
            "verified_before_local_archive": True,
        },
        "docker_archive": docker_archive,
        "prerequisites": prerequisites,
    }


def scan_archive_with_scout(
    archive: Path,
    output: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    diagnostics_path: Path | None = None,
) -> dict[str, Any]:
    """Execute the pinned scanner and retain a bounded diagnostic on every outcome."""
    command = [
        "docker", "scout", "cves", "--format", "sarif",
        "--only-severity", "critical,high", "--output", str(output),
        f"archive://{archive}",
    ]
    try:
        if runner is subprocess.run:
            completed, diagnostic = run_external(command, timeout=1800)
        else:
            completed = runner(command, capture_output=True, check=False, timeout=1800)
            stdout = completed.stdout or b""
            stderr = completed.stderr or b""
            diagnostic = {
                "status": "PASS" if completed.returncode == 0 else "NONZERO_EXIT",
                "returncode": completed.returncode,
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
            }
    except ExternalToolTimeout as exc:
        diagnostic = exc.diagnostic
        if diagnostics_path is not None:
            diagnostics_path.write_bytes(canonical_json(diagnostic))
        raise DigestRescanRefusal(
            "SCOUT_EXECUTION_TIMEOUT",
            f"Docker Scout exceeded its timeout: {canonical_json(diagnostic).decode().strip()}",
        ) from exc
    if diagnostics_path is not None:
        diagnostics_path.write_bytes(canonical_json(diagnostic))
    if completed.returncode not in {0, 2} or not output.is_file():
        raise DigestRescanRefusal(
            "SCOUT_EXECUTION_REFUSED",
            f"Docker Scout digest rescan did not produce SARIF: {canonical_json(diagnostic).decode().strip()}",
        )
    value = json.loads(output.read_bytes())
    result = validate_scout_sarif(value)
    return {
        **result,
        "sarif_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "scan_mode": "LIVE_DOCKER_SCOUT_CVES",
        "external_tool_diagnostic": diagnostic,
    }


def create_docker_archive(layout: Path, archive: Path, image: dict[str, Any]) -> dict[str, Any]:
    """Add Docker's metadata file without changing any verified image bytes."""
    child = json.loads(
        (layout / "blobs" / "sha256" / image["linux_amd64_digest"].removeprefix("sha256:")).read_bytes()
    )
    child_path = layout / "blobs" / "sha256" / image["linux_amd64_digest"].removeprefix("sha256:")
    if hashlib.sha256(child_path.read_bytes()).hexdigest() != image["linux_amd64_digest"].removeprefix("sha256:"):
        raise DigestRescanRefusal("SCOUT_ARCHIVE_CHILD_BYTES_DIFFER", "ECR child bytes differ")
    config = child.get("config", {})
    layers = child.get("layers")
    if config.get("digest") != image["config_digest"] or not isinstance(layers, list):
        raise DigestRescanRefusal(
            "SCOUT_ARCHIVE_BINDING_DIFFERS", "verified ECR child cannot form the exact Docker archive"
        )
    for descriptor in [config, *layers]:
        digest, size = _descriptor(descriptor)
        path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
        if not path.is_file() or path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != digest.removeprefix("sha256:"):
            raise DigestRescanRefusal("SCOUT_ARCHIVE_PAYLOAD_BYTES_DIFFER", f"Docker archive payload differs: {digest}")
    manifest = [{
        "Config": f"blobs/sha256/{image['config_digest'].removeprefix('sha256:')}",
        "RepoTags": [image["local_tag"]],
        "Layers": [f"blobs/sha256/{item['digest'].removeprefix('sha256:')}" for item in layers],
    }]
    manifest_path = layout / "manifest.json"
    manifest_path.write_bytes(json.dumps(manifest, separators=(",", ":")).encode())
    command = [
        "tar", "-cf", str(archive), "-C", str(layout),
        "oci-layout", "index.json", "manifest.json", "blobs",
    ]
    try:
        completed, diagnostic = run_external(command, timeout=1800)
    except ExternalToolTimeout as exc:
        raise DigestRescanRefusal(
            "SCOUT_ARCHIVE_TIMEOUT", f"Docker archive creation timed out: {canonical_json(exc.diagnostic).decode().strip()}"
        ) from exc
    finally:
        manifest_path.unlink(missing_ok=True)
    if completed.returncode != 0 or not archive.is_file():
        raise DigestRescanRefusal(
            "SCOUT_ARCHIVE_REFUSED", f"Docker archive creation refused: {canonical_json(diagnostic).decode().strip()}"
        )
    return {
        "status": "PASS_EXACT_DOCKER_ARCHIVE",
        "archive_bytes": archive.stat().st_size,
        "child_digest": image["linux_amd64_digest"],
        "config_digest": image["config_digest"],
        "layer_count": len(layers),
        "all_payload_objects_from_verified_ecr_layout": True,
        "generated_metadata_only": ["manifest.json"],
    }


def scan_exact_ecr_child(
    ecr: Any,
    repository: str,
    image: dict[str, Any],
    workdir: Path,
    *,
    downloader: Callable[[str, Path], None] = _default_download,
    scout_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    prerequisites = validate_scout_prerequisites(runner=scout_runner)
    workdir.mkdir(parents=True, exist_ok=True)
    if any(workdir.iterdir()):
        raise DigestRescanRefusal("ECR_RESCAN_WORKDIR_NOT_EMPTY", "digest-rescan work directory is not empty")
    archive = workdir / "exact-ecr-child.docker.tar"

    # Compatibility adapter for tests and callers that supply the historical
    # URL-to-path downloader. Live execution uses the streaming opener and
    # never creates a second full image representation.
    if downloader is _default_download:
        opener = _default_opener
    else:
        class _DownloadedBytes:
            def __init__(self, raw: bytes):
                self._stream = io.BytesIO(raw)

            def __enter__(self):
                return self._stream

            def __exit__(self, *_: Any) -> None:
                self._stream.close()

        def opener(url: str) -> Any:
            import tempfile

            with tempfile.TemporaryDirectory(prefix="descriptor-") as temporary:
                path = Path(temporary) / "descriptor"
                downloader(url, path)
                return _DownloadedBytes(path.read_bytes())

    reconstruction = create_verified_docker_archive_from_ecr(
        ecr,
        repository,
        image,
        archive,
        opener=opener,
    )
    basic = validate_basic_scan(
        ecr.describe_image_scan_findings(
            repositoryName=repository,
            imageId={"imageDigest": image["linux_amd64_digest"]},
        )
    )
    scout = scan_archive_with_scout(
        archive,
        workdir / "docker-scout.sarif.json",
        runner=scout_runner,
        diagnostics_path=workdir / "docker-scout-diagnostic.json",
    )
    scout.update({
        "artifact_mode": "SINGLE_STREAMED_DOCKER_ARCHIVE_OF_DIGEST_VERIFIED_ECR_CHILD",
        "docker_archive": {
            "status": reconstruction["status"],
            "archive_bytes": reconstruction["archive_bytes"],
            "child_digest": reconstruction["child_digest"],
            "config_digest": reconstruction["config_digest"],
            "all_payload_objects_stream_verified_from_ecr": True,
            "simultaneous_full_image_representations": 1,
        },
    })
    return {
        "status": "PASS_DIGEST_VERIFIED_DUAL_SCAN_GATE",
        "prerequisites": prerequisites,
        "reconstruction": reconstruction,
        "ecr_basic": basic,
        "docker_scout": scout,
    }
