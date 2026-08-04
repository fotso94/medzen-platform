from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6A-EKS-STANDARD-SUPPORT-2026-004.json"
AUTHORIZATION = (
    ROOT / "platform/decisions/B6A-AWS-AUTH-2026-004-eks-standard-support.json"
)
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-004-eks-standard-support.md"
)


def _load(path: Path):
    return json.loads(path.read_text())


def test_completion_evidence_is_bound_to_authorization_and_packet():
    evidence = _load(EVIDENCE)
    authorization = _load(AUTHORIZATION)

    assert evidence["status"] == "VERIFIED_COMPLETE"
    assert evidence["authorization"]["path"] == str(AUTHORIZATION.relative_to(ROOT))
    assert evidence["change_packet"]["path"] == str(PACKET.relative_to(ROOT))
    assert evidence["change_packet"]["sha256"] == hashlib.sha256(
        PACKET.read_bytes()
    ).hexdigest()
    assert authorization["packet"]["sha256"] == evidence["change_packet"]["sha256"]


def test_completion_records_only_the_standard_support_change():
    evidence = _load(EVIDENCE)

    assert evidence["reviewed_plan"]["summary"] == {
        "add": 0,
        "change": 1,
        "destroy": 0,
        "replacement": 0,
    }
    assert evidence["reviewed_plan"]["resource_actions"] == {
        "aws_eks_cluster.this": "update"
    }
    assert evidence["reviewed_plan"]["exact_field_transition"] == {
        "upgrade_policy.support_type": {
            "before": "EXTENDED",
            "after": "STANDARD",
        }
    }
    assert evidence["post_apply_verification"]["residual_plan"]["result"] == (
        "NO_CHANGES"
    )
    assert evidence["post_apply_verification"]["eks"]["support_type"] == "STANDARD"


def test_packet_003a_remains_separately_gated():
    evidence = _load(EVIDENCE)

    assert evidence["authorization_boundary"]["packet_2026_003a_authorized"] is False
    assert all(value == 0 for value in evidence["explicit_non_events"].values())
    assert evidence["post_apply_verification"]["gpu_nodegroup"]["desired"] == 0
    assert evidence["post_apply_verification"]["registry_parameter_count"] == 0

    later_authorizations = list(
        (ROOT / "platform/decisions").glob("B6A-AWS-AUTH-2026-003A*")
    )
    if later_authorizations:
        assert len(later_authorizations) == 1
        later = _load(later_authorizations[0])
        assert later["id"] == "B6A-AWS-AUTH-2026-003A"
        assert later["authorized_utc"] > evidence["completed_utc"]
        assert "only to packet 2026-003A" in later["interpretation"]
