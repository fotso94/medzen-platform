#!/usr/bin/env python3
"""Render and verify the no-service, strict-egress pilot Kubernetes workload."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
from pathlib import Path
from typing import Any

import yaml


NAMESPACE = "medzen-asr-eval"
LABELS = {
    "app.kubernetes.io/name": "asr-base-model-pilot",
    "medzen.io/classification": "offline-evaluation-only",
}


def _cidr(value: str) -> str:
    return str(ipaddress.ip_network(value, strict=False))


def render(bindings: dict[str, Any], endpoint_ips: list[str], s3_cidrs: list[str], attempt: int) -> str:
    digest = bindings["image"]["linux_amd64_digest"]
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("image digest is malformed")
    if attempt not in {1, 2, 3, 4}:
        raise ValueError("attempt must be 1, 2, 3 or 4")
    endpoint_blocks = sorted({_cidr(f"{ip}/32") for ip in endpoint_ips})
    s3_blocks = sorted({_cidr(value) for value in s3_cidrs})
    if len(endpoint_blocks) < 2 or not s3_blocks:
        raise ValueError("endpoint IP or S3 CIDR bindings are incomplete")
    egress = [
        {
            "to": [{"ipBlock": {"cidr": value}} for value in endpoint_blocks + s3_blocks],
            "ports": [{"protocol": "TCP", "port": 443}],
        },
        {
            "to": [{"ipBlock": {"cidr": "172.31.0.2/32"}}],
            "ports": [
                {"protocol": "UDP", "port": 53},
                {"protocol": "TCP", "port": 53},
            ],
        },
    ]
    image = f"558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-asr-eval-runtime@{digest}"
    documents = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE, "labels": LABELS}},
        {
            "apiVersion": "resource.k8s.io/v1", "kind": "ResourceClaimTemplate",
            "metadata": {"name": "asr-eval-gpu", "namespace": NAMESPACE, "labels": LABELS},
            "spec": {"spec": {"devices": {"requests": [{"name": "gpu", "exactly": {"deviceClassName": "gpu.nvidia.com", "count": 1}}]}}},
        },
        {
            "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "asr-eval-default-deny", "namespace": NAMESPACE},
            "spec": {"podSelector": {"matchLabels": LABELS}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []},
        },
        {
            "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "asr-eval-private-egress", "namespace": NAMESPACE},
            "spec": {"podSelector": {"matchLabels": LABELS}, "policyTypes": ["Egress"], "egress": egress},
        },
        {
            "apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": f"asr-base-model-pilot-a{attempt}", "namespace": NAMESPACE, "labels": LABELS},
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "metadata": {"labels": LABELS},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "hostNetwork": False,
                        "restartPolicy": "Never",
                        "nodeSelector": {"workload": "gpu"},
                        "tolerations": [{"key": "nvidia.com/gpu", "operator": "Equal", "value": "true", "effect": "NoSchedule"}],
                        "resourceClaims": [{"name": "gpu", "resourceClaimTemplateName": "asr-eval-gpu"}],
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                        "containers": [{
                            "name": "offline-evaluator",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/sh", "-ec"],
                            "args": [
                                "python -m medzen_asr_eval network-probe --binding /input/network-binding.json --receipt /output/network-probe.json && "
                                "python -c 'import pathlib,socket,time; s=socket.socket(); s.bind((\"0.0.0.0\",8080)); s.listen(1); "
                                "pathlib.Path(\"/output/inbound-listener-ready\").write_text(\"READY\\n\"); "
                                "[(time.sleep(1)) for _ in iter(int,1) if not pathlib.Path(\"/input/network-release\").exists()]; s.close()' && "
                                "python -m medzen_asr_eval pilot --rows /input/runtime-rows.json --model-root /input/models --model-binding /input/model-bindings.json "
                                "--conditioning /opt/medzen/assets/language-conditioning-v1.json --receipt-root /output/rows --aggregate-receipt /output/aggregate.json"
                            ],
                            "resources": {"requests": {"cpu": "2", "memory": "14Gi"}, "limits": {"cpu": "4", "memory": "20Gi"}, "claims": [{"name": "gpu"}]},
                            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                            "volumeMounts": [
                                {"name": "input", "mountPath": "/input", "readOnly": True},
                                {"name": "output", "mountPath": "/output"},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }],
                        "volumes": [
                            {"name": "input", "hostPath": {"path": f"/var/lib/medzen-asr-eval/attempt-{attempt}/input", "type": "Directory"}},
                            {"name": "output", "hostPath": {"path": f"/var/lib/medzen-asr-eval/attempt-{attempt}/output", "type": "Directory"}},
                            {"name": "tmp", "emptyDir": {"sizeLimit": "2Gi"}},
                        ],
                    },
                },
            },
        },
    ]
    rendered = yaml.safe_dump_all(documents, sort_keys=False)
    verify(rendered, digest, attempt)
    return rendered


def verify(rendered: str, digest: str, attempt: int) -> dict[str, Any]:
    documents = [value for value in yaml.safe_load_all(rendered) if value]
    kinds = [value.get("kind") for value in documents]
    if kinds != ["Namespace", "ResourceClaimTemplate", "NetworkPolicy", "NetworkPolicy", "Job"]:
        raise ValueError("workload kind set or ordering differs")
    if any(kind in {"Service", "Ingress"} for kind in kinds):
        raise ValueError("traffic-facing Kubernetes object is prohibited")
    job = documents[-1]
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    if pod.get("automountServiceAccountToken") is not False or pod.get("hostNetwork") is not False:
        raise ValueError("pod identity or host network boundary differs")
    if not container["image"].endswith("@" + digest) or container.get("ports"):
        raise ValueError("image pin or listening-port boundary differs")
    command = " ".join(container["args"])
    if command.index("network-probe") > command.index(" pilot "):
        raise ValueError("network probe does not precede pilot import path")
    if "inbound-listener-ready" not in command or "network-release" not in command or command.index("network-probe") > command.index("network-release") or command.index("network-release") > command.index(" pilot "):
        raise ValueError("cross-pod isolation release gate differs")
    if job["metadata"]["name"] != f"asr-base-model-pilot-a{attempt}":
        raise ValueError("attempt job identity differs")
    return {"status": "PASS_K8S_RENDER", "kinds": kinds, "service_count": 0, "ingress_count": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--endpoint-ip", action="append", default=[])
    parser.add_argument("--s3-cidr", action="append", default=[])
    parser.add_argument("--attempt", type=int, required=True)
    args = parser.parse_args()
    try:
        print(render(json.loads(args.bindings.read_bytes()), args.endpoint_ip, args.s3_cidr, args.attempt), end="")
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
