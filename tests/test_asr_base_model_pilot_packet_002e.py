from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_integrity import (
    EXECUTOR_MODULE_PATHS,
    read_committed_artifact,
    validate_executor_module_bindings,
)


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002E.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002E-COLD/cold-rehearsal.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002E-attempt-6.md"
RISK = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bindings() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt_six_binds_every_live_module_unconditionally() -> None:
    value = _bindings()
    assert tuple(value["executor_modules"]) == EXECUTOR_MODULE_PATHS
    assert validate_executor_module_bindings(ROOT, value["executor_modules"])["status"] == (
        "PASS_ALL_EXECUTOR_MODULE_HASHES"
    )
    assert value["executor_integrity_policy"] == {
        "all_module_hashes_required_on_every_attempt": True,
        "conditional_integrity_guards_permitted": False,
        "missing_or_extra_module_bindings_refuse": True,
        "changed_module_bytes_refuse": True,
    }


def test_rehearsal_loaded_the_actual_committed_bindings_not_a_fixture() -> None:
    value = json.loads(COLD.read_bytes())
    source = value["bindings_source"]
    assert source == {
        "fixture_used": False,
        "loaded_from_committed_head": True,
        "path": str(BINDINGS.relative_to(ROOT)),
        "sha256": _sha(BINDINGS),
    }
    assert read_committed_artifact(ROOT, BINDINGS) == BINDINGS.read_bytes()
    assert value["executor_module_integrity"]["module_count"] == len(EXECUTOR_MODULE_PATHS)
    assert value["attempt_6_security_rehearsal"]["aligned_pass"] is True
    assert value["full_pass_runs"] == 1
    assert value["injected_failure_runs"] == 5


def test_historical_cold_rehearsal_remains_write_once() -> None:
    committed = json.loads(COLD.read_bytes())
    assert committed["status"] == "PASS_COLD_REHEARSAL"
    assert committed["bindings_source"]["sha256"] == _sha(BINDINGS)


def test_write_once_history_and_unchanged_subject_are_exact() -> None:
    value = _bindings()
    for item in value["write_once_history"].values():
        assert _sha(ROOT / item["path"]) == item["sha256"]
    assert value["risk_acceptance_sha256"] == _sha(RISK)
    assert value["image"]["image_context_changed"] is False
    assert value["image"]["oci_index_digest"] == (
        "sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa"
    )
    assert value["attempts"]["authorized_numbers"] == [6]
    assert value["attempts"]["attempt_5_reuse_permitted"] is False


def test_packet_requires_post_approval_committed_complete_stage_one_dry_run() -> None:
    text = PACKET.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" in text
    assert "numbered attempt 6" in text
    assert "all 11 live executor modules" in text
    assert "conditional integrity guards are\nprohibited" in text
    assert "complete `deadline_identity_and_acceptance` stage" in text
    assert "authorization, bindings\nand packet" in text
    assert "committed PASS receipt" in text
    assert "No AWS execution is authorized by this draft" in text


def test_authorization_and_deadline_dry_run_are_write_once_after_approval() -> None:
    value = _bindings()
    authorization = ROOT / value["authorization"]["path"]
    dry_run = ROOT / value["authorization"]["deadline_dry_run_path"]
    assert _sha(authorization) == "aa2366d3dffa70229a2e105990fb5f5be57d6ec771dce90ddc80a92aade38faf"
    assert _sha(dry_run) == "ebd21c0ac90c148659c18c726ee817dd2793f80c7e6f122cfce1fd6abd0b71f0"
