from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6-WEBSOCKET-PARTIAL-SOURCE-LOCAL-QUALIFICATION-2026-001.json"
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-033-streaming-partial-source-scan-only.md"


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


def test_evidence_binds_source_image_receipts_and_both_dependency_outcomes():
    evidence = json.loads(EVIDENCE.read_bytes())
    assert evidence["status"] == "PASS_LOCAL_ECR_SCAN_NOT_AUTHORIZED"
    source = evidence["source"]
    for field, relative in {
        "dockerfile_sha256": "services/speech-orchestrator/Dockerfile",
        "requirements_sha256": "services/speech-orchestrator/requirements.txt",
        "deployed_requirements_sha256": "services/speech-orchestrator/requirements.deployed.txt",
        "streaming_core_sha256": "services/speech-orchestrator/medzen_speech_orchestrator/streaming.py",
        "streaming_app_sha256": "services/speech-orchestrator/medzen_speech_orchestrator/streaming_app.py",
        "window_probe_sha256": "scripts/b6_6_probe.py",
        "qualification_checker_sha256": "scripts/check_b6_service_image.py",
        "qualification_builder_sha256": "scripts/build_b6_orchestrator_websocket_image.sh",
        "remediation_decision_sha256": "platform/decisions/B6-WEBSOCKET-PARTIAL-SOURCE-REMEDIATION-2026-001.json",
    }.items():
        assert source[field] == git_sha(source["git_commit"], relative)

    image = evidence["image"]
    assert image["local_deployable_child"] == "sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3"
    for receipt in ("runtime_receipt", "local_scan_receipt"):
        assert sha(image[receipt]["path"]) == image[receipt]["sha256"]

    runtime = json.loads((ROOT / image["runtime_receipt"]["path"]).read_bytes())
    assert runtime["websocket_dependency_gate"] == {
        "application_started": True,
        "dependency": "streaming_partial_source",
        "http_status": 503,
        "reason_code": "STREAMING_PARTIAL_SOURCE_UNAVAILABLE",
        "status": "PASS_FAIL_CLOSED",
    }
    conversation = runtime["websocket_conversation"]
    assert conversation["status"] == "PASS"
    assert conversation["stable_conversation_passes"] == 3
    assert conversation["final_result_preserved"] is True
    assert conversation["partial_queue_limit"] == 4
    assert conversation["audio_queue_limit"] == 8
    assert conversation["probe_app_binding"]["pair_sha256"] == "f6c8eb872cbd80c5542350e0c4ac5c0b1cff82d820d94ab452ef12cba816a9d6"

    sarif = json.loads((ROOT / image["local_scan_receipt"]["path"]).read_bytes())
    assert all(run.get("results", []) == [] for run in sarif["runs"])
    assert evidence["tests"]["canonical_local_suite"] == {
        "passed": 1528,
        "failed": 0,
        "skipped": 0,
        "deselected": 7,
        "warnings": 1,
    }
    assert all(value == 0 for value in evidence["aws_boundary"].values())


def test_packet_preserves_fail_closed_meaning_and_requires_dependency_ready_pass():
    packet = PACKET.read_text()
    normalized = " ".join(packet.split())
    assert "Approve B6 AWS change packet 2026-033 only." in packet
    assert "Code `4503` remains the correct fail-closed response" in packet
    assert "cannot satisfy the streamed-conversation milestone" in normalized
    assert "dependency-ready full conversation to pass" in normalized
    assert "three consecutive times" in packet


def test_packet_is_one_image_scan_only_with_no_compute_or_new_reservation():
    packet = PACKET.read_text()
    normalized = " ".join(packet.split())
    assert "Status: **DRAFT — AWAITING INDEPENDENT REVIEW" in packet
    assert "This packet is scan-only" in packet
    assert "No waiver is permitted" in packet
    assert "querying the child digest" in normalized
    assert "New reservation | `$0.00`" in packet
    assert "CPU and GPU remain at zero throughout" in normalized
    assert "grants no window attempt or compute allowance" in normalized
    assert "fresh owner allowance decision is required" in normalized
