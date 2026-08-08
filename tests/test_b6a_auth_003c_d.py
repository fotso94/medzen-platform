from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import b6a_003c_d_common as common


AUTH = (
    ROOT
    / "platform/decisions/B6A-AWS-AUTH-2026-003C-D-stage-receipts-and-ssm-sampler.json"
)
PACKET = (
    ROOT
    / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-D-stage-receipts-and-ssm-sampler.md"
)
REVIEW = ROOT / "platform/decisions/B6A-IAM-REVIEW-2026-003C-D.json"


def record():
    return json.loads(AUTH.read_bytes())


def test_003c_d_authorization_binds_review_owner_packet_and_allowance():
    value = record()
    assert value["status"] == "owner-approved"
    assert value["independent_iam_review"]["status"] == "PASS"
    assert value["independent_iam_review"]["sha256"] == hashlib.sha256(
        REVIEW.read_bytes()
    ).hexdigest()
    assert value["packet"] == {
        "id": "B6A-AWS-CHANGE-PACKET-2026-003C-D",
        "sha256": hashlib.sha256(PACKET.read_bytes()).hexdigest(),
    }
    assert value["aws_scope"]["maximum_window_seconds"] == 6520


def test_003c_d_authorization_binds_every_executable_source():
    for relative, expected in record()["source_bindings"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_003c_d_runtime_validator_accepts_only_exact_authorization():
    value = common.authorization(AUTH, hashlib.sha256(PACKET.read_bytes()).hexdigest(), ROOT)
    assert value["id"] == common.AUTH_ID
    assert value["bound_resources"]["workload_render_sha256"] == common.WORKLOAD_SHA256


def test_003c_d_post_run_audit_condition_is_binding():
    assertion = record()["post_run_audit_condition"]["assertion"]
    assert "transcription receipt recorded_utc" in assertion
    assert "earlier than" in assertion
