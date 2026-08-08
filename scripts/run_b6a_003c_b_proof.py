#!/usr/bin/env python3
"""Run the internal-only B6A proof while sampling L4 memory from the DRA Pod."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

MODEL_LOADER_IMAGE = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-model-loader"
    "@sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5"
)
ASR_RUNTIME_IMAGE = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-runtime"
    "@sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087"
)
DRA_IMAGE = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-nvidia-dra"
    "@sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246"
)
MANIFEST_SHA = "c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850"
AUDIO_SHA = "3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3"
AUDIO_BYTES = 155962
PHRASE = "This is a synthetic MedZen platform test. No patient data is present."
REQUEST_ID = "00000000-0000-4000-8000-0000000006a0"


class ProofRefusal(RuntimeError):
    pass


def _authorization(path: Path, packet_sha256: str,
                   workload_sha256: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise ProofRefusal("exact packet SHA-256 is required")
    try:
        record = json.loads(path.read_bytes())
    except Exception as exc:
        raise ProofRefusal("003C-B authorization is unreadable") from exc
    if record.get("id") != "B6A-AWS-AUTH-2026-003C-B" or record.get("status") != "owner-approved":
        raise ProofRefusal("003C-B is not owner-approved")
    if record.get("packet", {}).get("sha256") != packet_sha256:
        raise ProofRefusal("authorization packet binding differs")
    resources = record.get("bound_resources", {})
    if resources.get("workload_render_sha256") != workload_sha256:
        raise ProofRefusal("authorization workload render binding differs")
    if resources.get("synthetic_audio_sha256") != AUDIO_SHA:
        raise ProofRefusal("authorization synthetic audio binding differs")
    return record


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        raise ProofRefusal("Kubernetes query or mutation failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProofRefusal("Kubernetes JSON response is malformed") from exc


def validate_response(payload: dict[str, Any]) -> None:
    if payload.get("request_id") != REQUEST_ID:
        raise ProofRefusal("transcription request identity differs")
    if payload.get("classification") != "PLATFORM_PROOF_ONLY":
        raise ProofRefusal("platform-test classification is absent")
    if payload.get("production_approved") is not False:
        raise ProofRefusal("response incorrectly claims production approval")
    versions = payload.get("model_versions", {})
    if versions != {
        "asr": "v0",
        "registry_snapshot": f"b6a-non-serving:{MANIFEST_SHA}",
        "llm": None,
        "rag": None,
        "tts": None,
    }:
        raise ProofRefusal("response model identities differ")
    transcript = payload.get("transcript")
    if not isinstance(transcript, dict):
        raise ProofRefusal("transcript response is absent")
    if set(transcript) != {"verbatim", "normalized", "normalization_version"}:
        raise ProofRefusal("transcript response fields differ")
    if transcript.get("normalization_version") != "b6a-unicode-nfc-whitespace-v1":
        raise ProofRefusal("normalization version differs")


def parse_memory_samples(lines: list[str]) -> dict[str, Any]:
    samples = []
    for raw in lines:
        fields = [item.strip() for item in raw.strip().split(",")]
        if len(fields) != 4:
            continue
        timestamp, index, used, total = fields
        try:
            sample = {
                "timestamp": timestamp,
                "gpu_index": int(index),
                "used_mib": int(used),
                "total_mib": int(total),
            }
        except ValueError:
            continue
        if sample["gpu_index"] != 0 or not 0 <= sample["used_mib"] <= sample["total_mib"]:
            raise ProofRefusal("GPU memory sample is invalid")
        samples.append(sample)
    if not samples:
        raise ProofRefusal("peak L4 GPU memory is NOT_MEASURED")
    return {
        "sample_count": len(samples),
        "baseline_used_mib": samples[0]["used_mib"],
        "peak_used_mib": max(item["used_mib"] for item in samples),
        "total_mib": samples[0]["total_mib"],
        "first_timestamp": samples[0]["timestamp"],
        "last_timestamp": samples[-1]["timestamp"],
        "sampling_path": "nvidia-dra:/driver-root/usr/bin/nvidia-smi",
    }


def validate_logs(logs: str, payload: dict[str, Any]) -> None:
    forbidden = [PHRASE, AUDIO_SHA, "AKIA", "ASIA", "secretAccessKey"]
    transcript = payload.get("transcript", {})
    for key in ("verbatim", "normalized"):
        value = transcript.get(key)
        if isinstance(value, str) and value:
            forbidden.append(value)
    lowered = logs.lower()
    for value in forbidden:
        if value.lower() in lowered:
            raise ProofRefusal("logs contain synthetic audio text, transcript text or credentials")


def validate_workload_pod_images(pod: dict[str, Any]) -> None:
    spec = pod.get("spec", {})
    init_images = {
        item.get("name"): item.get("image") for item in spec.get("initContainers", [])
    }
    runtime_images = {
        item.get("name"): item.get("image") for item in spec.get("containers", [])
    }
    if init_images != {"model-loader": MODEL_LOADER_IMAGE}:
        raise ProofRefusal("running model-loader digest differs from the scanned child")
    if runtime_images != {"asr-runtime": ASR_RUNTIME_IMAGE}:
        raise ProofRefusal("running ASR runtime digest differs from the scanned child")


def _http_json(method: str, path: str, *, body: bytes | None = None,
               headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", 18081, timeout=30)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    try:
        return response.status, json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProofRefusal("ASR response is not JSON") from exc


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_proof(*, kubeconfig: Path, workload: Path, audio: Path) -> dict[str, Any]:
    kubectl = ["kubectl", "--kubeconfig", str(kubeconfig)]
    daemonset = _run_json(kubectl + [
        "get", "daemonset", "dra-driver-nvidia-gpu-kubelet-plugin",
        "--namespace", "nvidia-dra-driver", "-o", "json",
    ])
    status = daemonset.get("status", {})
    if status.get("desiredNumberScheduled") != 1 or status.get("numberReady") != 1:
        raise ProofRefusal("exactly one ready DRA Pod is required")
    pod_spec = daemonset["spec"]["template"]["spec"]
    images = [item["image"] for item in pod_spec.get("initContainers", [])]
    images += [item["image"] for item in pod_spec.get("containers", [])]
    if not images or set(images) != {DRA_IMAGE}:
        raise ProofRefusal("running DRA digest differs from the scanned manifest")

    pods = _run_json(kubectl + [
        "get", "pods", "--namespace", "nvidia-dra-driver",
        "-l", "dra-driver-nvidia-gpu-component=kubelet-plugin", "-o", "json",
    ]).get("items", [])
    if len(pods) != 1:
        raise ProofRefusal("exactly one DRA Pod is required")
    dra_pod = pods[0]["metadata"]["name"]
    _run_json(kubectl + ["get", "deviceclass", "gpu.nvidia.com", "-o", "json"])
    slices = _run_json(kubectl + ["get", "resourceslices", "-o", "json"])
    if not any(item.get("spec", {}).get("driver") == "gpu.nvidia.com"
               for item in slices.get("items", [])):
        raise ProofRefusal("NVIDIA ResourceSlice is absent")

    samples: list[str] = []
    sampler = subprocess.Popen(
        kubectl + [
            "exec", "--namespace", "nvidia-dra-driver", dra_pod, "-c", "gpus", "--",
            "/busybox/sh", "-c",
            "while true; do /driver-root/usr/bin/nvidia-smi "
            "--query-gpu=timestamp,index,memory.used,memory.total "
            "--format=csv,noheader,nounits; /busybox/sleep 0.2; done",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )

    def collect() -> None:
        assert sampler.stdout is not None
        for line in sampler.stdout:
            samples.append(line)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    port_forward: subprocess.Popen[str] | None = None
    try:
        stop = time.monotonic() + 30
        while not samples and time.monotonic() < stop and sampler.poll() is None:
            time.sleep(0.2)
        if not samples:
            raise ProofRefusal("pre-startup GPU memory baseline is absent")

        applied = subprocess.run(
            kubectl + ["apply", "--filename", str(workload)],
            check=False, text=True, capture_output=True,
        )
        if applied.returncode != 0:
            raise ProofRefusal("B6A workload apply failed")
        available = subprocess.run(
            kubectl + [
                "wait", "--namespace", "medzen", "--for=condition=available",
                "deployment/asr-runtime-b6a", "--timeout=15m",
            ],
            check=False, text=True, capture_output=True,
        )
        if available.returncode != 0:
            raise ProofRefusal("B6A workload did not become available")

        port_forward = subprocess.Popen(
            kubectl + [
                "port-forward", "--namespace", "medzen",
                "service/asr-runtime-b6a", "18081:8081",
                "--address", "127.0.0.1",
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        ready_payload = None
        stop = time.monotonic() + 60
        while time.monotonic() < stop:
            if port_forward.poll() is not None:
                raise ProofRefusal("internal port-forward stopped")
            try:
                code, candidate = _http_json("GET", "/readyz")
                if code == 200 and candidate.get("ready") is True:
                    ready_payload = candidate
                    break
            except (OSError, ProofRefusal):
                pass
            time.sleep(1)
        if ready_payload is None:
            raise ProofRefusal("ASR readiness proof failed")
        expected_ready = {
            "ready", "classification", "model_manifest_verified", "model_tree_verified",
            "model_loaded", "smoke_inference_passed", "platform_test_disclosure_loaded",
        }
        if set(ready_payload) != expected_ready or not all(
            ready_payload[key] is True for key in expected_ready - {"classification"}
        ) or ready_payload["classification"] != "PLATFORM_PROOF_ONLY":
            raise ProofRefusal("ASR readiness disclosure differs")

        code, payload = _http_json(
            "POST", "/internal/v1/transcriptions",
            body=audio.read_bytes(),
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": str(audio.stat().st_size),
                "X-Request-ID": REQUEST_ID,
                "X-MedZen-Language": "en",
            },
        )
        if code != 200:
            raise ProofRefusal("internal transcription did not return HTTP 200")
        validate_response(payload)

        workload_pods = _run_json(kubectl + [
            "get", "pods", "--namespace", "medzen",
            "-l", "app.kubernetes.io/name=asr-runtime-b6a", "-o", "json",
        ]).get("items", [])
        if len(workload_pods) != 1:
            raise ProofRefusal("exact B6A workload Pod is absent")
        workload_pod_record = workload_pods[0]
        validate_workload_pod_images(workload_pod_record)
        workload_pod = workload_pod_record["metadata"]["name"]
        logs = []
        for container in ("model-loader", "asr-runtime"):
            completed = subprocess.run(
                kubectl + [
                    "logs", "--namespace", "medzen", workload_pod, "-c", container,
                ],
                check=False, text=True, capture_output=True,
            )
            if completed.returncode != 0:
                raise ProofRefusal("PHI-safe log review could not be completed")
            logs.append(completed.stdout)
        validate_logs("\n".join(logs), payload)
    finally:
        _stop(port_forward)
        _stop(sampler)
        reader.join(timeout=5)

    memory = parse_memory_samples(samples)
    return {
        "status": "B6A_PLATFORM_PROOF_PASSED_PENDING_CLEANUP",
        "request_id": REQUEST_ID,
        "audio": {"sha256": AUDIO_SHA, "bytes": AUDIO_BYTES, "synthetic": True},
        "readiness": ready_payload,
        "response": payload,
        "gpu_memory": memory,
        "logs_phi_safe": True,
        "network_path": "kubectl_port_forward_loopback_only_no_public_endpoint",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        for path in (args.kubeconfig, args.workload, args.audio, args.authorization):
            if not path.is_file():
                raise ProofRefusal(f"required file is absent: {path}")
        workload_sha = _sha(args.workload)
        _authorization(args.authorization, args.packet_sha256, workload_sha)
        if _sha(args.audio) != AUDIO_SHA or args.audio.stat().st_size != AUDIO_BYTES:
            raise ProofRefusal("synthetic audio identity differs")
        if args.receipt.exists():
            raise ProofRefusal("refusing to overwrite proof receipt")
        result = run_proof(
            kubeconfig=args.kubeconfig, workload=args.workload, audio=args.audio
        )
        args.receipt.write_text(json.dumps(result, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
