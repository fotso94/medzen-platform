#!/usr/bin/env python3
"""Render the exact digest-pinned B6A workload without applying it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


MODEL_LOADER_PLACEHOLDER = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-model-loader"
    "@sha256:OWNER_APPROVAL_REQUIRED_ECR_DIGEST"
)
ASR_RUNTIME_PLACEHOLDER = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-runtime"
    "@sha256:OWNER_APPROVAL_REQUIRED_ECR_DIGEST"
)
MODEL_LOADER_IMAGE = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-model-loader"
    "@sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5"
)
ASR_RUNTIME_IMAGE = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-runtime"
    "@sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087"
)
TREE = "5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e"
MANIFEST_SHA = "c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850"


class RenderRefusal(RuntimeError):
    pass


def _one(documents: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matched = [item for item in documents if item.get("kind") == kind]
    if len(matched) != 1:
        raise RenderRefusal(f"expected exactly one {kind}, found {len(matched)}")
    return matched[0]


def _validate(documents: list[dict[str, Any]]) -> None:
    forbidden_kinds = {"Ingress", "Gateway", "HTTPRoute"}
    if any(item.get("kind") in forbidden_kinds for item in documents):
        raise RenderRefusal("public routing object is forbidden")

    namespace = _one(documents, "Namespace")
    if namespace.get("metadata", {}).get("name") != "medzen":
        raise RenderRefusal("B6A namespace differs")

    service = _one(documents, "Service")
    if service.get("spec", {}).get("type") != "ClusterIP":
        raise RenderRefusal("B6A service must be internal ClusterIP")

    network = _one(documents, "NetworkPolicy")
    if network.get("spec", {}).get("ingress") != []:
        raise RenderRefusal("B6A ingress must be deny-all")

    config = _one(documents, "ConfigMap").get("data", {})
    expected_uri = f"s3://medzen-speech/b6a/asr/v0/{TREE}/MANIFEST.json"
    if config.get("MODEL_MANIFEST_S3_URI") != expected_uri:
        raise RenderRefusal("manifest URI differs from exact non-approved artifact")
    if config.get("MODEL_MANIFEST_SHA256") != MANIFEST_SHA:
        raise RenderRefusal("manifest SHA-256 differs")
    if "approved" in json.dumps(config).lower():
        raise RenderRefusal("approved artifact path is forbidden")

    deployment = _one(documents, "Deployment")
    if deployment.get("spec", {}).get("replicas") != 1:
        raise RenderRefusal("B6A deployment must have exactly one replica")
    pod = deployment["spec"]["template"]["spec"]
    if pod.get("nodeSelector") != {"workload": "gpu"}:
        raise RenderRefusal("B6A workload is not restricted to GPU nodes")
    if pod.get("resourceClaims") != [{
        "name": "gpu", "resourceClaimTemplateName": "asr-runtime-b6a-gpu"
    }]:
        raise RenderRefusal("B6A shared GPU claim differs")
    if len(pod.get("initContainers", [])) != 1 or len(pod.get("containers", [])) != 1:
        raise RenderRefusal("B6A permits one loader and one runtime only")
    loader = pod["initContainers"][0]
    runtime = pod["containers"][0]
    if loader.get("image") != MODEL_LOADER_IMAGE:
        raise RenderRefusal("model-loader is not pinned to its scanned child")
    if runtime.get("image") != ASR_RUNTIME_IMAGE:
        raise RenderRefusal("ASR runtime is not pinned to its scanned child")
    if "claims" in loader.get("resources", {}):
        raise RenderRefusal("model-loader must not request a GPU")
    if runtime.get("resources", {}).get("claims") != [{"name": "gpu"}]:
        raise RenderRefusal("ASR runtime must claim the one DRA GPU")
    for container in (loader, runtime):
        security = container.get("securityContext", {})
        if security.get("readOnlyRootFilesystem") is not True:
            raise RenderRefusal("runtime filesystem must be read-only")
        if security.get("allowPrivilegeEscalation") is not False:
            raise RenderRefusal("privilege escalation must be disabled")

    rendered = yaml.safe_dump_all(documents, sort_keys=False)
    if "OWNER_APPROVAL_REQUIRED" in rendered:
        raise RenderRefusal("unresolved image placeholder remains")
    if ":latest" in rendered:
        raise RenderRefusal("mutable latest tag is forbidden")


def render(template: bytes) -> bytes:
    documents = [item for item in yaml.safe_load_all(template) if item is not None]
    if not documents:
        raise RenderRefusal("template contains no Kubernetes objects")
    deployment = _one(documents, "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    loader = pod["initContainers"][0]
    runtime = pod["containers"][0]
    if loader.get("image") != MODEL_LOADER_PLACEHOLDER:
        raise RenderRefusal("model-loader placeholder is missing or ambiguous")
    if runtime.get("image") != ASR_RUNTIME_PLACEHOLDER:
        raise RenderRefusal("ASR placeholder is missing or ambiguous")
    loader["image"] = MODEL_LOADER_IMAGE
    runtime["image"] = ASR_RUNTIME_IMAGE
    deployment.setdefault("metadata", {}).setdefault("annotations", {})[
        "medzen.io/change-packet"
    ] = "B6A-AWS-CHANGE-PACKET-2026-003C-B"
    _validate(documents)
    return yaml.safe_dump_all(
        documents, explicit_start=True, sort_keys=False, width=1000
    ).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise RenderRefusal("refusing to overwrite an existing render")
        result = render(args.template.read_bytes())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(result)
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    print(json.dumps({
        "status": "RENDERED_NOT_APPLIED",
        "bytes": len(result),
        "sha256": hashlib.sha256(result).hexdigest(),
        "output": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
