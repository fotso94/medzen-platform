from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6A-ECR-SCAN-RULES-2026-005.json"
AUTHORIZATION = (
    ROOT / "platform/decisions/B6A-AWS-AUTH-2026-005-ecr-scan-rules.json"
)
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-005-ecr-scan-rules.md"
)


def _load(path: Path):
    return json.loads(path.read_text())


def test_completion_is_bound_to_authorization_and_packet():
    evidence = _load(EVIDENCE)
    assert evidence["status"] == "VERIFIED_COMPLETE"
    assert evidence["authorization"]["sha256"] == hashlib.sha256(
        AUTHORIZATION.read_bytes()
    ).hexdigest()
    assert evidence["change_packet"]["sha256"] == hashlib.sha256(
        PACKET.read_bytes()
    ).hexdigest()


def test_completion_records_exact_live_rules_and_no_residual_change():
    evidence = _load(EVIDENCE)
    assert evidence["reviewed_plan"]["summary"] == {
        "add": 1,
        "change": 0,
        "destroy": 0,
        "replacement": 0,
    }
    live = evidence["post_apply_verification"]["live_registry_configuration"]
    assert live["scan_type"] == "BASIC"
    assert live["rules"] == [{
        "scan_frequency": "SCAN_ON_PUSH",
        "repository_filters": [
            "medzen-asr-runtime",
            "medzen-model-loader",
            "medzen-nvidia-dra",
        ],
        "filter_type": "WILDCARD",
    }]
    residual = evidence["post_apply_verification"]["residual_plan"]
    assert residual["result"] == "NO_CHANGES"
    assert residual["summary"] == {"add": 0, "change": 0, "destroy": 0}


def test_packet_005_did_not_publish_or_authorize_003b():
    evidence = _load(EVIDENCE)
    boundary = evidence["authorization_boundary"]
    assert boundary["packet_2026_005_complete"] is True
    assert boundary["packet_2026_003b_authorized"] is False
    assert boundary["b6a_deployed"] is False
    assert all(value == 0 for value in evidence["explicit_non_events"].values())
    assert evidence["post_apply_verification"]["gpu_nodegroup"]["desired"] == 0
    assert evidence["post_apply_verification"]["packet_003b_role_exists"] is False
    assert evidence["post_apply_verification"]["b6a_artifact_prefix_objects"] == 0


def test_existing_model_loader_manifests_are_prior_failure_evidence():
    evidence = _load(EVIDENCE)
    provenance = evidence["post_apply_verification"][
        "model_loader_existing_image_provenance"
    ]
    source = ROOT / provenance["source_evidence"]["path"]
    assert provenance["all_three_manifests_predate_this_authorization"] is True
    assert provenance["source_evidence"]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert evidence["post_apply_verification"]["ecr_image_manifest_counts"] == {
        "medzen-model-loader": 3,
        "medzen-asr-runtime": 0,
        "medzen-nvidia-dra": 0,
    }
