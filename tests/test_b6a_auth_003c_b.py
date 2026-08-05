from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "platform/decisions/B6A-AWS-AUTH-2026-003C-B-deployment.json"


def _auth():
    return json.loads(AUTH.read_text())


def test_auth_003c_b_binds_exact_packet_and_prerequisites():
    auth = _auth()
    assert auth["id"] == "B6A-AWS-AUTH-2026-003C-B"
    assert auth["status"] == "owner-approved"
    for binding in (auth["packet"], *auth["prerequisites"].values()):
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == (
            binding["sha256"]
        )


def test_auth_003c_b_binds_exact_renders_and_synthetic_audio():
    resources = _auth()["bound_resources"]
    for path_key, sha_key in (
        ("workload_render_path", "workload_render_sha256"),
        ("nvidia_dra_render_path", "nvidia_dra_render_sha256"),
        ("synthetic_audio_path", "synthetic_audio_sha256"),
    ):
        assert hashlib.sha256((ROOT / resources[path_key]).read_bytes()).hexdigest() == (
            resources[sha_key]
        )
    assert resources["synthetic_audio_bytes"] == 155962


def test_auth_003c_b_keeps_nonpromotion_and_gpu_limits():
    auth = _auth()
    assert auth["aws_scope"]["maximum_gpu_nodes"] == 1
    assert auth["aws_scope"]["maximum_window_seconds"] == 7200
    prohibited = " ".join(auth["prohibited_operations"])
    assert "approved_asr_write" in prohibited
    assert "model_registration" in prohibited
    assert "production_ssm_change" in prohibited
    assert "second_gpu_node" in prohibited
    assert auth["permitted_success_label"] == "B6A_PLATFORM_PROOF_COMPLETE"
