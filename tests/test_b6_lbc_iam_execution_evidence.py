from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packet_011_authorization_and_execution_evidence_are_exact() -> None:
    auth_path = ROOT / "platform/decisions/B6-AWS-AUTH-2026-011-lbc-iam-lifecycle-correction.json"
    evidence_path = ROOT / "platform/evidence/B6-LBC-IAM-LIFECYCLE-AWS-EXECUTION-2026-001.json"
    auth = json.loads(auth_path.read_text())
    evidence = json.loads(evidence_path.read_text())

    assert auth["status"] == "owner-approved"
    assert auth["packet"]["sha256"] == "9df81fb37f8bfd47dc0b7daa426ec19020e72ed6623853dab783ea38aaabac73"
    assert evidence["status"] == "VERIFIED_COMPLETE"
    assert evidence["outcome"] == "PASS_IAM_ONLY"
    assert evidence["authorization"]["sha256"] == sha(auth_path)
    assert evidence["terraform"]["fresh_plan"] == {
        "path_not_retained": "/private/tmp/b6-lbc-iam-2026-011-approved.tfplan",
        "sha256": "d5bb91210fb4ecb750263afbca3e3de16fc7be97415a0aeb68c2e19ad424be07",
        "adds": 0,
        "updates": 1,
        "destroys": 0,
        "only_address": "aws_iam_role_policy.b6_load_balancer_controller",
        "guard": "PASS_B6_LBC_IAM_CORRECTION changes=1 add=0 update=1 destroy=0",
    }
    assert evidence["live_policy_after"]["canonical_sha256"] == auth["required_postchange_canonical_policy_sha256"]
    simulation = evidence["live_postapply_simulation"]
    assert simulation["required_positive_allowed"] == 21
    assert simulation["negative_boundary_implicit_denies"] == 19
    assert simulation["listener_rule_tag_mutation_observation_implicit_denies"] == 4
    assert simulation["mismatches"] == 0
    assert evidence["zero_state_after"]["verified"] is True
    assert all(value == 0 for value in evidence["explicit_non_events"].values())
