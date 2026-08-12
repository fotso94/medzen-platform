from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_eval_digest_rescan import (
    DigestRescanRefusal,
    EXPECTED_HIGH_TUPLES,
    OCI_INDEX,
    OCI_MANIFEST,
    reconstruct_exact_child,
    create_docker_archive,
    validate_basic_scan,
    validate_scout_sarif,
    validate_security_binding,
    validate_scout_prerequisites,
)


def blob(value: bytes, media_type: str) -> dict:
    return {
        "digest": "sha256:" + hashlib.sha256(value).hexdigest(),
        "size": len(value),
        "mediaType": media_type,
    }


def manifest(value: dict, media_type: str) -> tuple[dict, bytes]:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return blob(raw, media_type), raw


class FakeEcr:
    def __init__(self) -> None:
        self.config_bytes = b'{"architecture":"amd64","os":"linux"}'
        self.layer_bytes = b"exact-layer-bytes"
        self.config = blob(self.config_bytes, "application/vnd.oci.image.config.v1+json")
        self.layer = blob(self.layer_bytes, "application/vnd.oci.image.layer.v1.tar+gzip")
        child_value = {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "config": self.config,
            "layers": [self.layer],
        }
        child, self.child_raw = manifest(child_value, OCI_MANIFEST)
        self.child = {**child, "platform": {"architecture": "amd64", "os": "linux"}}
        attestation_value = {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": self.config, "layers": []}
        attestation, self.attestation_raw = manifest(attestation_value, OCI_MANIFEST)
        self.attestation = {**attestation, "platform": {"architecture": "unknown", "os": "unknown"}}
        index_value = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [self.child, self.attestation]}
        self.index, self.index_raw = manifest(index_value, OCI_INDEX)
        self.urls = {
            self.config["digest"]: self.config_bytes,
            self.layer["digest"]: self.layer_bytes,
        }

    @property
    def image(self) -> dict:
        return {
            "tag": "pilot-exact",
            "oci_index_digest": self.index["digest"],
            "linux_amd64_digest": self.child["digest"],
            "attestation_digest": self.attestation["digest"],
            "config_digest": self.config["digest"],
        }

    def batch_get_image(self, **kwargs):
        image_id = kwargs["imageIds"][0]
        if "imageTag" in image_id or image_id.get("imageDigest") == self.index["digest"]:
            digest, raw, media = self.index["digest"], self.index_raw, OCI_INDEX
        elif image_id["imageDigest"] == self.child["digest"]:
            digest, raw, media = self.child["digest"], self.child_raw, OCI_MANIFEST
        elif image_id["imageDigest"] == self.attestation["digest"]:
            digest, raw, media = self.attestation["digest"], self.attestation_raw, OCI_MANIFEST
        else:
            return {"images": [], "failures": [{"failureCode": "ImageNotFound"}]}
        return {
            "images": [{
                "imageId": {"imageDigest": digest},
                "imageManifest": raw.decode(),
                "imageManifestMediaType": media,
            }],
            "failures": [],
        }

    def get_download_url_for_layer(self, **kwargs):
        return {"downloadUrl": "memory://" + kwargs["layerDigest"]}

    def download(self, url: str, destination: Path) -> None:
        destination.write_bytes(self.urls[url.removeprefix("memory://")])


def scout_sarif(tuples=EXPECTED_HIGH_TUPLES) -> dict:
    rules = []
    results = []
    for cve, package, version, severity in sorted(tuples):
        rules.append({
            "id": cve,
            "properties": {
                "cvssV3_severity": severity,
                "purls": [f"pkg:pypi/{package}@{version.replace('+', '%2B')}"],
            },
        })
        results.append({"ruleId": cve, "level": "error"})
    return {"runs": [{"tool": {"driver": {"rules": rules}}, "results": results}]}


def test_aligned_digest_reconstruction_and_dual_scan_pass(tmp_path: Path) -> None:
    ecr = FakeEcr()
    result = reconstruct_exact_child(
        ecr, "medzen-asr-eval-runtime", ecr.image, tmp_path / "oci", downloader=ecr.download
    )
    assert result["status"] == "PASS_EXACT_ECR_CHILD_RECONSTRUCTION"
    assert result["all_downloaded_descriptors_byte_verified"] is True
    assert validate_basic_scan({
        "imageScanStatus": {"status": "COMPLETE"},
        "imageScanFindings": {"findings": [], "findingSeverityCounts": {}},
    })["status"] == "PASS_ECR_BASIC_OS_GATE"
    assert validate_scout_sarif(scout_sarif())["status"] == "PASS_DOCKER_SCOUT_ACCEPTED_RISK_GATE"


def test_verified_ecr_child_becomes_a_scout_readable_docker_archive(tmp_path: Path) -> None:
    ecr = FakeEcr()
    image = {**ecr.image, "local_tag": "medzen-asr-eval-runtime:pilot-exact"}
    layout = tmp_path / "oci"
    reconstruct_exact_child(ecr, "medzen-asr-eval-runtime", image, layout, downloader=ecr.download)
    result = create_docker_archive(layout, tmp_path / "exact.tar", image)
    assert result["status"] == "PASS_EXACT_DOCKER_ARCHIVE"
    assert result["child_digest"] == image["linux_amd64_digest"]
    assert result["all_payload_objects_from_verified_ecr_layout"] is True
    assert not (layout / "manifest.json").exists()


def test_docker_archive_refuses_a_corrupt_verified_layout_payload(tmp_path: Path) -> None:
    ecr = FakeEcr()
    image = {**ecr.image, "local_tag": "medzen-asr-eval-runtime:pilot-exact"}
    layout = tmp_path / "oci"
    reconstruct_exact_child(ecr, "medzen-asr-eval-runtime", image, layout, downloader=ecr.download)
    layer = layout / "blobs/sha256" / ecr.layer["digest"].removeprefix("sha256:")
    layer.write_bytes(layer.read_bytes() + b"corrupt")
    with pytest.raises(DigestRescanRefusal) as captured:
        create_docker_archive(layout, tmp_path / "exact.tar", image)
    assert captured.value.reason_code == "SCOUT_ARCHIVE_PAYLOAD_BYTES_DIFFER"


def test_wrong_digest_refuses_before_scout(tmp_path: Path) -> None:
    ecr = FakeEcr()
    binding = {**ecr.image, "linux_amd64_digest": "sha256:" + "0" * 64}
    with pytest.raises(DigestRescanRefusal) as captured:
        reconstruct_exact_child(
            ecr, "medzen-asr-eval-runtime", binding, tmp_path / "oci", downloader=ecr.download
        )
    assert captured.value.reason_code == "ECR_RESCAN_CHILD_BINDING_DIFFERS"


def test_downloaded_layer_digest_drift_refuses(tmp_path: Path) -> None:
    ecr = FakeEcr()

    def corrupt(url: str, destination: Path) -> None:
        ecr.download(url, destination)
        destination.write_bytes(destination.read_bytes() + b"corrupt")

    with pytest.raises(DigestRescanRefusal) as captured:
        reconstruct_exact_child(
            ecr, "medzen-asr-eval-runtime", ecr.image, tmp_path / "oci", downloader=corrupt
        )
    assert captured.value.reason_code == "ECR_RESCAN_BLOB_BYTES_DIFFER"


def test_extra_scout_finding_refuses() -> None:
    extra = set(EXPECTED_HIGH_TUPLES)
    extra.add(("CVE-2099-0001", "example", "1.0.0", "HIGH"))
    with pytest.raises(DigestRescanRefusal) as captured:
        validate_scout_sarif(scout_sarif(extra))
    assert captured.value.reason_code == "SCOUT_FINDINGS_DIFFER"


def test_missing_scout_finding_refuses() -> None:
    missing = set(EXPECTED_HIGH_TUPLES)
    missing.pop()
    with pytest.raises(DigestRescanRefusal) as captured:
        validate_scout_sarif(scout_sarif(missing))
    assert captured.value.reason_code == "SCOUT_FINDINGS_DIFFER"


def test_basic_scan_is_supplementary_zero_severity_gate() -> None:
    with pytest.raises(DigestRescanRefusal) as captured:
        validate_basic_scan({
            "imageScanStatus": {"status": "COMPLETE"},
            "imageScanFindings": {"findings": [], "findingSeverityCounts": {"HIGH": 1}},
        })
    assert captured.value.reason_code == "ECR_BASIC_OS_FINDINGS_PRESENT"


def test_attempt_4_real_basic_scan_response_is_supplementary_os_pass() -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/aws/ecr-describe-image-scan-findings-asr-eval-attempt-4.json").read_bytes()
    )
    assert validate_basic_scan(fixture) == {
        "status": "PASS_ECR_BASIC_OS_GATE",
        "coverage": "OPERATING_SYSTEM_PACKAGES_ONLY",
        "critical": 0,
        "high": 0,
    }


def test_scout_source_commit_drift_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_SCOUT_HUB_USER", "synthetic")
    monkeypatch.setenv("DOCKER_SCOUT_HUB_PASSWORD", "synthetic-secret")
    def runner(command, **kwargs):
        if command[2] == "version":
            return subprocess.CompletedProcess(command, 0, b"version: v1.18.3 (go1.24.6)\ngit commit: wrong\n", b"")
        raise AssertionError("scan must not start when the pinned scanner identity differs")

    with pytest.raises(DigestRescanRefusal) as captured:
        validate_scout_prerequisites(runner=runner)
    assert captured.value.reason_code == "SCOUT_VERSION_DIFFERS"


def test_scout_authentication_absent_refuses_before_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCKER_SCOUT_HUB_USER", raising=False)
    monkeypatch.delenv("DOCKER_SCOUT_HUB_PASSWORD", raising=False)

    def runner(command, **kwargs):
        if command[2] == "version":
            return subprocess.CompletedProcess(
                command,
                0,
                b"version: v1.18.3 (go1.24.6)\ngit commit: aa68fc25c596bea659d54867443238fd30218d23\n",
                b"",
            )
        raise AssertionError("scan must not start without explicit scanner credentials")

    with pytest.raises(DigestRescanRefusal) as captured:
        validate_scout_prerequisites(runner=runner)
    assert captured.value.reason_code == "SCOUT_AUTHENTICATION_ABSENT"


def test_security_binding_is_exact_and_fail_closed() -> None:
    binding = {
        "registry_scanning_mutation_permitted": False,
        "inspector_enhanced_scanning_permitted": False,
        "docker_scout_version": "1.18.3",
        "docker_scout_git_commit": "aa68fc25c596bea659d54867443238fd30218d23",
        "accepted_high_tuples": ["|".join(item) for item in sorted(EXPECTED_HIGH_TUPLES)],
    }
    assert validate_security_binding(binding)["status"] == "PASS_EXACT_SECURITY_GATE_BINDING"
    binding["accepted_high_tuples"].append("CVE-2099-0001|example|1.0.0|HIGH")
    with pytest.raises(DigestRescanRefusal) as captured:
        validate_security_binding(binding)
    assert captured.value.reason_code == "SECURITY_GATE_BINDING_DIFFERS"
