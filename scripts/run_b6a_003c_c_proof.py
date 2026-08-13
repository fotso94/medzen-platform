#!/usr/bin/env python3
"""Wait for stable DRA readiness, then run the B6A proof with safe diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from scripts.asr_base_model_boundary_contracts import (
    DRA_WAIT_MAX_SECONDS,
    validate_boundary_parameters,
)

try:
    from scripts import run_b6a_003c_b_proof as base
except ModuleNotFoundError:  # Direct execution from scripts/.
    import run_b6a_003c_b_proof as base


AUTH_ID = "B6A-AWS-AUTH-2026-003C-C"
STABLE_READS = 3
POLL_SECONDS = 2
MAX_WAIT_SECONDS = DRA_WAIT_MAX_SECONDS


class StableDRARefusal(base.ProofRefusal):
    def __init__(self, message: str, observation: dict[str, Any] | None = None):
        super().__init__(message)
        self.observation = observation or {}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _authorization(path: Path, packet_sha256: str,
                   workload_sha256: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise base.ProofRefusal("exact 003C-C packet SHA-256 is required")
    try:
        record = json.loads(path.read_bytes())
    except Exception as exc:
        raise base.ProofRefusal("003C-C authorization is unreadable") from exc
    if record.get("id") != AUTH_ID or record.get("status") != "owner-approved":
        raise base.ProofRefusal("003C-C is not owner-approved")
    if record.get("packet", {}).get("sha256") != packet_sha256:
        raise base.ProofRefusal("003C-C authorization packet binding differs")
    resources = record.get("bound_resources", {})
    if resources.get("workload_render_sha256") != workload_sha256:
        raise base.ProofRefusal("003C-C workload render binding differs")
    if resources.get("synthetic_audio_sha256") != base.AUDIO_SHA:
        raise base.ProofRefusal("003C-C synthetic audio binding differs")
    return record


def evaluate_dra_snapshot(daemonset: dict[str, Any], pods: dict[str, Any],
                          device_class: dict[str, Any],
                          slices: dict[str, Any]) -> dict[str, Any]:
    status = daemonset.get("status", {})
    if (
        status.get("desiredNumberScheduled") != 1
        or status.get("numberReady") != 1
        or status.get("numberAvailable") != 1
    ):
        raise StableDRARefusal("DRA DaemonSet is not exactly one ready and available Pod")
    pod_spec = daemonset.get("spec", {}).get("template", {}).get("spec", {})
    images = [item.get("image") for item in pod_spec.get("initContainers", [])]
    images += [item.get("image") for item in pod_spec.get("containers", [])]
    if not images or set(images) != {base.DRA_IMAGE}:
        raise StableDRARefusal("DRA DaemonSet digest differs from the scanned manifest")

    items = pods.get("items", [])
    if len(items) != 1:
        raise StableDRARefusal("exactly one DRA Pod is not present", {
            "dra_pod_count": len(items)
        })
    pod = items[0]
    pod_status = pod.get("status", {})
    ready = any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in pod_status.get("conditions", [])
    )
    if pod_status.get("phase") != "Running" or not ready:
        raise StableDRARefusal("DRA Pod is not Running and Ready", {
            "dra_pod_phase": pod_status.get("phase"), "dra_pod_ready": ready
        })
    if pod.get("metadata", {}).get("deletionTimestamp"):
        raise StableDRARefusal("DRA Pod is terminating")
    live_spec = pod.get("spec", {})
    live_images = [item.get("image") for item in live_spec.get("initContainers", [])]
    live_images += [item.get("image") for item in live_spec.get("containers", [])]
    if not live_images or set(live_images) != {base.DRA_IMAGE}:
        raise StableDRARefusal("live DRA Pod digest differs from the scanned manifest")
    node_name = live_spec.get("nodeName")
    pod_uid = pod.get("metadata", {}).get("uid")
    if not isinstance(node_name, str) or not node_name or not pod_uid:
        raise StableDRARefusal("DRA Pod node or UID is absent")

    if device_class.get("metadata", {}).get("name") != "gpu.nvidia.com":
        raise StableDRARefusal("gpu.nvidia.com DeviceClass is absent")
    matching = []
    for item in slices.get("items", []):
        spec = item.get("spec", {})
        if spec.get("driver") != "gpu.nvidia.com" or spec.get("nodeName") != node_name:
            continue
        devices = spec.get("devices", [])
        if isinstance(devices, list) and devices:
            matching.append(item)
    if not matching:
        raise StableDRARefusal("matching NVIDIA ResourceSlice with devices is absent", {
            "dra_pod_uid": pod_uid,
            "gpu_node": node_name,
            "resource_slice_count": len(slices.get("items", [])),
        })
    slice_names = sorted(item["metadata"]["name"] for item in matching)
    device_count = sum(len(item["spec"]["devices"]) for item in matching)
    return {
        "dra_pod_uid": pod_uid,
        "gpu_node": node_name,
        "resource_slices": slice_names,
        "device_count": device_count,
        "dra_image": base.DRA_IMAGE,
    }


def _json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        raise StableDRARefusal("DRA readiness query failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StableDRARefusal("DRA readiness response is malformed") from exc


def wait_for_stable_dra(
    *, kubeconfig: Path, timeout_seconds: int = MAX_WAIT_SECONDS,
    poll_seconds: int = POLL_SECONDS,
    reader: Callable[[list[str]], dict[str, Any]] = _json,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    try:
        validate_boundary_parameters(
            "dra_wait",
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
    except Exception as exc:
        raise StableDRARefusal(str(exc)) from exc
    kubectl = ["kubectl", "--kubeconfig", str(kubeconfig)]
    commands = (
        kubectl + ["get", "daemonset", "dra-driver-nvidia-gpu-kubelet-plugin",
                   "--namespace", "nvidia-dra-driver", "-o", "json"],
        kubectl + ["get", "pods", "--namespace", "nvidia-dra-driver", "-l",
                   "dra-driver-nvidia-gpu-component=kubelet-plugin", "-o", "json"],
        kubectl + ["get", "deviceclass", "gpu.nvidia.com", "-o", "json"],
        kubectl + ["get", "resourceslices", "-o", "json"],
    )
    stop = clock() + timeout_seconds
    previous: dict[str, Any] | None = None
    stable = 0
    last_observation: dict[str, Any] = {}
    last_reason = "DRA readiness was not observed"
    reads = 0
    while clock() < stop:
        reads += 1
        try:
            observation = evaluate_dra_snapshot(*(reader(command) for command in commands))
            if observation == previous:
                stable += 1
            else:
                previous = observation
                stable = 1
            last_observation = observation
            if stable >= STABLE_READS:
                return {
                    "status": "DRA_STABLE_READY",
                    "consecutive_reads": stable,
                    "poll_seconds": poll_seconds,
                    "reads": reads,
                    **observation,
                }
        except StableDRARefusal as exc:
            previous = None
            stable = 0
            last_reason = str(exc)
            if exc.observation:
                last_observation = exc.observation
        sleeper(poll_seconds)
    raise StableDRARefusal(
        f"DRA stable readiness timed out: {last_reason}", last_observation
    )


def _write_receipt(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(result, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    stage = "LOCAL_BINDINGS"
    last_observation: dict[str, Any] = {}
    try:
        for path in (args.kubeconfig, args.workload, args.audio, args.authorization):
            if not path.is_file():
                raise base.ProofRefusal(f"required file is absent: {path}")
        if args.receipt.exists():
            raise base.ProofRefusal("refusing to overwrite 003C-C proof receipt")
        workload_sha = _sha(args.workload)
        _authorization(args.authorization, args.packet_sha256, workload_sha)
        if _sha(args.audio) != base.AUDIO_SHA or args.audio.stat().st_size != base.AUDIO_BYTES:
            raise base.ProofRefusal("synthetic audio identity differs")

        stage = "DRA_STABLE_READINESS"
        readiness = wait_for_stable_dra(kubeconfig=args.kubeconfig)
        last_observation = readiness
        stage = "ASR_PLATFORM_PROOF"
        result = base.run_proof(
            kubeconfig=args.kubeconfig, workload=args.workload, audio=args.audio
        )
        result["dra_stable_readiness"] = readiness
        result["diagnostic_schema"] = "B6A_003C_C_NO_PHI_V1"
        _write_receipt(args.receipt, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        if isinstance(exc, StableDRARefusal) and exc.observation:
            last_observation = exc.observation
        result = {
            "status": "REFUSED",
            "stage": stage,
            "error": type(exc).__name__,
            "reason": str(exc),
            "last_dra_observation": last_observation,
            "diagnostic_schema": "B6A_003C_C_NO_PHI_V1",
            "contains_audio_or_transcript": False,
        }
        if not args.receipt.exists():
            _write_receipt(args.receipt, result)
        print(json.dumps(result, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
