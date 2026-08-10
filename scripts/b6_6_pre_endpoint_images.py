#!/usr/bin/env python3
"""Prove every B6.6 Kubernetes image is resident before private ECR DNS."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable


EXPECTED = {
    ("kube-system", "aws-load-balancer-controller"): {
        "sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f"
    },
    ("nvidia-dra-driver", "dra-driver-nvidia-gpu"): {
        "sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246"
    },
    ("medzen", "rag-index"): {
        "sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"
    },
    ("medzen", "asr-runtime"): {
        "sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5",
        "sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087",
    },
    ("medzen", "tts-gateway"): {
        "sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d"
    },
    ("medzen", "llm-gateway"): {
        "sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a"
    },
    ("medzen", "speech-orchestrator"): {
        "sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa"
    },
}
PULL_FAILURE_REASONS = {"ErrImagePull", "ImagePullBackOff", "RegistryUnavailable"}


class ImageReadinessRefusal(RuntimeError):
    pass


def _identity(pod: dict[str, Any]) -> tuple[str, str] | None:
    metadata = pod.get("metadata", {})
    namespace = metadata.get("namespace")
    name = metadata.get("name")
    labels = metadata.get("labels", {})
    application = labels.get("app.kubernetes.io/name")
    if namespace == "kube-system" and application == "aws-load-balancer-controller":
        return namespace, application
    if namespace == "nvidia-dra-driver" and application == "dra-driver-nvidia-gpu":
        return namespace, application
    if namespace == "medzen" and application in {
        "rag-index", "asr-runtime", "tts-gateway", "llm-gateway", "speech-orchestrator"
    }:
        return namespace, application
    if name and namespace:
        return None
    return None


def _digest(value: str) -> str:
    marker = "sha256:"
    index = value.rfind(marker)
    if index < 0:
        raise ImageReadinessRefusal("image is not digest-pinned")
    digest = value[index:]
    if len(digest) != 71 or any(char not in "0123456789abcdef" for char in digest[7:]):
        raise ImageReadinessRefusal("image digest is malformed")
    return digest


def _statuses(pod: dict[str, Any]) -> list[dict[str, Any]]:
    status = pod.get("status", {})
    return [
        *status.get("initContainerStatuses", []),
        *status.get("containerStatuses", []),
    ]


def verify_pre_endpoint(pods: list[dict[str, Any]]) -> dict[str, Any]:
    selected: dict[tuple[str, str], list[dict[str, Any]]] = {
        identity: [] for identity in EXPECTED
    }
    for pod in pods:
        identity = _identity(pod)
        if identity in selected:
            selected[identity].append(pod)
    if any(len(matches) != 1 for matches in selected.values()):
        counts = {f"{key[0]}/{key[1]}": len(value) for key, value in selected.items()}
        raise ImageReadinessRefusal(f"expected pod cardinality differs: {counts}")

    proof = []
    for identity, matches in sorted(selected.items()):
        pod = matches[0]
        metadata = pod["metadata"]
        spec = pod.get("spec", {})
        status = pod.get("status", {})
        if status.get("phase") != "Running" or not spec.get("nodeName"):
            raise ImageReadinessRefusal(f"pod is not Running on a node: {identity}")
        conditions = {item.get("type"): item.get("status") for item in status.get("conditions", [])}
        if conditions.get("Ready") != "True":
            raise ImageReadinessRefusal(f"pod is not Ready: {identity}")

        spec_images = {
            _digest(container.get("image", ""))
            for container in [
                *spec.get("initContainers", []),
                *spec.get("containers", []),
            ]
        }
        statuses = _statuses(pod)
        status_images = {_digest(item.get("imageID", "")) for item in statuses}
        expected = EXPECTED[identity]
        if spec_images != expected or status_images != expected or not statuses:
            raise ImageReadinessRefusal(f"resident image digest set differs: {identity}")
        for item in status.get("containerStatuses", []):
            if item.get("ready") is not True or "running" not in item.get("state", {}):
                raise ImageReadinessRefusal(f"application container is not running: {identity}")
        for item in status.get("initContainerStatuses", []):
            state = item.get("state", {})
            complete = state.get("terminated", {}).get("exitCode") == 0
            if not complete and "running" not in state:
                raise ImageReadinessRefusal(f"init container is not resident and complete: {identity}")
        proof.append({
            "namespace": identity[0],
            "application": identity[1],
            "pod": metadata["name"],
            "node": spec["nodeName"],
            "phase": "Running",
            "ready": True,
            "resident_child_digests": sorted(expected),
        })
    return {
        "status": "PASS",
        "private_ecr_endpoints_present": False,
        "pod_count": len(proof),
        "application_count": len(EXPECTED),
        "all_pods_running_and_ready": True,
        "all_images_present_on_scheduled_nodes": True,
        "proof": proof,
    }


def classify_post_endpoint_failure(pods: list[dict[str, Any]]) -> dict[str, Any]:
    failures = []
    for pod in pods:
        identity = _identity(pod)
        metadata = pod.get("metadata", {})
        namespace = metadata.get("namespace", "unknown")
        application = (
            identity[1]
            if identity in EXPECTED
            else metadata.get("labels", {}).get("app.kubernetes.io/name", "unlabeled")
        )
        for item in _statuses(pod):
            reason = item.get("state", {}).get("waiting", {}).get("reason")
            if reason in PULL_FAILURE_REASONS:
                failures.append({
                    "namespace": namespace,
                    "application": application,
                    "pod": metadata.get("name", "unknown"),
                    "container": item.get("name"),
                    "waiting_reason": reason,
                })
    if failures:
        return {
            "status": "REFUSED",
            "reason_code": "POST_ENDPOINT_NEW_KUBERNETES_IMAGE_PULL_FATAL",
            "failures": failures,
        }
    return {
        "status": "NO_KUBERNETES_IMAGE_PULL_FAILURE_OBSERVED",
        "reason_code": "PRIMARY_STAGE_REASON_RETAINED",
        "failures": [],
    }


def _pods(kubeconfig: Path, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> list[dict[str, Any]]:
    result = runner(
        ["kubectl", "--kubeconfig", str(kubeconfig), "get", "pods", "-A", "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    if not isinstance(value.get("items"), list):
        raise ImageReadinessRefusal("kubectl pod response is malformed")
    return value["items"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("pre", "post-failure"))
    parser.add_argument("--kubeconfig", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "pre":
            result = verify_pre_endpoint(_pods(args.kubeconfig))
            code = 0
        else:
            result = classify_post_endpoint_failure(_pods(args.kubeconfig))
            code = 3 if result["status"] == "REFUSED" else 0
    except (ImageReadinessRefusal, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
