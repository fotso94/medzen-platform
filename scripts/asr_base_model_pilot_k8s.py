#!/usr/bin/env python3
"""Render and verify the no-service, strict-egress pilot Kubernetes workload."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Any

import yaml

from scripts.asr_base_model_pilot_workload import (
    JOB_ACTIVE_DEADLINE_SECONDS,
    JOB_TERMINATION_GRACE_SECONDS,
    PILOT_ENVIRONMENT,
    PILOT_WORKLOAD_COMMAND,
    PILOT_WORKLOAD_SCRIPT,
    audit_pilot_workload,
    bound_attempt_window,
)
from scripts.asr_base_model_pilot_dns import (
    VPC_DNS_RESOLVER,
    pod_dns_fields,
    validate_pod_dns_fields,
)


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
    if attempt not in set(range(1, 44)):
        raise ValueError("attempt must be 1 through 43")
    attempt_window = bound_attempt_window(bindings)
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
            "to": [{"ipBlock": {"cidr": f"{VPC_DNS_RESOLVER}/32"}}],
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
                "activeDeadlineSeconds": attempt_window["job_active_deadline_seconds"],
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "metadata": {"labels": LABELS},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "hostNetwork": False,
                        **pod_dns_fields(),
                        "restartPolicy": "Never",
                        "terminationGracePeriodSeconds": JOB_TERMINATION_GRACE_SECONDS,
                        "nodeSelector": {"workload": "gpu"},
                        "tolerations": [{"key": "nvidia.com/gpu", "operator": "Equal", "value": "true", "effect": "NoSchedule"}],
                        "resourceClaims": [{"name": "gpu", "resourceClaimTemplateName": "asr-eval-gpu"}],
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                        "containers": [{
                            "name": "offline-evaluator",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": list(PILOT_WORKLOAD_COMMAND),
                            "args": [PILOT_WORKLOAD_SCRIPT],
                            "env": [
                                *PILOT_ENVIRONMENT,
                                {
                                    "name": "MEDZEN_EXPECTED_ROWS",
                                    "value": str(
                                        bindings.get("input_freeze", {}).get("pilot_rows", 540)
                                    ),
                                },
                            ],
                            "resources": {"requests": {"cpu": "2", "memory": "14Gi"}, "limits": {"cpu": "4", "memory": "20Gi"}, "claims": [{"name": "gpu"}]},
                            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                            "volumeMounts": [
                                {"name": "input", "mountPath": "/input", "readOnly": True},
                                # The image's fairseq2 asset cards bind model
                                # checkpoints at absolute /models paths; the
                                # staged weights must appear there or Meta
                                # backend loads fail (attempt-25 refusal).
                                {"name": "input", "mountPath": "/models", "subPath": "models", "readOnly": True},
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
    verify(
        rendered,
        digest,
        attempt,
        expected_job_active_deadline_seconds=attempt_window["job_active_deadline_seconds"],
    )
    return rendered


ASSET_CARD_PATH = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "asr-eval-runtime"
    / "assets"
    / "models.yaml"
)
ASSET_CARD_FILE_FIELDS = ("checkpoint", "tokenizer")


def asset_card_file_paths() -> list[str]:
    """Absolute file paths the image's baked fairseq2 asset cards resolve."""
    paths: list[str] = []
    for document in yaml.safe_load_all(ASSET_CARD_PATH.read_text()):
        if not isinstance(document, dict):
            continue
        for field in ASSET_CARD_FILE_FIELDS:
            value = document.get(field)
            if isinstance(value, str) and value.startswith("/"):
                paths.append(value)
    if not paths:
        raise ValueError("asset cards declare no absolute file paths")
    return sorted(set(paths))


def validate_asset_card_mount_coverage(pod: dict[str, Any]) -> dict[str, Any]:
    """Refuse unless every asset-card file path sits under a pod mount."""
    mounts = [
        item["mountPath"]
        for item in pod["containers"][0].get("volumeMounts", [])
        if isinstance(item, dict) and isinstance(item.get("mountPath"), str)
    ]
    uncovered = [
        path
        for path in asset_card_file_paths()
        if not any(path == mount or path.startswith(mount.rstrip("/") + "/") for mount in mounts)
    ]
    if uncovered:
        raise ValueError(
            f"asset-card file paths are not covered by pod mounts: {uncovered}"
        )
    return {
        "status": "PASS_ASSET_CARD_MOUNT_COVERAGE",
        "card_paths": asset_card_file_paths(),
        "mounts": sorted(mounts),
    }


def verify(
    rendered: str,
    digest: str,
    attempt: int,
    *,
    expected_job_active_deadline_seconds: int = JOB_ACTIVE_DEADLINE_SECONDS,
) -> dict[str, Any]:
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
    try:
        dns = validate_pod_dns_fields(pod)
    except Exception as exc:
        raise ValueError("pod DNS boundary differs") from exc
    if not container["image"].endswith("@" + digest) or container.get("ports"):
        raise ValueError("image pin or listening-port boundary differs")
    command = " ".join(container["args"])
    if command.index("network-probe") > command.index("pilot-phase-journal.jsonl"):
        raise ValueError("network probe does not precede pilot import path")
    if "inbound-listener-ready" not in command or "network-release" not in command or command.index("network-probe") > command.index("network-release") or command.index("network-release") > command.index("pilot-phase-journal.jsonl"):
        raise ValueError("cross-pod isolation release gate differs")
    if job["metadata"]["name"] != f"asr-base-model-pilot-a{attempt}":
        raise ValueError("attempt job identity differs")
    workload_audit = audit_pilot_workload()
    if job["spec"].get("activeDeadlineSeconds") != expected_job_active_deadline_seconds:
        raise ValueError("pilot job active deadline differs")
    if pod.get("terminationGracePeriodSeconds") != JOB_TERMINATION_GRACE_SECONDS:
        raise ValueError("pilot termination grace differs")
    observed_env = container.get("env") or []
    if (
        observed_env[:-1] != list(PILOT_ENVIRONMENT)
        or not observed_env
        or observed_env[-1].get("name") != "MEDZEN_EXPECTED_ROWS"
        or not str(observed_env[-1].get("value", "")).isdigit()
    ):
        raise ValueError("pilot explicit environment differs")
    card_coverage = validate_asset_card_mount_coverage(pod)
    workload_argv = [*container["command"], *container["args"]]
    return {
        "status": "PASS_K8S_RENDER",
        "asset_card_mount_coverage": card_coverage,
        "kinds": kinds,
        "service_count": 0,
        "ingress_count": 0,
        "pilot_workload_historical_live_pass": False,
        "pilot_workload_argv_sha256": hashlib.sha256(
            b"\0".join(value.encode() for value in workload_argv)
        ).hexdigest(),
        "pilot_workload_audit": workload_audit,
        "pod_dns": dns,
    }


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
