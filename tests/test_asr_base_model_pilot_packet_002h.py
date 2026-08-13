from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_integrity import (
    ATTEMPT_9_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan
from scripts.asr_base_model_pilot_staging import validate_prestage_proof


PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002H-attempt-9.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002H.json"
PROOF = ROOT / "platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002G-ATTEMPT-8-ARTIFACT-STAGING-STALL-REFUSAL.json"
RISK = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_packet_is_non_executable_attempt_nine_only() -> None:
    text = PACKET.read_text(encoding="utf-8")
    value = bound()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002H only" in text
    assert value["attempts"]["authorized_numbers"] == [9]
    assert value["attempts"]["maximum"] == 1
    assert value["attempts"]["seconds_each"] == 10800
    assert value["attempts"]["non_transferable"] is True
    assert value["attempts"]["attempts_1_through_8_reuse_permitted"] is False


def test_attempt_eight_and_risk_history_are_unchanged() -> None:
    value = bound()
    assert value["write_once_history"]["attempt_8_refusal"]["sha256"] == sha(REFUSAL)
    assert value["risk_acceptance_sha256"] == sha(RISK)
    for item in value["write_once_history"].values():
        assert sha(ROOT / item["path"]) == item["sha256"]


def test_complete_prestage_proof_is_committed_and_bound() -> None:
    value = bound()
    proof = json.loads(PROOF.read_bytes())
    assert value["artifact_prestage_proof"]["sha256"] == sha(PROOF)
    result = validate_prestage_proof(
        proof, expected_bundle_sha256=value["pilot_bundle"]["sha256"]
    )
    assert result == {
        "status": "PASS_PRESTAGE_PROOF_STRUCTURE",
        "object_count": 9,
        "object_bytes": 13116686091,
        "bundle_identity_sha256": value["pilot_bundle"]["sha256"],
    }
    assert proof["timed_window"]["artifact_stage_mode"] == "VERIFY_ONLY"
    assert proof["timed_window"]["in_attempt_upload_bytes"] == 0
    assert proof["scope"]["gpu_started"] is False
    assert proof["scope"]["endpoints_created"] == 0


def test_attempt_nine_binds_every_current_executor_module() -> None:
    value = bound()
    # Historical 002H bindings remain write-once. Validate them against the
    # exact reviewed source tree rather than the attempt-10 successor bytes.
    reviewed = value["executor_source_commit"]
    for relative in ATTEMPT_9_EXECUTOR_MODULE_PATHS:
        completed = subprocess.run(
            ["git", "show", f"{reviewed}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        assert hashlib.sha256(completed.stdout).hexdigest() == value["executor_modules"][relative]


def test_attempt_nine_plan_has_no_artifact_creation() -> None:
    value = bound()
    plan = exact_plan(value, 9)
    assert plan["permanent_create_only"] == []
    assert "s3:" + value["pilot_bundle"]["s3_prefix"].removeprefix("s3://") + "**" in plan["read_only_existing"]


def test_packet_binds_prestage_refusal_and_risk_hashes() -> None:
    text = PACKET.read_text(encoding="utf-8")
    for path in (PROOF, REFUSAL, RISK):
        assert sha(path) in text


def test_cold_rehearsal_defaults_to_current_committed_bindings() -> None:
    text = (ROOT / "scripts/asr_base_model_pilot_cold_rehearsal.py").read_text(
        encoding="utf-8"
    )
    assert text.count("ASR-BASE-MODEL-PILOT-BINDINGS-2026-002J.json") == 2
