from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/B6A-AWS-AUTH-2026-003C-A-scan-only.json"


def _auth():
    return json.loads(AUTH.read_text())


def test_authorization_binds_exact_committed_packet_and_evidence():
    auth = _auth()
    assert auth["status"] == "owner-approved"
    for key in ("packet", "local_engineering"):
        binding = auth[key]
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == binding[
            "sha256"
        ]


def test_authorization_is_only_for_two_bound_image_scans():
    auth = _auth()
    assert set(auth["bound_images"]) == {"asr_runtime", "nvidia_dra", "model_loader"}
    assert auth["bound_images"]["model_loader"]["action"] == "read_only_reuse_no_push"
    assert "tag_and_push_only_the_bound_asr_runtime_identity" in auth[
        "authorized_operations"
    ]
    assert "tag_and_push_only_the_bound_nvidia_dra_identity" in auth[
        "authorized_operations"
    ]


def test_authorization_forbids_deployment_gpu_manual_scan_and_waiver():
    prohibited = set(_auth()["prohibited_operations"])
    for required in (
        "manual_ecr_start_image_scan",
        "image_rebuild_substitution_or_security_waiver",
        "nvidia_dra_installation_or_workload_deployment",
        "gpu_scale_up_or_gpu_window",
        "production_ssm_change",
    ):
        assert required in prohibited


def test_only_scan_pass_allows_preparing_a_new_packet():
    auth = _auth()
    assert auth["deterministic_outcomes"] == [
        "PASS_SCAN_ONLY",
        "BLOCKED_IMAGE_SCAN",
        "FAILED_CLOSED_EXECUTION",
    ]
    assert auth["next_boundary"].startswith("Only PASS_SCAN_ONLY")
