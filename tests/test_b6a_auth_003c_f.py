from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import b6a_003c_f_common as common


AUTH = ROOT / "platform/decisions/B6A-AWS-AUTH-2026-003C-F-ssm-discovery-full-proof.json"
PACKET = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-003C-F-ssm-discovery-full-proof.md"
REVIEW = ROOT / "platform/decisions/B6A-INDEPENDENT-REVIEW-2026-003C-F.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record() -> dict:
    return json.loads(AUTH.read_bytes())


def test_003c_f_authorization_binds_review_owner_packet_and_allowance():
    value = record()
    assert value["status"] == "owner-approved"
    assert value["independent_review"]["status"] == "PASS"
    assert value["independent_review"]["sha256"] == sha(REVIEW)
    assert value["packet"] == {
        "id": "B6A-AWS-CHANGE-PACKET-2026-003C-F",
        "sha256": sha(PACKET),
    }
    assert value["aws_scope"]["maximum_window_seconds"] == 4610
    assert value["aws_scope"]["iam_changes"] == 0
    assert value["aws_scope"]["terraform_resource_changes"] == 0


def test_003c_f_authorization_binds_every_executable_source():
    for relative, expected in record()["source_bindings"].items():
        assert sha(ROOT / relative) == expected


def test_003c_f_runtime_validator_accepts_only_exact_authorization():
    value = common.authorization(AUTH, sha(PACKET), ROOT)
    assert value["id"] == common.AUTH_ID
    assert value["bound_resources"]["workload_render_sha256"] == common.WORKLOAD_SHA256


def test_003c_f_closure_and_next_step_conditions_are_binding():
    conditions = record()["post_run_audit_conditions"]
    assert conditions["transcription_receipt_recorded_before_memory_sampler_start"] is True
    assert conditions["numeric_peak_l4_gpu_memory_required_for_completion"] is True
    assert conditions["cleanup_zero_required"] is True
    assert conditions["all_pass_effect"] == "publish B6A closure record and stop"
    assert conditions["next_step_after_closure"] == "full-B6 planning review"
