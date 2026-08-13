from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_b6a_003c_b_proof as base
from scripts.run_b6a_003c_c_proof import (
    StableDRARefusal,
    evaluate_dra_snapshot,
    wait_for_stable_dra,
)


NODE = "ip-172-31-1-2.eu-central-1.compute.internal"


def snapshot():
    daemonset = {
        "status": {
            "desiredNumberScheduled": 1,
            "numberReady": 1,
            "numberAvailable": 1,
        },
        "spec": {"template": {"spec": {
            "initContainers": [{"name": "init-container", "image": base.DRA_IMAGE}],
            "containers": [{"name": "gpus", "image": base.DRA_IMAGE}],
        }}},
    }
    pods = {"items": [{
        "metadata": {"name": "dra-pod", "uid": "pod-uid"},
        "spec": {
            "nodeName": NODE,
            "initContainers": [{"name": "init-container", "image": base.DRA_IMAGE}],
            "containers": [{"name": "gpus", "image": base.DRA_IMAGE}],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }]}
    device_class = {"metadata": {"name": "gpu.nvidia.com"}}
    slices = {"items": [{
        "metadata": {"name": "gpu-slice"},
        "spec": {
            "driver": "gpu.nvidia.com",
            "nodeName": NODE,
            "devices": [{"name": "gpu-0"}],
        },
    }]}
    return daemonset, pods, device_class, slices


def test_snapshot_requires_exact_ready_pod_digest_and_matching_slice():
    result = evaluate_dra_snapshot(*snapshot())
    assert result == {
        "dra_pod_uid": "pod-uid",
        "gpu_node": NODE,
        "resource_slices": ["gpu-slice"],
        "device_count": 1,
        "dra_image": base.DRA_IMAGE,
    }
    values = list(snapshot())
    values[3] = {"items": []}
    with pytest.raises(StableDRARefusal, match="ResourceSlice") as refusal:
        evaluate_dra_snapshot(*values)
    assert refusal.value.observation["resource_slice_count"] == 0


def test_snapshot_refuses_unready_or_wrong_digest_dra_pod():
    values = list(snapshot())
    values[1] = copy.deepcopy(values[1])
    values[1]["items"][0]["status"]["conditions"][0]["status"] = "False"
    with pytest.raises(StableDRARefusal, match="Running and Ready"):
        evaluate_dra_snapshot(*values)
    values = list(snapshot())
    values[0] = copy.deepcopy(values[0])
    values[0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "example.invalid/dra:latest"
    )
    with pytest.raises(StableDRARefusal, match="digest"):
        evaluate_dra_snapshot(*values)


def test_wait_requires_three_stable_reads_after_transient_missing_slice(tmp_path):
    ready = snapshot()
    not_ready = list(snapshot())
    not_ready[3] = {"items": []}
    cycles = [tuple(not_ready), ready, ready, ready]
    calls = 0

    def reader(command):
        nonlocal calls
        cycle = min(calls // 4, len(cycles) - 1)
        item = cycles[cycle][calls % 4]
        calls += 1
        return copy.deepcopy(item)

    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("test")
    result = wait_for_stable_dra(
        kubeconfig=kubeconfig, timeout_seconds=2, poll_seconds=1,
        reader=reader, sleeper=lambda _: None,
    )
    assert result["status"] == "DRA_STABLE_READY"
    assert result["consecutive_reads"] == 3
    assert result["reads"] == 4


def test_retry_window_installs_cleanup_before_7140_second_deadline_and_scale():
    text = (ROOT / "scripts/run_b6a_003c_c_gpu_window.sh").read_text()
    trap = text.index("trap cleanup EXIT INT TERM")
    arm = text.index("003c_c_deadline.py arm")
    scale = text.index("desiredSize=1")
    proof = text.index("run_b6a_003c_c_proof.py")
    assert trap < arm < scale < proof
    assert "--window-seconds 7140" in text
    assert "three consecutive reads" in text


def test_refusal_receipt_is_explicit_and_declared_no_phi():
    text = (ROOT / "scripts/run_b6a_003c_c_proof.py").read_text()
    assert '"stage": stage' in text
    assert '"reason": str(exc)' in text
    assert '"contains_audio_or_transcript": False' in text
    assert "B6A_003C_C_NO_PHI_V1" in text
