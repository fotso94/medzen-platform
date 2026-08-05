from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-A-scan-only.md"
)
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-003.json"


def test_packet_is_unapproved_and_binds_local_evidence():
    text = PACKET.read_text()
    evidence_hash = hashlib.sha256(EVIDENCE.read_bytes()).hexdigest()
    assert "Status: **BLOCKED — NOT AUTHORIZED**" in text
    assert "Approve B6A AWS change packet 2026-003C-A only." in text
    assert evidence_hash in text


def test_packet_binds_exact_asr_and_dra_identities():
    text = PACKET.read_text()
    evidence = json.loads(EVIDENCE.read_text())
    asr = evidence["accepted_asr_runtime_image"]
    dra = evidence["nvidia_dra_reverification"]
    for expected in (
        asr["oci_index_digest"],
        asr["linux_amd64_manifest_digest"],
        asr["config_digest"],
        dra["digest"],
        dra["proposed_ecr_tag"],
    ):
        assert expected in text


def test_packet_scans_both_subjects_without_sequential_discovery():
    text = PACKET.read_text()
    assert "two image scans are independent qualification subjects" in text
    assert "does not prevent collecting" in text
    assert "PASS_SCAN_ONLY" in text
    assert "BLOCKED_IMAGE_SCAN" in text
    assert "FAILED_CLOSED_EXECUTION" in text


def test_packet_requires_automatic_scan_and_forbids_manual_scan():
    text = PACKET.read_text()
    assert "SCAN_ON_PUSH" in text
    assert "automatic scan-on-push" in text
    assert "Do not invoke a manual scan" in text
    assert "Calling `ecr:StartImageScan`" in text


def test_scan_only_packet_cannot_deploy_or_start_gpu():
    text = PACKET.read_text()
    for forbidden in (
        "Uploading the zero-shot artifact",
        "Creating or changing IAM",
        "Installing NVIDIA DRA",
        "Scaling a GPU node",
        "Writing under `approved/asr/`",
        "registering a model",
        "changing production SSM",
    ):
        assert forbidden in text
    assert "No outcome from this packet permits deployment" in text
    assert "Only `PASS_SCAN_ONLY` permits" in text
