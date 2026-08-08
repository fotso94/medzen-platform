#!/usr/bin/env python3
"""Run separate, durable B6A transcription and GPU-memory proofs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pipeline.runtime_receipts_v2 import ReceiptStore, canonical_json
from scripts import run_b6a_003c_b_proof as base


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "platform/runtime-receipt-policy-v2.yaml"


class MeasurementIncomplete(RuntimeError):
    pass


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _start_port_forward(kubectl: list[str]) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        kubectl + [
            "port-forward", "--namespace", "medzen",
            "service/asr-runtime-b6a", "18081:8081", "--address", "127.0.0.1",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stop = time.monotonic() + 60
    while time.monotonic() < stop:
        if process.poll() is not None:
            raise base.ProofRefusal("internal port-forward stopped")
        try:
            code, payload = base._http_json("GET", "/readyz")
            if code == 200 and payload.get("ready") is True:
                return process
        except (OSError, base.ProofRefusal):
            pass
        time.sleep(1)
    base._stop(process)
    raise base.ProofRefusal("ASR readiness proof failed")


def _ready_payload() -> dict[str, Any]:
    code, payload = base._http_json("GET", "/readyz")
    expected = {
        "ready", "classification", "model_manifest_verified", "model_tree_verified",
        "model_loaded", "smoke_inference_passed", "platform_test_disclosure_loaded",
    }
    if code != 200 or set(payload) != expected:
        raise base.ProofRefusal("ASR readiness disclosure fields differ")
    if payload.get("classification") != "PLATFORM_PROOF_ONLY" or not all(
        payload[key] is True for key in expected - {"classification"}
    ):
        raise base.ProofRefusal("ASR readiness disclosure differs")
    return payload


def _transcribe(audio: Path) -> dict[str, Any]:
    code, payload = base._http_json(
        "POST",
        "/internal/v1/transcriptions",
        body=audio.read_bytes(),
        headers={
            "Content-Type": "audio/wav",
            "Content-Length": str(audio.stat().st_size),
            "X-Request-ID": base.REQUEST_ID,
            "X-MedZen-Language": "en",
        },
    )
    if code != 200:
        raise base.ProofRefusal("internal transcription did not return HTTP 200")
    base.validate_response(payload)
    return payload


def _workload_pod(kubectl: list[str]) -> dict[str, Any]:
    items = base._run_json(kubectl + [
        "get", "pods", "--namespace", "medzen",
        "-l", "app.kubernetes.io/name=asr-runtime-b6a", "-o", "json",
    ]).get("items", [])
    if len(items) != 1:
        raise base.ProofRefusal("exact B6A workload Pod is absent")
    base.validate_workload_pod_images(items[0])
    return items[0]


def _validate_phi_safe_logs(kubectl: list[str], pod: dict[str, Any], payload: dict[str, Any]) -> None:
    logs = []
    name = pod["metadata"]["name"]
    for container in ("model-loader", "asr-runtime"):
        completed = subprocess.run(
            kubectl + ["logs", "--namespace", "medzen", name, "-c", container],
            check=False,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise base.ProofRefusal("PHI-safe log review could not be completed")
        logs.append(completed.stdout)
    base.validate_logs("\n".join(logs), payload)


def transcription_receipt_payload(
    payload: dict[str, Any], readiness: dict[str, Any], pod: dict[str, Any]
) -> dict[str, Any]:
    transcript = payload["transcript"]
    return {
        "request_id": base.REQUEST_ID,
        "audio_sha256": base.AUDIO_SHA,
        "audio_bytes": base.AUDIO_BYTES,
        "synthetic_no_phi": True,
        "http_status": 200,
        "response_sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
        "transcript_verbatim_sha256": _hash_text(transcript["verbatim"]),
        "transcript_normalized_sha256": _hash_text(transcript["normalized"]),
        "normalization_version": transcript["normalization_version"],
        "readiness_sha256": hashlib.sha256(canonical_json(readiness)).hexdigest(),
        "model_loader_image": base.MODEL_LOADER_IMAGE,
        "asr_runtime_image": base.ASR_RUNTIME_IMAGE,
        "workload_pod_uid": pod["metadata"]["uid"],
        "logs_phi_safe": True,
        "network_path": "kubectl_port_forward_loopback_only_no_public_endpoint",
        "transcript_text_persisted": False,
    }


def _dra_pod(kubectl: list[str]) -> str:
    pods = base._run_json(kubectl + [
        "get", "pods", "--namespace", "nvidia-dra-driver",
        "-l", "dra-driver-nvidia-gpu-component=kubelet-plugin", "-o", "json",
    ]).get("items", [])
    if len(pods) != 1:
        raise MeasurementIncomplete("exact DRA sampler Pod is absent")
    return pods[0]["metadata"]["name"]


def measure_gpu_memory(
    *, kubectl: list[str], dra_pod: str, audio: Path
) -> dict[str, Any]:
    samples: list[str] = []
    sampler = subprocess.Popen(
        kubectl + [
            "exec", "--namespace", "nvidia-dra-driver", dra_pod, "-c", "gpus", "--",
            "/busybox/sh", "-c",
            "while true; do /busybox/chroot /driver-root /usr/bin/nvidia-smi "
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
    try:
        stop = time.monotonic() + 30
        while time.monotonic() < stop and sampler.poll() is None:
            try:
                base.parse_memory_samples(samples)
                break
            except base.ProofRefusal:
                time.sleep(0.2)
        else:
            raise MeasurementIncomplete("parsed GPU memory baseline is absent")
        # This second synthetic request is the independent measurement exercise.
        # The first transcription is already fsync-durable before this sampler starts.
        _transcribe(audio)
        time.sleep(2)
    except MeasurementIncomplete:
        raise
    except Exception as exc:
        raise MeasurementIncomplete("memory exercise request failed") from exc
    finally:
        base._stop(sampler)
        reader.join(timeout=5)
    try:
        result = base.parse_memory_samples(samples)
    except base.ProofRefusal as exc:
        raise MeasurementIncomplete("numeric GPU memory result is absent") from exc
    result.update({
        "sampling_path": "nvidia-dra:/busybox/chroot:/driver-root/usr/bin/nvidia-smi",
        "measurement_scope": "loaded_v0_model_plus_second_synthetic_inference",
        "transcription_receipt_existed_before_sampler_start": True,
        "raw_sampler_stdout_or_stderr_persisted": False,
    })
    return result


def run(
    *, kubeconfig: Path, workload: Path, audio: Path, store: ReceiptStore
) -> int:
    store.require_pass("sampler_self_test")
    kubectl = ["kubectl", "--kubeconfig", str(kubeconfig)]
    applied = subprocess.run(
        kubectl + ["apply", "--filename", str(workload)],
        check=False,
        text=True,
        capture_output=True,
    )
    if applied.returncode != 0:
        raise base.ProofRefusal("B6A workload apply failed")
    available = subprocess.run(
        kubectl + [
            "wait", "--namespace", "medzen", "--for=condition=available",
            "deployment/asr-runtime-b6a", "--timeout=15m",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if available.returncode != 0:
        raise base.ProofRefusal("B6A workload did not become available")

    port_forward: subprocess.Popen[str] | None = None
    try:
        port_forward = _start_port_forward(kubectl)
        readiness = _ready_payload()
        payload = _transcribe(audio)
        pod = _workload_pod(kubectl)
        _validate_phi_safe_logs(kubectl, pod, payload)

        # Standing rule: this exclusive, fsync-backed receipt is durable before
        # the independent GPU-memory sampler is even constructed.
        transcription = store.persist(
            "transcription",
            "PASS",
            transcription_receipt_payload(payload, readiness, pod),
        )
        try:
            memory = measure_gpu_memory(
                kubectl=kubectl, dra_pod=_dra_pod(kubectl), audio=audio
            )
            memory_receipt = store.persist("gpu_memory_measurement", "PASS", memory)
            summary = store.persist(
                "proof_summary",
                "PASS",
                {
                    "outcome": "B6A_PLATFORM_PROOFS_PASSED_PENDING_CLEANUP",
                    "transcription_receipt_sha256": transcription["receipt_sha256"],
                    "memory_receipt_sha256": memory_receipt["receipt_sha256"],
                },
                dependencies=("transcription", "gpu_memory_measurement"),
            )
            print(json.dumps({
                "status": "PASS_PENDING_CLEANUP",
                "proof_summary_sha256": summary["receipt_sha256"],
            }, sort_keys=True))
            return 0
        except Exception as exc:
            memory_receipt = store.persist(
                "gpu_memory_measurement",
                "INCOMPLETE_MEASUREMENT",
                {
                    "reason_code": type(exc).__name__,
                    "transcription_receipt_preserved": True,
                    "raw_sampler_stdout_or_stderr_persisted": False,
                },
            )
            summary = store.persist(
                "proof_summary",
                "INCOMPLETE_MEASUREMENT",
                {
                    "outcome": "INCOMPLETE_MEASUREMENT",
                    "transcription_status": "PASS",
                    "transcription_receipt_sha256": transcription["receipt_sha256"],
                    "memory_receipt_sha256": memory_receipt["receipt_sha256"],
                    "transcription_voided": False,
                },
            )
            print(json.dumps({
                "status": "INCOMPLETE_MEASUREMENT",
                "transcription_status": "PASS",
                "proof_summary_sha256": summary["receipt_sha256"],
            }, sort_keys=True))
            return 3
    finally:
        base._stop(port_forward)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    args = parser.parse_args()
    store = ReceiptStore(args.receipts_dir, policy_path=POLICY)
    try:
        for path in (args.kubeconfig, args.workload, args.audio):
            if not path.is_file():
                raise base.ProofRefusal(f"required file is absent: {path}")
        if hashlib.sha256(args.workload.read_bytes()).hexdigest() != (
            "9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68"
        ):
            raise base.ProofRefusal("workload render SHA-256 differs")
        if hashlib.sha256(args.audio.read_bytes()).hexdigest() != base.AUDIO_SHA:
            raise base.ProofRefusal("synthetic audio SHA-256 differs")
        return run(kubeconfig=args.kubeconfig, workload=args.workload, audio=args.audio, store=store)
    except Exception as exc:
        if not store.path("transcription").exists():
            store.persist(
                "transcription",
                "REFUSED",
                {"reason_code": type(exc).__name__, "transcript_text_persisted": False},
                dependencies=("sampler_self_test",) if store.path("sampler_self_test").exists() else (),
            )
        if not store.path("proof_summary").exists():
            store.persist(
                "proof_summary",
                "REFUSED",
                {"outcome": "BLOCKED_PLATFORM_PROOF", "reason_code": type(exc).__name__},
                dependencies=(),
            )
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
