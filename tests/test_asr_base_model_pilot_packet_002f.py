from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_integrity import (
    EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_eval_digest_rescan import validate_scout_sarif


PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002F-attempt-7.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002F.json"
DIAGNOSIS = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-SCOUT-EXECUTION-DIAGNOSIS-2026-001.json"
PREFLIGHT = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-SCOUT-PREFLIGHT-2026-001.json"
SARIF = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-SCOUT-PREFLIGHT-2026-001.sarif.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002F-COLD-v4/cold-rehearsal.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002E-A1-ATTEMPT-6-SCOUT-EXECUTION-REFUSAL.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bindings() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_packet_is_non_executable_and_requests_fresh_attempt_seven_only() -> None:
    text = PACKET.read_text(encoding="utf-8")
    value = bindings()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002F only" in text
    assert "numbered attempt 7" in text
    assert "fresh $10 ceiling" in text
    assert value["attempts"]["authorized_numbers"] == [7]
    assert value["attempts"]["attempt_6_reuse_permitted"] is False


def test_diagnosis_closes_every_requested_hypothesis_and_binds_refusal() -> None:
    value = json.loads(DIAGNOSIS.read_bytes())
    assert value["trigger"]["sha256"] == sha(REFUSAL)
    assert value["root_cause"]["class"] == (
        "DOCKER_SCOUT_1_18_3_OCI_DIRECTORY_SBOM_GENERATION_DEFECT"
    )
    assert {item["result"] for item in value["hypotheses"].values()} == {
        "RULED_OUT", "CONFIRMED"
    }
    assert value["hypotheses"]["oci_directory_ingestion"]["result"] == "CONFIRMED"


def test_preflight_is_real_zero_aws_exact_image_pass() -> None:
    value = json.loads(PREFLIGHT.read_bytes())
    bound = bindings()
    assert value["status"] == "PASS_EXACT_IMAGE_SCOUT_REAL_EXECUTION_PREFLIGHT"
    assert value["image"]["oci_index_digest"] == bound["image"]["oci_index_digest"]
    assert value["image"]["linux_amd64_digest"] == bound["image"]["linux_amd64_digest"]
    assert value["diagnostic"]["returncode"] == 0
    assert value["scope"] == {
        "aws_calls": 0,
        "aws_mutations": 0,
        "cost_usd": 0.0,
        "gpu_started": False,
        "kubectl_calls": 0,
        "temporary_files_destroyed": True,
    }
    assert validate_scout_sarif(json.loads(SARIF.read_bytes()))["high"] == 4
    assert bound["scout_real_execution_preflight"]["sha256"] == sha(PREFLIGHT)
    assert bound["scout_real_execution_preflight"]["sarif_sha256"] == sha(SARIF)


def test_all_thirteen_executor_modules_are_bound_and_current() -> None:
    value = bindings()
    assert tuple(value["executor_modules"]) == EXECUTOR_MODULE_PATHS
    assert validate_executor_module_bindings(ROOT, value["executor_modules"])["module_count"] == 13
    assert value["external_tool_diagnostics"] == {
        "required_for_every_subprocess": True,
        "write_once_journal": True,
        "maximum_sanitized_stdout_bytes": 4096,
        "maximum_sanitized_stderr_bytes": 4096,
        "exit_code_required": True,
        "timeout_status_required": True,
        "raw_output_sha256_required": True,
        "credential_values_recorded": False,
    }


def test_final_cold_rehearsal_uses_actual_committed_bindings() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["bindings_source"]["path"] == str(BINDINGS.relative_to(ROOT))
    assert value["attempt_7_security_rehearsal"]["aligned_pass"] is True
    assert value["full_pass_runs"] == 1
    assert value["injected_failure_runs"] == 5
    assert value["executor_module_integrity"]["module_count"] == 13
    assert bindings()["cold_rehearsal"]["receipt_path"] == str(COLD.relative_to(ROOT))


def test_historical_attempt_six_records_remain_byte_identical() -> None:
    value = bindings()["write_once_history"]
    for item in value.values():
        assert sha(ROOT / item["path"]) == item["sha256"]


def test_packet_binds_every_new_evidence_hash() -> None:
    text = PACKET.read_text(encoding="utf-8")
    for path in (BINDINGS, DIAGNOSIS, PREFLIGHT, SARIF, COLD, REFUSAL):
        assert sha(path) in text
