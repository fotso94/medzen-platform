from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "platform/decisions/B6A-DESIGN-2026-004-deployment-safety.json"


def _decision():
    return json.loads(DECISION.read_text())


def test_design_decision_preserves_every_prior_binding():
    decision = _decision()
    assert decision["status"] == "owner-approved-local-preparation-only"
    for binding in decision["immutable_bindings"]:
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == binding[
            "sha256"
        ]
        assert binding["historical_record_edited"] is False


def test_design_distinguishes_oci_children_from_single_dra_manifest():
    images = _decision()["exact_deployment_images"]
    assert images["model_loader"]["kind"] == "OCI_INDEX_WITH_LINUX_AMD64_CHILD"
    assert images["asr_runtime"]["kind"] == "OCI_INDEX_WITH_LINUX_AMD64_CHILD"
    assert images["nvidia_dra"]["kind"] == "SINGLE_DOCKER_MANIFEST_NO_CHILD"
    for image in images.values():
        assert image["deployable_digest"].startswith("sha256:")
        assert len(image["deployable_digest"]) == 71


def test_design_requires_independent_deadline_before_gpu_scale():
    controls = " ".join(_decision()["required_local_controls"]["independent_deadline"])
    assert "Before GPU scale-up" in controls
    assert "scheduled action" in controls
    assert "min=0, desired=0 and max=1" in controls
    assert "EXIT cleanup" in controls
    assert "retain the deadline action and fail closed" in controls


def test_design_requires_synthetic_no_phi_input_and_memory_measurement():
    decision = _decision()["required_local_controls"]
    test_input = " ".join(decision["test_input"])
    proof = " ".join(decision["success_proof"])
    assert "synthetic spoken phrase" in test_input
    assert "cached training" in test_input
    assert "Peak L4 GPU memory" in proof
    assert "nvidia-smi" in proof
    assert "absence of audio bytes, transcript text" in proof


def test_design_authorizes_no_aws_or_gpu_mutation():
    prohibited = " ".join(_decision()["prohibited_operations"])
    for required in (
        "Any AWS mutation",
        "Any Helm or kubectl apply",
        "Any NVIDIA DRA installation",
        "Any GPU scale-up",
        "Any approved-ASR write",
    ):
        assert required in prohibited
