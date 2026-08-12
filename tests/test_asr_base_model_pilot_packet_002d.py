from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002D-attempt-5.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002D.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002D-COLD/cold-rehearsal.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_is_non_executable_and_requests_only_attempt_5() -> None:
    text = PACKET.read_text()
    assert "DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE" in text
    assert "numbered attempt 5" in text
    assert "one non-transferable 10,800-second" in text
    assert "Attempts 1, 2, 3, and 4 are\nconsumed" in text
    assert "attempt 4" in text and "cannot be reused" in text


def test_bindings_hashes_are_current_and_history_is_write_once() -> None:
    value = json.loads(BINDINGS.read_bytes())
    for section in ("write_once_history",):
        for item in value[section].values():
            assert sha(ROOT / item["path"]) == item["sha256"]
    assert sha(ROOT / value["scanner_capability_diagnosis"]["path"]) == value[
        "scanner_capability_diagnosis"
    ]["sha256"]
    assert sha(ROOT / value["digest_rescan_bindings"]["path"]) == value[
        "digest_rescan_bindings"
    ]["sha256"]
    assert sha(ROOT / value["executor"]["cold_rehearsal_path"]) == value["executor"][
        "cold_rehearsal_receipt_sha256"
    ]
    assert value["risk_acceptance_sha256"] == sha(
        ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json"
    )


def test_packet_binds_exact_security_gate_and_no_registry_mutation() -> None:
    value = json.loads(BINDINGS.read_bytes())
    gate = value["security_gate"]
    assert gate["registry_scanning_mutation_permitted"] is False
    assert gate["inspector_enhanced_scanning_permitted"] is False
    assert gate["docker_scout_version"] == "1.18.3"
    assert len(gate["accepted_high_tuples"]) == 4
    assert value["ecr_existing_image"]["attempt_5_upload_required"] is False
    text = PACKET.read_text()
    assert "no Inspector Enhanced scanning" in text
    assert "skip every image upload" in text


def test_rehearsal_covers_required_scan_paths_and_no_scan_mutation() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL"
    gate = value["attempt_5_security_rehearsal"]
    assert gate["aligned_pass"] is True
    assert gate["wrong_digest_refuses"] is True
    assert gate["extra_finding_refuses"] is True
    assert value["registry_scanning_boundary"]["maximum_put_calls_per_scenario"] == 0


def test_packet_source_hash_table_matches_files() -> None:
    text = PACKET.read_text()
    reviewed_commit = json.loads(BINDINGS.read_bytes())["executor"]["reviewed_source_commit"]
    rows = re.findall(r"\| `([^`]+)` \| `([0-9a-f]{64})` \|", text)
    checked = 0
    for relative, expected in rows:
        path = ROOT / relative
        if path.is_file():
            historical = subprocess.run(
                ["git", "show", f"{reviewed_commit}:{relative}"],
                cwd=ROOT,
                capture_output=True,
                check=True,
            ).stdout
            assert hashlib.sha256(historical).hexdigest() == expected
            checked += 1
    assert checked >= 8
