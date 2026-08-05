from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003B-deployment.md"
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-002.json"
FAILED = ROOT / "platform/evidence/B6A-PACKET-2026-003A-FAILED-IMAGE-SCAN.json"


def test_packet_003b_is_blocked_behind_separate_scan_packet():
    text = PACKET.read_text()
    normalized = " ".join(text.split())
    assert "BLOCKED — NOT AUTHORIZED" in text
    assert "003B is ineligible for approval" in text
    assert "No operation from this packet may be combined with packet 2026-005" in normalized
    assert "linux/amd64` child" in text
    assert "automatic child scan" in normalized
    assert "manual scan is not a substitute" in normalized


def test_packet_binds_clean_images_and_no_loader_gpu():
    text = PACKET.read_text()
    evidence = json.loads(EVIDENCE.read_text())
    for image in evidence["images"].values():
        assert image["local_oci_index_id"] in text
        assert image["docker_scout_critical"] == 0
        assert image["docker_scout_high"] == 0
    assert "loader has no GPU claim" in text
    assert "No serving-plane vulnerability waiver exists" in text


def test_local_evidence_preserves_003a_and_records_zero_aws_mutations():
    evidence = json.loads(EVIDENCE.read_text())
    preserved = {
        item["path"]: item["sha256"]
        for item in evidence["governing_records"]
    }
    path = "platform/evidence/B6A-PACKET-2026-003A-FAILED-IMAGE-SCAN.json"
    assert preserved[path] == hashlib.sha256(FAILED.read_bytes()).hexdigest()
    assert evidence["aws_boundary"]["aws_mutations_during_this_engineering_record"] == 0
    assert evidence["aws_boundary"]["ecr_images_pushed"] == 0
    assert evidence["aws_boundary"]["gpu_hours"] == 0


def test_nonpromotion_and_budget_boundaries_are_explicit():
    text = PACKET.read_text()
    for required in (
        "Approved ASR writes: `0`",
        "Registered models/model versions: `0 / 0`",
        "Production SSM changes: `0`",
        "B5 BLOCKED report: unchanged",
        "never B6.1 or",
        "does not create a second `$15` reservation",
    ):
        assert required in text
