from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6A-SAMPLER-DEBUG-2026-001-RESULT.json"


def _record() -> dict:
    return json.loads(RESULT.read_text())


def test_debug_proves_exact_120_sample_sampler_hash() -> None:
    record = _record()
    assert record["outcome"] == "PASS_PROVEN_120_SAMPLE_SCRIPT"
    sampler = record["proven_sampler"]
    assert sampler["sample_count"] == 120
    assert sampler["total_mib"] == 23034
    assert sampler["raw_stderr"] == ""
    assert hashlib.sha256((ROOT / sampler["path"]).read_bytes()).hexdigest() == sampler["proven_sha256"]
    assert sampler["runtime_interface"] == "/usr/local/bin/nerdctl --namespace k8s.io"


def test_debug_receipts_are_content_addressed_and_pre_artifact() -> None:
    record = _record()
    for binding in record["receipts"]:
        path = ROOT / binding["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
        receipt = json.loads(path.read_text())
        assert receipt["contains_audio_transcript_credentials_secrets_or_phi"] is False
        if "raw_stdout" in receipt or "raw_stderr" in receipt:
            facts = receipt["pre_artifact_facts"]
            assert facts == {
                "model_artifact_present_on_node": False,
                "audio_artifact_present_on_node": False,
                "model_or_audio_workload_applied": False,
            }


def test_debug_cleanup_and_budget_fail_closed() -> None:
    record = _record()
    cleanup = record["cleanup_verification"]
    for key in (
        "eks_gpu_desired", "asg_instances", "gpu_nodes", "dra_pods",
        "medzen_workloads", "scheduled_actions", "approved_asr_objects_or_versions",
        "production_registry_parameters",
    ):
        assert cleanup[key] == 0
    assert cleanup["post_run_terraform_plan"] == "NO_CHANGES"
    budget = record["budget_control"]
    assert budget["debug_conservative_debit_seconds"] == 900
    assert budget["remaining_original_two_hour_allowance_seconds"] == 5109


def test_debug_does_not_authorize_or_execute_003c_e() -> None:
    boundaries = _record()["production_and_governance_boundaries"]
    assert boundaries["packet_003c_e_created"] is False
    assert boundaries["packet_003c_e_approved"] is False
    assert boundaries["full_b6a_proof_attempted"] is False
    assert boundaries["approved_artifact_written"] is False
    assert boundaries["production_ssm_changed"] is False


def test_historical_v1_policy_remains_unchanged() -> None:
    record = _record()
    policy = record["policy"]
    assert hashlib.sha256((ROOT / policy["historical_v1_path"]).read_bytes()).hexdigest() == policy["historical_v1_sha256"]
    assert policy["historical_receipts_changed"] is False
