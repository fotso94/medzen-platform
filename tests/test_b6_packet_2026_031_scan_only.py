from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6-WEBSOCKET-RUNTIME-LOCAL-QUALIFICATION-2026-001.json"
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-031-orchestrator-websocket-scan-only.md"


def sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def git_sha(commit: str, relative: str) -> str:
    content = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(content).hexdigest()


def test_local_evidence_binds_exact_image_runtime_handshake_and_scan():
    evidence = json.loads(EVIDENCE.read_bytes())
    assert evidence["status"] == "PASS_LOCAL_ECR_SCAN_NOT_AUTHORIZED"
    source = evidence["source"]
    for field, relative in {
        "dockerfile_sha256": "services/speech-orchestrator/Dockerfile",
        "requirements_sha256": "services/speech-orchestrator/requirements.txt",
        "deployed_requirements_sha256": (
            "services/speech-orchestrator/requirements.deployed.txt"
        ),
        "qualification_checker_sha256": "scripts/check_b6_service_image.py",
        "qualification_builder_sha256": (
            "scripts/build_b6_orchestrator_websocket_image.sh"
        ),
        "hardening_standard_v2_sha256": (
            "platform/standards/runtime-image-hardening-v2.md"
        ),
    }.items():
        assert source[field] == git_sha(source["git_commit"], relative)
    image = evidence["image"]
    assert image["local_deployable_child"] == (
        "sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed"
    )
    for receipt in ("runtime_receipt", "local_scan_receipt"):
        assert sha(image[receipt]["path"]) == image[receipt]["sha256"]
    runtime = json.loads((ROOT / image["runtime_receipt"]["path"]).read_bytes())
    assert runtime["runtime_smoke"]["websocket_backend"] == "17.0.1"
    assert runtime["websocket_handshake"] == {
        "container_read_only": True,
        "fixture_mounts": "read_only_synthetic_only",
        "http_status": 101,
        "network_binding": "loopback_ephemeral",
        "path": "/v1/conversations/stream",
        "protocol": "RFC6455",
        "status": "PASS",
        "transport": "real_tcp",
    }
    sarif = json.loads((ROOT / image["local_scan_receipt"]["path"]).read_bytes())
    assert all(run.get("results", []) == [] for run in sarif["runs"])
    assert evidence["tests"]["canonical_local_suite"] == {
        "passed": 1504,
        "failed": 0,
        "skipped": 0,
        "deselected": 7,
        "warnings": 1,
    }
    assert all(value == 0 for value in evidence["aws_boundary"].values())


def test_packet_is_one_image_scan_only_and_requires_exact_approval():
    packet = PACKET.read_text()
    normalized = " ".join(packet.split())
    assert "Status: **DRAFT — AWAITING INDEPENDENT REVIEW" in packet
    assert "Approve B6 AWS change packet 2026-031 only." in packet
    assert "No scanner-rule update is authorized or needed." in packet
    assert "No waiver is permitted." in packet
    assert "Only `PASS_SCAN_ONLY` permits" in packet
    assert "authorizes no deployment by itself" in normalized
    assert "New reservation | `$0.00`" in packet
    assert "CPU and GPU desired capacity remain zero throughout" in normalized
    assert "querying by the child digest rather than the tag or OCI index" in normalized


def test_successor_window_scope_preserves_file_pass_and_only_remaining_proofs():
    packet = PACKET.read_text()
    normalized = " ".join(packet.split())
    assert "preserve the immutable 2026-030A file-proof PASS without rerunning it" in packet
    assert "streaming, cancellation," in packet
    assert "failure drills and isolation" in packet
    assert "two fresh, non-transferable 4,500-second attempts" in packet
    assert "within the existing `$10` reservation" in normalized
