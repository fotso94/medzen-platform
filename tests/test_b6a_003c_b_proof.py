from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_b6a_003c_b_proof import (
    MANIFEST_SHA,
    REQUEST_ID,
    ProofRefusal,
    parse_memory_samples,
    validate_logs,
    validate_response,
    validate_workload_pod_images,
)


def response():
    return {
        "request_id": REQUEST_ID,
        "classification": "PLATFORM_PROOF_ONLY",
        "production_approved": False,
        "model_versions": {
            "asr": "v0",
            "registry_snapshot": f"b6a-non-serving:{MANIFEST_SHA}",
            "llm": None,
            "rag": None,
            "tts": None,
        },
        "transcript": {
            "verbatim": "Synthetic test.",
            "normalized": "Synthetic test.",
            "normalization_version": "b6a-unicode-nfc-whitespace-v1",
        },
    }


def test_response_requires_platform_disclosure_and_exact_model_identity():
    validate_response(response())
    changed = response()
    changed["production_approved"] = True
    with pytest.raises(ProofRefusal, match="production approval"):
        validate_response(changed)
    changed = response()
    changed["model_versions"]["asr"] = "v1"
    with pytest.raises(ProofRefusal, match="model identities"):
        validate_response(changed)


def test_gpu_memory_requires_timestamped_samples_and_reports_peak():
    result = parse_memory_samples([
        "2026/08/05 04:00:00.000, 0, 120, 23034\n",
        "2026/08/05 04:00:00.200, 0, 8132, 23034\n",
        "2026/08/05 04:00:00.400, 0, 7990, 23034\n",
    ])
    assert result["baseline_used_mib"] == 120
    assert result["peak_used_mib"] == 8132
    assert result["sample_count"] == 3
    with pytest.raises(ProofRefusal, match="NOT_MEASURED"):
        parse_memory_samples([])


def test_log_review_refuses_synthetic_or_transcript_text_and_credentials():
    payload = response()
    validate_logs("safe request metadata only", payload)
    with pytest.raises(ProofRefusal, match="logs contain"):
        validate_logs("Synthetic test.", payload)
    with pytest.raises(ProofRefusal, match="logs contain"):
        validate_logs("credential AKIAABCDEFGHIJKLMNOP", payload)


def test_live_workload_must_use_the_scanned_child_digests():
    pod = {
        "spec": {
            "initContainers": [{
                "name": "model-loader",
                "image": (
                    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-model-loader"
                    "@sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5"
                ),
            }],
            "containers": [{
                "name": "asr-runtime",
                "image": (
                    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-runtime"
                    "@sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087"
                ),
            }],
        }
    }
    validate_workload_pod_images(pod)
    pod["spec"]["containers"][0]["image"] = "example.invalid/asr:latest"
    with pytest.raises(ProofRefusal, match="ASR runtime digest"):
        validate_workload_pod_images(pod)


def test_gpu_window_arms_deadline_and_trap_before_scale_or_workload():
    text = (ROOT / "scripts/run_b6a_003c_b_gpu_window.sh").read_text()
    assert "trap cleanup EXIT INT TERM" in text
    trap = text.index("trap cleanup EXIT INT TERM")
    arm = text.index("deadline.py arm")
    scale = text.index("desiredSize=1")
    proof = text.index("run_b6a_003c_b_proof.py")
    assert trap < arm < scale < proof
    assert "--window-seconds 7200" in text


def test_proof_sampler_starts_before_workload_apply_and_uses_dra_host_driver():
    text = (ROOT / "scripts/run_b6a_003c_b_proof.py").read_text()
    sampler = text.index("/driver-root/usr/bin/nvidia-smi")
    apply = text.index('["apply", "--filename", str(workload)]')
    assert sampler < apply
    assert "memory.used,memory.total" in text
    assert '"port-forward"' in text
    assert '"--address", "127.0.0.1"' in text
