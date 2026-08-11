from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6-PACKET-2026-031-SCAN-RESULT.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_scan_result_binds_reviewed_packet_and_exact_child():
    value = json.loads(RESULT.read_bytes())
    packet = ROOT / value["packet"]["path"]
    assert _sha(packet) == value["packet"]["sha256"]
    assert value["packet"]["review_status"] == "PASS"
    assert value["packet"]["owner_approval"] == (
        "Approve B6 AWS change packet 2026-031 only."
    )
    assert value["subject"]["child_manifest_digest"] == (
        "sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed"
    )
    assert value["subject"]["platform"] == "linux/amd64"
    assert value["preconditions"]["destination_tag_absent_before_push"] is True


def test_scan_pass_has_zero_findings_and_no_waiver():
    value = json.loads(RESULT.read_bytes())
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


def test_scan_only_boundary_left_runtime_and_production_at_zero():
    value = json.loads(RESULT.read_bytes())
    post = value["postconditions"]
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
