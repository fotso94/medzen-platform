from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6A-PACKET-2026-003A-FAILED-IMAGE-SCAN.json"
AUTH = ROOT / "platform/decisions/B6A-AWS-AUTH-2026-003A-deployment.json"
PACKET = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003A-deployment.md"


def _load(path: Path):
    return json.loads(path.read_text())


def test_failure_evidence_is_bound_to_exact_authorization_and_packet():
    evidence = _load(EVIDENCE)
    assert evidence["status"] == "FAILED_CLOSED"
    assert evidence["authorization"]["sha256"] == hashlib.sha256(
        AUTH.read_bytes()
    ).hexdigest()
    assert evidence["packet"]["sha256"] == hashlib.sha256(
        PACKET.read_bytes()
    ).hexdigest()


def test_scan_gate_failed_and_success_was_not_claimed():
    evidence = _load(EVIDENCE)
    scan = evidence["scan_behavior"]["linux_amd64_child_scan"]
    assert scan["status"] == "COMPLETE"
    assert scan["critical"] == 1
    assert scan["high"] == 3
    assert scan["hard_gate"] == "FAIL"
    assert evidence["stop_decision"]["result"] == (
        "B6A_PLATFORM_PROOF_BLOCKED_IMAGE_SCAN"
    )
    assert evidence["stop_decision"]["success_label_used"] is False
    assert evidence["stop_decision"]["security_waiver_used"] is False


def test_hard_stop_prevented_every_downstream_change():
    evidence = _load(EVIDENCE)
    post = evidence["post_stop_verification"]
    assert post["terraform_residual_plan"]["result"] == "NO_CHANGES"
    assert post["gpu_nodegroup"]["desired"] == 0
    for field in (
        "asr_runtime_images_pushed",
        "nvidia_dra_images_pushed",
        "artifact_objects_uploaded",
        "b6a_iam_roles_created",
        "b6a_pod_identity_associations_created",
        "b6a_namespaces_created",
        "b6a_kubernetes_resources_created",
        "production_registry_parameters",
        "approved_asr_writes",
        "registered_models_or_versions",
        "language_artifact_or_approved_version_changes",
    ):
        assert post[field] == 0


def test_failed_packet_cannot_resume_without_a_new_owner_approved_revision():
    evidence = _load(EVIDENCE)
    boundary = evidence["remediation_boundary"]
    assert boundary["current_packet_may_resume"] is False
    assert boundary["new_packet_required"] == "B6A-AWS-CHANGE-PACKET-2026-003B"
    assert boundary["new_owner_approval_required"] is True

    active_terraform = (ROOT / "infra/b6a.tf").read_text()
    assert 'resource "aws_ecr_repository" "b6a_nvidia_dra"' in active_terraform
    assert 'resource "aws_iam_role" "b6a_asr"' not in active_terraform
    assert 'resource "aws_eks_pod_identity_association" "b6a_asr"' not in active_terraform
