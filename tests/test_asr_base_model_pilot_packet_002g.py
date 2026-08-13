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
    EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)


PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002G-attempt-8.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002G.json"
RISK = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002F-ATTEMPT-7-WORKTREE-BOUNDARY-REFUSAL.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002G-COLD/cold-rehearsal.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bindings() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_002g_is_attempt_eight_only_and_requires_exact_owner_phrase() -> None:
    text = PACKET.read_text(encoding="utf-8")
    value = bindings()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002G only" in text
    assert value["attempts"]["authorized_numbers"] == [8]
    assert value["attempts"]["maximum"] == 1
    assert value["attempts"]["seconds_each"] == 10800
    assert value["attempts"]["non_transferable"] is True
    assert value["attempts"]["attempt_7_reuse_permitted"] is False


def test_attempt_seven_refusal_and_all_history_remain_write_once() -> None:
    value = bindings()
    assert value["write_once_history"]["attempt_7_refusal"] == {
        "path": str(REFUSAL.relative_to(ROOT)),
        "sha256": sha(REFUSAL),
    }
    for item in value["write_once_history"].values():
        assert sha(ROOT / item["path"]) == item["sha256"]


def test_all_thirteen_live_modules_are_bound_to_current_reviewed_source() -> None:
    value = bindings()
    assert tuple(value["executor_modules"]) == EXECUTOR_MODULE_PATHS
    reviewed = value["executor_source_commit"]
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{reviewed}:{relative}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_external_workdir_and_rehearsal_fidelity_are_binding_rules() -> None:
    value = bindings()
    assert value["execution_workdir_policy"] == {
        "required_location": "OUTSIDE_REVIEWED_WORKTREE",
        "validated_before_any_runtime_side_effect": True,
        "receipt_store": "<external-workdir>/receipts",
        "runtime_evidence_commit_timing": "AFTER_TERMINAL_RUN_ONLY",
        "inside_worktree_refusal_code": "EXECUTION_WORKDIR_INSIDE_REVIEWED_WORKTREE",
    }
    assert value["rehearsal_fidelity_boundary"] == {
        "policy": "EVERYTHING_EXCEPT_PAID_EXTERNAL_CALLS",
        "shared_context_builder": "scripts.asr_base_model_pilot_runner.build_attempt_context",
        "shared_execution_runner": "scripts.asr_base_model_pilot_runner.execute_attempt",
        "shared_receipt_store": "pipeline.asr_base_model_pilot_receipts.ReceiptStore",
        "fake_only": ["AWS calls", "kubectl calls"],
        "filesystem_side_effects_faked": False,
    }


def test_image_risk_and_frozen_input_subject_are_unchanged() -> None:
    value = bindings()
    assert value["risk_acceptance_sha256"] == sha(RISK)
    assert value["image"]["image_context_changed"] is False
    assert value["image"]["oci_index_digest"] == (
        "sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa"
    )
    assert value["image"]["linux_amd64_digest"] == (
        "sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e"
    )
    assert value["input_freeze"]["pilot_rows"] == 540
    assert value["input_freeze"]["pilot_languages"] == 47


def test_final_receipt_uses_same_bootstrap_and_filesystem_ordering() -> None:
    value = json.loads(COLD.read_bytes())
    binding = bindings()
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["bindings_source"]["fixture_used"] is False
    assert value["fidelity_boundary"] == {
        "policy": "EVERYTHING_EXCEPT_PAID_EXTERNAL_CALLS",
        "shared_context_builder": "scripts.asr_base_model_pilot_runner.build_attempt_context",
        "shared_execution_runner": "scripts.asr_base_model_pilot_runner.execute_attempt",
        "shared_receipt_store": "pipeline.asr_base_model_pilot_receipts.ReceiptStore",
        "shared_filesystem_side_effect_ordering": True,
        "fake_boundary": ["AWS calls", "kubectl calls"],
        "filesystem_side_effects_faked": False,
    }
    for scenario in value["scenarios"].values():
        order = scenario["filesystem_side_effect_order"]
        assert order[:4] == [
            "external_workdir_validated_before_side_effects",
            "reviewed_worktree_clean_before_side_effects",
            "external_workdir_created",
            "pre_envelope_prerequisites_passed",
        ]
        assert order[4] == "attempt_envelope_persisted"
        assert order[-1] == "terminal_result_persisted"
        assert scenario["external_to_reviewed_worktree"] is True
        assert scenario["receipt_store_relative_path"] == "receipts"
    assert binding["cold_rehearsal"]["receipt_path"] == str(COLD.relative_to(ROOT))


def test_packet_binds_prospective_evidence_hashes() -> None:
    text = PACKET.read_text(encoding="utf-8")
    for path in (BINDINGS, REFUSAL, RISK, COLD):
        assert sha(path) in text
