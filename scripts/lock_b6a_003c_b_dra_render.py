#!/usr/bin/env python3
"""Lock an already rendered NVIDIA DRA chart to the scan-passed manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


TAGGED_IMAGE = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-nvidia-dra:"
    "v0.4.1-medzen.2-7fb313758a20"
)
LOCKED_IMAGE = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-nvidia-dra"
    "@sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246"
)


class DRARenderRefusal(RuntimeError):
    pass


def _lock(value: Any) -> tuple[Any, int]:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        count = 0
        for key, child in value.items():
            output[key], changed = _lock(child)
            count += changed
        return output, count
    if isinstance(value, list):
        output = []
        count = 0
        for child in value:
            locked, changed = _lock(child)
            output.append(locked)
            count += changed
        return output, count
    if value == TAGGED_IMAGE:
        return LOCKED_IMAGE, 1
    return value, 0


def _images(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image" and isinstance(child, str):
                found.append(child)
            found.extend(_images(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_images(child))
    return found


def lock_render(raw: bytes) -> bytes:
    documents = [item for item in yaml.safe_load_all(raw) if item is not None]
    if not documents:
        raise DRARenderRefusal("DRA render contains no objects")
    minimized: list[dict[str, Any]] = [{
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": "nvidia-dra-driver",
            "labels": {"medzen.io/change-packet": "B6A-AWS-CHANGE-PACKET-2026-003C-B"},
        },
    }]
    workload_kinds = {"DaemonSet", "Deployment", "StatefulSet", "Pod", "Job"}
    for document in documents:
        kind = str(document.get("kind", ""))
        name = str(document.get("metadata", {}).get("name", ""))
        if kind in workload_kinds and (
            "compute-domain" in name or "controller" in name
        ):
            raise DRARenderRefusal("compute-domain workload rendered unexpectedly")
        if "compute-domain" in name or "controller" in name:
            continue
        if kind == "DeviceClass" and name != "gpu.nvidia.com":
            continue
        if kind in {"ClusterRole", "Role"}:
            rules = []
            for rule in document.get("rules", []):
                if "resource.nvidia.com" in rule.get("apiGroups", []):
                    continue
                rules.append(rule)
            document["rules"] = rules
        minimized.append(document)
    empty_roles = {
        str(item.get("metadata", {}).get("name", ""))
        for item in minimized
        if item.get("kind") in {"ClusterRole", "Role"} and not item.get("rules")
    }
    minimized = [
        item for item in minimized
        if str(item.get("metadata", {}).get("name", "")) not in empty_roles
        and str(item.get("roleRef", {}).get("name", "")) not in empty_roles
    ]
    documents = minimized
    locked: list[dict[str, Any]] = []
    replacements = 0
    for document in documents:
        result, changed = _lock(document)
        locked.append(result)
        replacements += changed
    if replacements < 3:
        raise DRARenderRefusal("expected DRA image references were not all present")

    serialized = yaml.safe_dump_all(locked, sort_keys=False)
    if TAGGED_IMAGE in serialized or ":latest" in serialized:
        raise DRARenderRefusal("mutable or unlocked DRA image reference remains")
    images = _images(locked)
    if not images or set(images) != {LOCKED_IMAGE}:
        raise DRARenderRefusal("DRA workload contains an unexpected image")

    names = [str(item.get("metadata", {}).get("name", "")) for item in locked]
    if any("compute-domain" in name or "controller" in name for name in names):
        raise DRARenderRefusal("compute-domain resource rendered unexpectedly")
    if any(item.get("kind") in {"Ingress", "Gateway", "HTTPRoute"} for item in locked):
        raise DRARenderRefusal("public DRA routing object is forbidden")

    classes = [item for item in locked if item.get("kind") == "DeviceClass"]
    if [item.get("metadata", {}).get("name") for item in classes] != ["gpu.nvidia.com"]:
        raise DRARenderRefusal("exact gpu.nvidia.com DeviceClass is required")
    daemonsets = [item for item in locked if item.get("kind") == "DaemonSet"]
    if len(daemonsets) != 1:
        raise DRARenderRefusal("exactly one DRA kubelet DaemonSet is required")
    pod = daemonsets[0]["spec"]["template"]["spec"]
    if pod.get("nodeSelector") != {"workload": "gpu"}:
        raise DRARenderRefusal("DRA DaemonSet is not restricted to workload=gpu")
    container_names = {item["name"] for item in pod.get("containers", [])}
    if container_names != {"gpus"}:
        raise DRARenderRefusal("only the GPU DRA container is permitted")
    if any(item.get("kind") == "Deployment" for item in locked):
        raise DRARenderRefusal("compute-domain controller Deployment is forbidden")

    service_account = pod.get("serviceAccountName")
    if not isinstance(service_account, str) or not service_account:
        raise DRARenderRefusal("DRA DaemonSet service account is absent")
    policies = [
        item for item in locked if item.get("kind") == "ValidatingAdmissionPolicy"
    ]
    if len(policies) != 1:
        raise DRARenderRefusal("exactly one ResourceSlice validation policy is required")
    expected_identity = (
        "request.userInfo.username == "
        f'"system:serviceaccount:nvidia-dra-driver:{service_account}"'
    )
    conditions = policies[0].get("spec", {}).get("matchConditions", [])
    restricted = [item for item in conditions if item.get("name") == "isRestrictedUser"]
    if len(restricted) != 1:
        raise DRARenderRefusal("DRA restricted-user match condition is absent or ambiguous")
    # Chart 0.4.1 can render a generic service-account helper while the
    # kubelet-only DaemonSet uses its component-specific account. Lock the
    # admission subject to the account that actually creates ResourceSlices.
    restricted[0]["expression"] = expected_identity

    bindings = [
        item for item in locked
        if item.get("kind") == "ValidatingAdmissionPolicyBinding"
    ]
    if len(bindings) != 1:
        raise DRARenderRefusal("exactly one ResourceSlice policy binding is required")
    if bindings[0].get("spec", {}).get("policyName") != policies[0]["metadata"]["name"]:
        raise DRARenderRefusal("DRA policy binding does not name the locked policy")

    return yaml.safe_dump_all(
        locked, explicit_start=True, sort_keys=False, width=1000
    ).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise DRARenderRefusal("refusing to overwrite an existing render")
        result = lock_render(args.input.read_bytes())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(result)
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    print(json.dumps({
        "status": "LOCKED_NOT_APPLIED",
        "bytes": len(result),
        "sha256": hashlib.sha256(result).hexdigest(),
        "output": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
