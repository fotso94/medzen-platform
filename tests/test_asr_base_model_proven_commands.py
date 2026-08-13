from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal, ReceiptStore
from scripts.asr_base_model_proven_commands import (
    B6A_PROVEN_NVIDIA_SMI_ARGV,
    B6A_SAMPLER_RECEIPT,
    B6A_SAMPLER_RECEIPT_SHA256,
    B6A_SAMPLER_SCRIPT,
    B6A_SAMPLER_SCRIPT_SHA256,
    canonical_argv_sha256,
    validate_proven_command_bindings,
)


AUDIT = ROOT / "platform/evidence/ASR-BASE-MODEL-LIVE-NODE-COMMAND-AUDIT-2026-001.json"
ATTEMPT_15 = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002N-ATTEMPT-15-GPU-SAMPLER-REFUSAL.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding() -> dict:
    return {
        "sampler": {
            "script_path": str(B6A_SAMPLER_SCRIPT),
            "script_sha256": B6A_SAMPLER_SCRIPT_SHA256,
            "receipt_path": str(B6A_SAMPLER_RECEIPT),
            "receipt_sha256": B6A_SAMPLER_RECEIPT_SHA256,
            "canonical_inner_argv": list(B6A_PROVEN_NVIDIA_SMI_ARGV),
            "canonical_inner_argv_sha256": canonical_argv_sha256(
                B6A_PROVEN_NVIDIA_SMI_ARGV
            ),
        }
    }


def test_executor_argv_is_byte_identical_to_receipt_bound_b6a_script() -> None:
    result = validate_proven_command_bindings(ROOT, binding())
    assert result["status"] == "PASS_PROVEN_LIVE_NODE_COMMAND_BINDINGS"
    assert result["sampler"]["canonical_inner_argv"] == list(
        B6A_PROVEN_NVIDIA_SMI_ARGV
    )
    assert result["sampler"]["canonical_inner_argv_sha256"] == (
        "04e6d317a48f3602402b011289224cb686ab7313aab6726051d2f089ac5bd426"
    )
    receipt = json.loads((ROOT / B6A_SAMPLER_RECEIPT).read_bytes())
    assert receipt["payload"]["command_sha256"] == sha(ROOT / B6A_SAMPLER_SCRIPT)


def test_attempt_15_observed_failure_is_rehearsed_with_typed_diagnostic(tmp_path: Path) -> None:
    bindings = json.loads(
        (ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002N.json").read_bytes()
    )
    bindings["proven_live_node_commands"] = binding()
    operations, state = build_rehearsal_operations(
        bindings, injection="sampler_driver_library_missing"
    )
    context = AttemptContext(
        attempt=16,
        bindings=bindings,
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path,
    )
    with pytest.raises(OperationRefusal) as captured:
        operations.gpu_and_sampler_gate(context)
    assert captured.value.reason_code == "GPU_SAMPLER_DRIVER_LIBRARY_NOT_FOUND"
    diagnostic = json.loads((tmp_path / "gpu-sampler-self-test.json").read_bytes())
    assert diagnostic["reason_code"] == "GPU_SAMPLER_DRIVER_LIBRARY_NOT_FOUND"
    assert diagnostic["numeric_sample_count"] == 0
    assert diagnostic["proven_inner_argv"] == list(B6A_PROVEN_NVIDIA_SMI_ARGV)
    assert state.last_sampler_command is not None
    assert B6A_PROVEN_NVIDIA_SMI_ARGV[0] in state.last_sampler_command[-1]


def test_corrected_proven_sampler_rehearses_120_numeric_samples(tmp_path: Path) -> None:
    bindings = json.loads(
        (ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002N.json").read_bytes()
    )
    bindings["proven_live_node_commands"] = binding()
    operations, state = build_rehearsal_operations(bindings)
    context = AttemptContext(
        attempt=16,
        bindings=bindings,
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path,
    )
    result = operations.gpu_and_sampler_gate(context)
    assert result["status"] == "PASS_GPU_AND_SAMPLER_GATE"
    assert result["samples"] == 120
    assert result["baseline_mib"] == result["peak_mib"] == 100
    assert result["total_mib"] == 23034
    assert result["sampler_binding_status"] == "PASS_BYTE_IDENTICAL_HISTORICAL_ARGV"
    diagnostic = json.loads((tmp_path / "gpu-sampler-self-test.json").read_bytes())
    assert diagnostic["status"] == "PASS_120_NUMERIC_SAMPLES"
    assert diagnostic["reason_code"] is None


def test_audit_does_not_invent_staging_or_workload_provenance() -> None:
    audit = json.loads(AUDIT.read_bytes())
    assert audit["commands"]["gpu_sampler"]["classification"] == (
        "HISTORICALLY_PROVEN_AND_REQUIRED_BYTE_IDENTICAL"
    )
    for name in ("node_local_input_stage", "pilot_workload"):
        assert audit["commands"][name]["classification"] == "NOT_HISTORICALLY_PROVEN"
        assert audit["commands"][name]["successful_live_pass_receipts_found"] == 0
        assert audit["commands"][name]["may_be_described_as_proven_before_live_pass"] is False


def test_historical_refusal_and_proof_files_are_unchanged() -> None:
    assert sha(ATTEMPT_15) == "e26b1d686fc68a9e5b3a7a8e725745d90e0bbaf663460331a984b7926f51bbbe"
    assert sha(ROOT / B6A_SAMPLER_SCRIPT) == B6A_SAMPLER_SCRIPT_SHA256
    assert sha(ROOT / B6A_SAMPLER_RECEIPT) == B6A_SAMPLER_RECEIPT_SHA256


def test_no_direct_driver_root_nvidia_smi_remains_in_live_executor() -> None:
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text()
    assert "/driver-root/usr/bin/nvidia-smi" not in source
    assert "sampler_shell_command()" in source
