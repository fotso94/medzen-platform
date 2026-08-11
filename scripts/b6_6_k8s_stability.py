#!/usr/bin/env python3
"""Bounded stable-observation gates for B6.6 Kubernetes mutations."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


STABLE_OBSERVATIONS = 3
POLL_SECONDS = 5
MAXIMUM_WAIT_SECONDS = 1800
WINDOW_DEPLOYMENTS = {
    "rag-index",
    "asr-runtime",
    "tts-gateway",
    "llm-gateway",
    "speech-orchestrator",
}
WINDOW_SERVICES = set(WINDOW_DEPLOYMENTS)
WINDOW_INGRESS = "speech-orchestrator-b6-window"


class StabilityRefusal(RuntimeError):
    pass


class StabilityPending(RuntimeError):
    pass


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def observe_stably(
    observe: Callable[[], dict[str, Any]],
    wait_seconds: int,
    *,
    stable_observations: int = STABLE_OBSERVATIONS,
    poll_seconds: int = POLL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if (
        wait_seconds < 1
        or wait_seconds > MAXIMUM_WAIT_SECONDS
        or stable_observations != STABLE_OBSERVATIONS
        or poll_seconds != POLL_SECONDS
    ):
        raise StabilityRefusal("stable-observation boundary differs")
    deadline = monotonic() + wait_seconds
    stable_hash: str | None = None
    consecutive = 0
    polls = 0
    last_pending = "resource not ready"
    while True:
        polls += 1
        try:
            observed = observe()
            observed_hash = canonical_sha256(observed)
            if observed_hash == stable_hash:
                consecutive += 1
            else:
                stable_hash = observed_hash
                consecutive = 1
            if consecutive == stable_observations:
                return {
                    **observed,
                    "stable_observations": consecutive,
                    "verification_polls": polls,
                    "poll_interval_seconds": poll_seconds,
                }
        except StabilityPending as exc:
            stable_hash = None
            consecutive = 0
            last_pending = str(exc)
        if monotonic() >= deadline:
            raise StabilityRefusal(
                f"stable observation timed out: {last_pending}"
            )
        sleep(poll_seconds)


def kubectl_json(kubeconfig: Path, arguments: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            *arguments,
            "--request-timeout=15s",
            "-o",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise StabilityPending("Kubernetes read is not available")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise StabilityRefusal("Kubernetes response is malformed") from exc
    if not isinstance(value, dict):
        raise StabilityRefusal("Kubernetes response is not an object")
    return value


def deployment_observation(
    value: dict[str, Any], expected_replicas: int, expected_images: set[str]
) -> dict[str, Any]:
    metadata = value.get("metadata", {})
    spec = value.get("spec", {})
    status = value.get("status", {})
    generation = metadata.get("generation")
    observed_generation = status.get("observedGeneration")
    if not isinstance(generation, int) or not isinstance(observed_generation, int):
        raise StabilityPending("deployment generation is not observed")
    if spec.get("replicas") != expected_replicas:
        raise StabilityRefusal("deployment desired replicas differ")
    images = {
        str(item.get("image"))
        for item in [
            *spec.get("template", {}).get("spec", {}).get("initContainers", []),
            *spec.get("template", {}).get("spec", {}).get("containers", []),
        ]
    }
    if expected_images and images != expected_images:
        raise StabilityRefusal("deployment image set differs")
    ready = {
        "generation": generation,
        "observed_generation": observed_generation,
        "replicas": status.get("replicas", 0),
        "updated_replicas": status.get("updatedReplicas", 0),
        "ready_replicas": status.get("readyReplicas", 0),
        "available_replicas": status.get("availableReplicas", 0),
        "unavailable_replicas": status.get("unavailableReplicas", 0),
        "image_set_sha256": canonical_sha256(sorted(images)),
    }
    if (
        observed_generation < generation
        or any(
            ready[key] != expected_replicas
            for key in (
                "replicas",
                "updated_replicas",
                "ready_replicas",
                "available_replicas",
            )
        )
        or ready["unavailable_replicas"] != 0
    ):
        raise StabilityPending("deployment is not stably available")
    return ready


def daemonset_observation(
    value: dict[str, Any], expected_image: str
) -> dict[str, Any]:
    metadata = value.get("metadata", {})
    spec = value.get("spec", {})
    status = value.get("status", {})
    generation = metadata.get("generation")
    observed_generation = status.get("observedGeneration")
    images = {
        str(item.get("image"))
        for item in [
            *spec.get("template", {}).get("spec", {}).get("initContainers", []),
            *spec.get("template", {}).get("spec", {}).get("containers", []),
        ]
    }
    if images != {expected_image}:
        raise StabilityRefusal("daemonset image differs")
    desired = status.get("desiredNumberScheduled", 0)
    observed = {
        "generation": generation,
        "observed_generation": observed_generation,
        "desired": desired,
        "current": status.get("currentNumberScheduled", 0),
        "updated": status.get("updatedNumberScheduled", 0),
        "ready": status.get("numberReady", 0),
        "available": status.get("numberAvailable", 0),
        "unavailable": status.get("numberUnavailable", 0),
        "image": expected_image,
    }
    if (
        not isinstance(generation, int)
        or not isinstance(observed_generation, int)
        or observed_generation < generation
        or not isinstance(desired, int)
        or desired < 1
        or any(observed[key] != desired for key in ("current", "updated", "ready", "available"))
        or observed["unavailable"] != 0
    ):
        raise StabilityPending("daemonset is not stably available")
    return observed


def pod_image_observation(value: dict[str, Any], expected: set[str]) -> dict[str, Any]:
    items = value.get("items")
    if not isinstance(items, list):
        raise StabilityRefusal("pod list is malformed")
    digests: set[str] = set()
    for pod in items:
        statuses = [
            *pod.get("status", {}).get("initContainerStatuses", []),
            *pod.get("status", {}).get("containerStatuses", []),
        ]
        for status in statuses:
            match = re.search(r"sha256:[0-9a-f]{64}$", str(status.get("imageID", "")))
            if match:
                digests.add(match.group(0))
    if digests != expected:
        raise StabilityPending("resident pod image set differs")
    return {
        "pod_count": len(items),
        "resident_child_digests": sorted(digests),
        "resident_child_digest_count": len(digests),
    }


def endpoint_observation(value: dict[str, Any], expected_count: int) -> dict[str, Any]:
    subsets = value.get("subsets", [])
    count = sum(len(item.get("addresses", [])) for item in subsets)
    if count != expected_count:
        raise StabilityPending("endpoint address count differs")
    return {"endpoint_address_count": count}


def wait_deployment(args: argparse.Namespace) -> dict[str, Any]:
    expected_images = set(args.expected_image or [])
    return observe_stably(
        lambda: deployment_observation(
            kubectl_json(
                args.kubeconfig,
                ["get", f"deployment/{args.name}", "--namespace", args.namespace],
            ),
            args.replicas,
            expected_images,
        ),
        args.wait_seconds,
    )


def wait_daemonset(args: argparse.Namespace) -> dict[str, Any]:
    return observe_stably(
        lambda: daemonset_observation(
            kubectl_json(
                args.kubeconfig,
                ["get", f"daemonset/{args.name}", "--namespace", args.namespace],
            ),
            args.expected_image,
        ),
        args.wait_seconds,
    )


def wait_pod_images(args: argparse.Namespace) -> dict[str, Any]:
    expected = set(args.expected_digest)
    return observe_stably(
        lambda: pod_image_observation(
            kubectl_json(
                args.kubeconfig,
                ["get", "pods", "--namespace", args.namespace, "-l", args.selector],
            ),
            expected,
        ),
        args.wait_seconds,
    )


def wait_endpoints(args: argparse.Namespace) -> dict[str, Any]:
    return observe_stably(
        lambda: endpoint_observation(
            kubectl_json(
                args.kubeconfig,
                ["get", f"endpoints/{args.name}", "--namespace", args.namespace],
            ),
            args.count,
        ),
        args.wait_seconds,
    )


def zero_observation(kubeconfig: Path) -> dict[str, Any]:
    nodes = kubectl_json(
        kubeconfig, ["get", "nodes", "-l", "workload in (cpu,gpu)"]
    ).get("items", [])
    pods = kubectl_json(
        kubeconfig,
        [
            "get",
            "pods",
            "--namespace",
            "medzen",
            "-l",
            "medzen.io/classification=synthetic-integration-only",
        ],
    ).get("items", [])
    ingresses = kubectl_json(kubeconfig, ["get", "ingress", "--all-namespaces"]).get(
        "items", []
    )
    deployments = kubectl_json(
        kubeconfig, ["get", "deployments", "--namespace", "medzen"]
    ).get("items", [])
    counts = {
        "workload_nodes": len(nodes),
        "synthetic_pods": len(pods),
        "window_ingresses": sum(
            item.get("metadata", {}).get("name") == "speech-orchestrator-b6-window"
            for item in ingresses
        ),
        "window_deployments": sum(
            item.get("metadata", {}).get("name") in WINDOW_DEPLOYMENTS
            for item in deployments
        ),
    }
    if any(counts.values()):
        raise StabilityPending("Kubernetes window state is not zero")
    return counts


def wait_zero(args: argparse.Namespace) -> dict[str, Any]:
    return observe_stably(
        lambda: zero_observation(args.kubeconfig), args.wait_seconds
    )


def isolation_observation(kubeconfig: Path) -> dict[str, Any]:
    services = kubectl_json(
        kubeconfig, ["get", "services", "--namespace", "medzen"]
    ).get("items", [])
    ingresses = kubectl_json(
        kubeconfig, ["get", "ingress", "--namespace", "medzen"]
    ).get("items", [])
    selected_services = {
        str(item.get("metadata", {}).get("name")): item.get("spec", {}).get("type")
        for item in services
        if item.get("metadata", {}).get("name") in WINDOW_SERVICES
    }
    selected_ingresses = [
        item
        for item in ingresses
        if item.get("metadata", {}).get("name") == WINDOW_INGRESS
    ]
    if selected_services != {name: "ClusterIP" for name in WINDOW_SERVICES}:
        raise StabilityPending("dependency service isolation differs")
    if len(selected_ingresses) != 1:
        raise StabilityPending("orchestrator ingress cardinality differs")
    return {
        "dependency_service_type": "ClusterIP",
        "dependency_service_count": len(WINDOW_SERVICES) - 1,
        "orchestrator_service_type": "ClusterIP",
        "orchestrator_ingresses": 1,
        "dependency_ingresses": 0,
    }


def wait_isolation(args: argparse.Namespace) -> dict[str, Any]:
    return observe_stably(
        lambda: isolation_observation(args.kubeconfig), args.wait_seconds
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="mode", required=True)
    for mode in ("deployment", "daemonset", "pod-images", "endpoints"):
        sub = subparsers.add_parser(mode)
        sub.add_argument("--kubeconfig", type=Path, required=True)
        sub.add_argument("--namespace", required=True)
        sub.add_argument("--name", required=mode != "pod-images")
        sub.add_argument("--wait-seconds", type=int, required=True)
        if mode == "deployment":
            sub.add_argument("--replicas", type=int, required=True)
            sub.add_argument("--expected-image", action="append")
        elif mode == "daemonset":
            sub.add_argument("--expected-image", required=True)
        elif mode == "pod-images":
            sub.add_argument("--selector", required=True)
            sub.add_argument("--expected-digest", action="append", required=True)
        else:
            sub.add_argument("--count", type=int, required=True)
    zero = subparsers.add_parser("window-zero")
    zero.add_argument("--kubeconfig", type=Path, required=True)
    zero.add_argument("--wait-seconds", type=int, required=True)
    isolation = subparsers.add_parser("isolation")
    isolation.add_argument("--kubeconfig", type=Path, required=True)
    isolation.add_argument("--wait-seconds", type=int, required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.mode == "deployment":
            result = wait_deployment(args)
        elif args.mode == "daemonset":
            result = wait_daemonset(args)
        elif args.mode == "pod-images":
            result = wait_pod_images(args)
        elif args.mode == "endpoints":
            result = wait_endpoints(args)
        elif args.mode == "window-zero":
            result = wait_zero(args)
        else:
            result = wait_isolation(args)
    except (OSError, subprocess.SubprocessError, StabilityRefusal) as exc:
        print(
            json.dumps(
                {
                    "status": "REFUSED",
                    "reason_code": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps({"status": "PASS", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
