from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6-PACKET-2026-033-SCAN-RESULT.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_result_binds_reviewed_packet_exact_child_and_local_subject():
    value = json.loads(RESULT.read_bytes())
    packet = ROOT / value["packet"]["path"]
    assert _sha(packet) == value["packet"]["sha256"]
    assert value["packet"]["review_status"] == "PASS"
    assert value["packet"]["owner_approval"] == (
        "Approve B6 AWS change packet 2026-033 only."
    )
    subject = value["subject"]
    assert subject["child_manifest_digest"] == (
        "sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3"
    )
    assert subject["packaged_partial_source_sha256"] == (
        "f5e6c57c3d8a57d80980ee3741723b36ae810e03aea10d2057fa2c30776a90fc"
    )
    assert value["preconditions"]["destination_tag_absent_before_push"] is True
    assert value["preconditions"]["fresh_local_qualification_byte_identical"] is True


def test_result_preserves_both_dependency_outcomes_and_scan_pass():
    value = json.loads(RESULT.read_bytes())
    pre = value["preconditions"]
    assert pre["dependency_unavailable_http_status"] == 503
    assert pre["dependency_unavailable_reason"] == (
        "STREAMING_PARTIAL_SOURCE_UNAVAILABLE"
    )
    assert pre["real_rfc6455_handshake_http_status"] == 101
    assert pre["stable_full_conversation_passes"] == 3
    scan = value["authoritative_scan"]
    assert value["outcome"] == "PASS_SCAN_ONLY"
    assert scan["queried_by"] == "linux/amd64 child manifest digest"
    assert scan["status"] == "COMPLETE"
    assert scan["finding_count"] == 0
    assert all(
        scan[severity] == 0
        for severity in (
            "critical",
            "high",
            "medium",
            "low",
            "informational",
            "undefined",
        )
    )
    assert scan["security_waiver_used"] is False


def test_scan_only_boundary_left_compute_runtime_and_production_at_zero():
    value = json.loads(RESULT.read_bytes())
    post = value["postconditions"]
    assert post["ecr_images_pushed"] == 1
    assert post["ecr_repository_configuration_changes"] == 0
    assert value["deployment_authorized_by_this_record"] is False
    assert all(
        post[field] == 0
        for field in (
            "cpu_desired",
            "gpu_desired",
            "workload_nodes",
            "medzen_deployments",
            "medzen_pods",
            "production_serving_pointer_count",
            "approved_asr_objects",
            "kubernetes_mutations",
            "iam_mutations",
            "ssm_mutations",
            "secret_mutations",
            "gpu_seconds",
        )
    )
    assert post["deployment_attempted"] is False
    assert post["worker_scale_up"] is False
    assert value["verification"]["canonical_local_suite"] == {
        "passed": 1531,
        "failed": 0,
        "skipped": 0,
        "deselected": 7,
        "warnings": 1,
    }
    assert "fresh owner allowance decision is required" in value["next_boundary"]
