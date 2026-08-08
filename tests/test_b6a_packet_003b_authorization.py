from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/B6A-AWS-AUTH-2026-003B-deployment.json"
PACKET = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003B-deployment.md"


def _load(path: Path):
    return json.loads(path.read_text())


def test_authorization_is_bound_to_exact_003b_packet():
    authorization = _load(AUTH)
    assert authorization["status"] == "owner-approved"
    assert authorization["packet"]["path"] == str(PACKET.relative_to(ROOT))
    assert authorization["packet"]["sha256"] == hashlib.sha256(
        PACKET.read_bytes()
    ).hexdigest()
    assert "only to packet 2026-003B" in authorization["interpretation"]


def test_authorization_binds_clean_exact_images_and_failed_quality_artifact():
    authorization = _load(AUTH)
    for image in authorization["bound_images"].values():
        assert image["local_scout_critical"] == 0
        assert image["local_scout_high"] == 0
    artifact = authorization["bound_artifact"]
    assert artifact["tree_sha256"] in artifact["s3_prefix"]
    assert artifact["classification"] == "PLATFORM_PROOF_ONLY"
    assert artifact["quality_gate_outcome"] == "FAIL"
    assert artifact["production_approved"] is False


def test_budget_stop_and_cleanup_boundaries_are_not_relaxed():
    authorization = _load(AUTH)
    budget = authorization["prerequisites"]["budget"]
    assert budget["aggregate_ceiling_usd"] == 300.0
    assert budget["existing_packet_reservation_usd"] == 15.0
    assert budget["new_reservation_created"] is False
    assert budget["maximum_gpu_hours"] == 2
    assert authorization["mandatory_cleanup"] == {
        "deployment_replicas_after_test": 0,
        "gpu_desired_after_test": 0,
        "gpu_nodes_after_test": 0,
        "b6a_pods_after_test": 0,
    }
    assert "security_waiver_or_image_substitution" in authorization[
        "prohibited_operations"
    ]


def test_packet_005_is_a_closed_prerequisite_not_combined_authority():
    authorization = _load(AUTH)
    scan_evidence = authorization["prerequisites"]["ecr_scan_rules"]
    source = ROOT / scan_evidence["path"]
    assert scan_evidence["status"] == "VERIFIED_COMPLETE"
    assert scan_evidence["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
