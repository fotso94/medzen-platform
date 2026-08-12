from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_eval_digest_rescan import validate_security_binding


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002E-A1.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002E-A1-COLD/cold-rehearsal.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002E-A1-attempt-6.md"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002E-ATTEMPT-6-PRE-AWS-PREREQUISITE-REFUSAL.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_actual_committed_top_level_gate_is_the_live_exact_gate() -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    digest = json.loads((ROOT / bindings["digest_rescan_bindings"]["path"]).read_bytes())
    assert bindings["security_gate"] == digest["security_gate"]
    assert validate_security_binding(bindings["security_gate"])["status"] == (
        "PASS_EXACT_SECURITY_GATE_BINDING"
    )


def test_rehearsal_used_actual_binding_without_normalization() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["bindings_source"] == {
        "fixture_used": False,
        "loaded_from_committed_head": True,
        "path": str(BINDINGS.relative_to(ROOT)),
        "sha256": _sha(BINDINGS),
    }
    assert receipt["rehearsal_binding_normalization_permitted"] is False
    assert receipt["security_gate_validation"]["status"] == "PASS_EXACT_SECURITY_GATE_BINDING"
    assert receipt["scenarios"]["clean_pass"]["outcome"] == "PASS_PILOT"


def test_rehearsal_is_byte_deterministic_from_clean_commit(tmp_path: Path) -> None:
    reviewed_commit = "dcfea9a61345cf605813c4cbfba1d2cbdc50bfe2"
    committed = subprocess.run(
        ["git", "show", f"{reviewed_commit}:{BINDINGS.relative_to(ROOT)}"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    assert hashlib.sha256(committed).hexdigest() == _sha(BINDINGS)
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL"
    assert value["rehearsal_binding_normalization_permitted"] is False
    assert value["scenarios"]["clean_pass"]["outcome"] == "PASS_PILOT"


def test_attempt_six_continuity_and_zero_aws_refusal_are_bound() -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    refusal = json.loads(REFUSAL.read_bytes())
    assert bindings["attempts"]["authorized_numbers"] == [6]
    assert refusal["attempt_started"] is False
    assert refusal["attempt_consumed"] is False
    assert refusal["boundary_proof"]["aws_calls_by_runner"] == 0
    assert refusal["boundary_proof"]["gpu_desired"] == 0
    assert bindings["write_once_history"]["attempt_6_pre_aws_refusal"]["sha256"] == _sha(REFUSAL)


def test_packet_is_not_executable_and_requires_a_fresh_complete_dry_run() -> None:
    text = PACKET.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" in text
    assert "continuing unconsumed numbered attempt 6" in text
    assert "No AWS execution is authorized" in text
    assert "new\ncommitted read-only run" in text
    assert "No live\nexecutor source changed" in text
    assert "no seventh attempt" in text
