from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = (
    ROOT / "platform/decisions/B6A-AWS-AUTH-2026-005-ecr-scan-rules.json"
)
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-005-ecr-scan-rules.md"
)


def _authorization():
    return json.loads(AUTHORIZATION.read_text())


def test_authorization_is_bound_to_the_immutable_packet():
    authorization = _authorization()
    assert authorization["status"] == "owner-approved"
    assert authorization["packet"]["path"] == str(PACKET.relative_to(ROOT))
    assert authorization["packet"]["sha256"] == hashlib.sha256(
        PACKET.read_bytes()
    ).hexdigest()


def test_authorization_is_exactly_one_resource_and_three_repositories():
    authorization = _authorization()
    preconditions = authorization["hard_apply_preconditions"]
    assert preconditions["allowed_resource_actions"] == {
        "aws_ecr_registry_scanning_configuration.b6a_runtime": ["create"]
    }
    assert preconditions["exact_repository_filters"] == [
        "medzen-asr-runtime",
        "medzen-model-loader",
        "medzen-nvidia-dra",
    ]
    assert preconditions["deletes"] == 0
    assert preconditions["output_changes"] == 0


def test_authorization_does_not_extend_to_003b_or_publication():
    authorization = _authorization()
    assert "only to packet 2026-005" in authorization["interpretation"]
    prohibited = authorization["prohibited_operations"]
    assert "packet_2026_003B_identity_or_deployment_operation" in prohibited
    assert "image_push_tag_delete_or_scan_invocation" in prohibited
    assert "s3_iam_kms_ssm_eks_kubernetes_or_gpu_change" in prohibited
