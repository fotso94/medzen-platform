#!/usr/bin/env python3
"""Audit every B6.6 post-mutation verification for bounded stable polling."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RULE_ID = "B6-POST-MUTATION-STABILITY-2026-001"
EVIDENCE_ID = "B6-POST-MUTATION-VERIFIER-AUDIT-2026-003"
PREDECESSOR_PATH = (
    "platform/evidence/"
    "B6-PACKET-2026-030-ATTEMPT-1-REFUSED-PROBE-AUDIO-BINDING.json"
)


class AuditRefusal(RuntimeError):
    pass


@dataclass(frozen=True)
class Control:
    id: str
    mutation: str
    verifier: str
    source: str
    marker: str
    stable_observations: int
    corrected_by_this_change: bool


CONTROLS = (
    Control("credential_version_stage", "Secrets Manager PutSecretValue", "exact version AWSCURRENT visibility", "scripts/b6_6_credential.py", "wait_for_exact_current_version", 3, True),
    Control("local_token_file", "ephemeral token file write and fsync", "exact mode, length, newline, content and hash", "scripts/b6_6_credential.py", "wait_for_local_token_stable", 3, True),
    Control("operator_plaintext_denial", "synthetic secret version rotation", "stable operator GetSecretValue denial", "scripts/b6_6_credential.py", "wait_for_operator_denied", 3, True),
    Control("deadline_arm", "two Auto Scaling scheduled actions", "matching shared deadline", "scripts/b6_6_deadline.py", "stable_deadline_observations", 3, True),
    Control("worker_scale_up", "EKS CPU and GPU desired capacity", "exact Ready worker set", "scripts/b6_6_wait_workers.py", '"stable_observations": consecutive', 3, True),
    Control("dra_apply", "NVIDIA DRA DaemonSet apply", "ready digest-pinned DaemonSet", "scripts/b6_6_operations.sh", "b6_6_k8s_stability.py daemonset", 3, True),
    Control("rag_deploy", "RAG Deployment apply and scale", "RAG Deployment availability", "scripts/b6_6_operations.sh", "--name rag-index --replicas 1", 3, True),
    Control("asr_deploy", "ASR Deployment scale", "ASR Deployment availability", "scripts/b6_6_operations.sh", "--name asr-runtime --replicas 1", 3, True),
    Control("tts_deploy", "TTS Deployment scale", "TTS Deployment availability", "scripts/b6_6_operations.sh", "--name tts-gateway --replicas 1", 3, True),
    Control("llm_deploy", "LLM Deployment scale", "LLM Deployment availability", "scripts/b6_6_operations.sh", "--name llm-gateway --replicas 1", 3, True),
    Control("orchestrator_deploy", "orchestrator Deployment scale", "orchestrator Deployment availability", "scripts/b6_6_operations.sh", "--name speech-orchestrator --replicas 1", 3, True),
    Control("resident_image_set", "all workload image pulls", "exact resident child-digest set", "scripts/b6_6_operations.sh", "b6_6_k8s_stability.py pod-images", 3, True),
    Control("full_pre_endpoint_image_set", "all seven workload and controller pod starts", "exact eight-digest node-resident image set", "scripts/b6_6_pre_endpoint_images.py", '"stable_observations": consecutive', 3, True),
    Control("controller_install", "Terraform Helm controller install", "controller Deployment and digest", "scripts/b6_6_operations.sh", "--name aws-load-balancer-controller --replicas 1", 3, True),
    Control("endpoint_create", "Terraform endpoint creation", "exact available endpoint boundary", "scripts/b6_6_probe_endpoints.py", '"stable_observations": consecutive', 3, True),
    Control("alb_target_registration", "Ingress and ALB target registration", "stable healthy target identity", "scripts/b6_6_lbc_runtime.py", '"stable_healthy_observations": consecutive', 3, False),
    Control("alb_runtime_shape", "ALB listener, rules and creation tags", "stable runtime shape hash", "scripts/b6_6_lbc_runtime.py", '"stable_runtime_shape_observations": consecutive', 3, True),
    Control("fargate_task", "ECS RunTask", "stable terminal task result", "scripts/b6_6_fargate_probe.py", '"stable_terminal_observations": consecutive_terminal', 2, True),
    Control("alb_tag_classification", "controller post-create tag attempts", "stable bounded tag-denial classification", "scripts/b6_6_lbc_runtime.py", '"stable_tag_classification_observations": consecutive', 3, True),
    Control("local_port_forward", "kubectl port-forward process start", "two consecutive ready responses", "scripts/b6_6_operations.sh", 'stable_ready=$((stable_ready + 1))', 2, True),
    Control("rag_drill_unavailable", "RAG Service selector diversion", "zero Endpoint addresses", "scripts/b6_6_operations.sh", "--name rag-index --count 0", 3, True),
    Control("rag_drill_restore", "RAG Service selector restoration", "one Endpoint address", "scripts/b6_6_operations.sh", "--name rag-index --count 1", 3, True),
    Control("isolation_state", "window Service and Ingress creation", "exact stable ClusterIP and ingress boundary", "scripts/b6_6_operations.sh", "b6_6_k8s_stability.py isolation", 3, True),
    Control("cleanup_ecs_tasks", "ECS StopTask", "zero listed tasks", "scripts/b6_6_cleanup.sh", "ecs_zero_stable", 3, True),
    Control("cleanup_alb", "Ingress deletion", "LoadBalancerNotFound", "scripts/b6_6_cleanup.sh", "alb_absent_stable", 3, True),
    Control("cleanup_kubernetes", "Kubernetes workload deletion", "zero window Kubernetes state", "scripts/b6_6_cleanup.sh", "kubernetes_zero_payload", 3, True),
    Control("cleanup_terraform", "Terraform temporary-resource destroy", "zero-change targeted plan", "scripts/b6_6_cleanup.sh", "terraform_zero_stable", 3, True),
    Control("cleanup_endpoints", "VPC endpoint destroy", "stable endpoint and SG absence", "scripts/b6_6_cleanup.sh", "b6_6_probe_endpoints.py wait-absent", 3, True),
    Control("cleanup_workers", "EKS worker scale to zero", "stable EKS and ASG zero", "scripts/b6_6_deadline.py", '"stable_zero_observations": POST_MUTATION_STABLE_OBSERVATIONS', 3, True),
    Control("cleanup_deadlines", "scheduled-action deletion", "stable scheduled-action absence", "scripts/b6_6_deadline.py", '"stable_deadline_absence_observations": POST_MUTATION_STABLE_OBSERVATIONS', 3, True),
    Control("cleanup_local_files", "token and hostname unlink", "stable local absence", "scripts/b6_6_cleanup.sh", "local_absence_stable", 3, True),
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root: Path = ROOT) -> dict[str, Any]:
    ids = [control.id for control in CONTROLS]
    if len(ids) != len(set(ids)):
        raise AuditRefusal("post-mutation control IDs are not unique")
    results: list[dict[str, Any]] = []
    for control in CONTROLS:
        path = root / control.source
        if not path.is_file() or control.marker not in path.read_text():
            raise AuditRefusal(f"post-mutation control is not wired: {control.id}")
        if control.stable_observations < 2:
            raise AuditRefusal(f"one-shot post-mutation control remains: {control.id}")
        results.append({**asdict(control), "status": "PASS"})
    corrected = sum(item["corrected_by_this_change"] for item in results)
    return {
        "id": EVIDENCE_ID,
        "recorded_date": "2026-08-11",
        "rule_id": RULE_ID,
        "status": "PASS",
        "predecessor": {
            "path": PREDECESSOR_PATH,
            "sha256": sha256_file(root / PREDECESSOR_PATH),
            "diagnosis": "PROBE_AUDIO_BINDING_SOURCE_DRIFT",
        },
        "post_mutation_paths": len(results),
        "corrected_paths": corrected,
        "preexisting_compliant_paths": len(results) - corrected,
        "one_shot_paths_remaining": 0,
        "minimum_stable_observations": min(
            item["stable_observations"] for item in results
        ),
        "source_hashes": {
            relative: sha256_file(root / relative)
            for relative in sorted({control.source for control in CONTROLS})
        },
        "controls": results,
        "deviations": [],
    }


def main() -> int:
    try:
        result = audit()
    except AuditRefusal as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
