from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import STAGES
from scripts.asr_base_model_pilot_fake import assert_no_parallel_stage_implementation
from scripts.asr_base_model_pilot_integrity import validate_executor_module_bindings
from scripts.asr_base_model_pilot_plan import exact_plan


PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002I-attempt-10.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002I.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002I-COLD/cold-rehearsal.json"
DIAGNOSIS = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002H-ATTEMPT-9-LIVE-REHEARSAL-DIVERGENCE-DIAGNOSIS.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002H-ATTEMPT-9-ARTIFACT-STATUS-CONTRACT-REFUSAL.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_packet_is_a_non_executable_single_attempt_request() -> None:
    text = PACKET.read_text(encoding="utf-8")
    value = bound()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002I only" in text
    assert value["attempts"] == {
        "authorized_numbers": [10],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10.0,
        "attempts_1_through_9_reuse_permitted": False,
    }
    assert value["risk_acceptance_sha256"] in text


def test_attempt_nine_history_and_diagnosis_are_bound_write_once() -> None:
    value = bound()["write_once_history"]
    assert value["attempt_9_refusal"]["sha256"] == sha(REFUSAL)
    assert value["attempt_9_live_rehearsal_divergence_diagnosis"]["sha256"] == sha(DIAGNOSIS)
    for item in value.values():
        assert sha(ROOT / item["path"]) == item["sha256"]


def test_every_live_and_rehearsal_module_is_hash_bound() -> None:
    result = validate_executor_module_bindings(ROOT, bound()["executor_modules"])
    assert result["status"] == "PASS_ALL_EXECUTOR_MODULE_HASHES"
    assert result["module_count"] == 16
    assert "scripts/asr_base_model_pilot_fake.py" in result["module_sha256"]
    assert "scripts/asr_base_model_pilot_cold_rehearsal.py" in result["module_sha256"]


def test_rehearsal_has_one_real_stage_class_and_all_stage_mappings() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["real_stage_implementations"] == len(STAGES) == 11
    assert receipt["parallel_fake_stage_implementations"] == 0
    assert receipt["full_pass_runs"] == 1
    assert receipt["injected_failure_runs"] == 8
    assert receipt["real_aws_calls"] == 0
    assert receipt["real_kubectl_calls"] == 0
    assert set(receipt["execution_asset_completeness"]) == set(STAGES)
    assert all(
        value["same_operation_for_live_and_rehearsal"] is True
        for value in receipt["execution_asset_completeness"].values()
    )
    assert assert_no_parallel_stage_implementation()["parallel_stage_implementations"] == 0


def test_artifact_wrapper_contract_and_full_pass_are_proven() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["artifact_wrapper_contract"] == {
        "status": "PASS_ARTIFACT_WRAPPER_CONTRACT",
        "outer_status": "PASS_ARTIFACT_STAGE",
        "nested_verification_status": "PASS_PRESTAGED_BUNDLE_VERIFY_ONLY",
        "artifact_upload_bytes": 0,
    }
    assert receipt["scenarios"]["clean_pass"]["outcome"] == "PASS_PILOT"
    assert receipt["scenarios"]["clean_pass"]["zero_state"] is True
    assert receipt["scenarios"]["prestage_object_absent"]["failure_reason_code"] == "PRESTAGED_OBJECT_ABSENT"


def test_recorded_boundary_fixture_hashes_are_exact() -> None:
    for item in bound()["recorded_boundary_fixtures"].values():
        assert sha(ROOT / item["path"]) == item["sha256"]


def test_attempt_ten_plan_keeps_artifact_and_image_existing_read_only() -> None:
    value = bound()
    plan = exact_plan(value, 10)
    assert plan["permanent_create_only"] == []
    assert "ecr:repository/medzen-asr-eval-runtime" in plan["read_only_existing"]
    assert "s3:" + value["pilot_bundle"]["s3_prefix"].removeprefix("s3://") + "**" in plan["read_only_existing"]


def test_packet_and_rehearsal_have_reviewable_stable_hashes(tmp_path: Path) -> None:
    assert sha(COLD) in PACKET.read_text(encoding="utf-8")
    generated = tmp_path / "cold-rehearsal.json"
    subprocess.run(
        [sys.executable, "scripts/asr_base_model_pilot_cold_rehearsal.py", "--output", str(generated)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert generated.read_bytes() == COLD.read_bytes()
