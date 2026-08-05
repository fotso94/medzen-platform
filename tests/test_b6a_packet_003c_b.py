from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = (
    ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-B-deployment.md"
)
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-004.json"


def test_packet_003c_b_is_unapproved_and_binds_local_evidence():
    text = PACKET.read_text()
    assert "Status: **BLOCKED — NOT AUTHORIZED**" in text
    assert "Approve B6A AWS change packet 2026-003C-B only." in text
    assert hashlib.sha256(EVIDENCE.read_bytes()).hexdigest() in text


def test_packet_003c_b_binds_scanned_deployable_manifests_not_tags():
    text = PACKET.read_text()
    evidence = json.loads(EVIDENCE.read_text())
    for digest in (
        evidence["deployable_image_identity"]["model_loader_child"],
        evidence["deployable_image_identity"]["asr_runtime_child"],
        evidence["deployable_image_identity"]["nvidia_dra_single_manifest"],
    ):
        assert digest in text
    assert "live-Pod" not in text  # Use durable wording, not commentary shorthand.
    assert "Reverify the running" in text
    assert "model-loader and ASR Pod image digests" in text


def test_packet_003c_b_arms_independent_deadline_before_gpu_and_fails_closed():
    text = PACKET.read_text()
    arm = text.index("**Arm the independent deadline before scale-up.**")
    scale = text.index("**Open the bounded GPU window.**")
    proof = text.index("**Measure and prove the chain.**")
    cleanup = text.index("**Clean up immediately.**")
    assert arm < scale < proof < cleanup
    assert "no later than two hours" in text
    assert "leave the AWS-side action armed" in text
    assert "scheduled action be deleted" in text


def test_packet_003c_b_is_platform_proof_not_promotion():
    text = PACKET.read_text()
    for expected in (
        "the `0.20` absolute gate",
        "No `approved/` path",
        "production_approved: false",
        "Any training",
        "model registration",
        "production SSM change",
        "not B6.1 or full B6",
    ):
        assert expected in text
    assert "B6A_PLATFORM_PROOF_COMPLETE" in text
    assert "BLOCKED_PLATFORM_PROOF" in text
    assert "FAILED_CLOSED_EXECUTION" in text


def test_packet_003c_b_uses_one_synthetic_no_phi_input_and_measures_memory():
    text = PACKET.read_text()
    synthetic = json.loads(
        (ROOT / "platform/testdata/b6a-003c-b-synthetic.json").read_text()
    )
    assert synthetic["wav"]["sha256"] in text
    assert "contains no PHI or clinical content" in text
    assert "timestamped `nvidia-smi` sampling" in text
    assert "`NOT_MEASURED` refuses" in text
    assert "loopback-only" in text
