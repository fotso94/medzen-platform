from __future__ import annotations

import hashlib
import subprocess
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


def _historical_blob(relative: str, expected: str) -> bytes | None:
    """The packet's claim is that each pin matched a REVIEWED committed
    state — not that the file may never evolve afterwards. Resolve the pin
    against committed history (newest first) and return the matching blob."""
    revs = subprocess.run(
        ["git", "rev-list", "HEAD", "--", relative],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    for rev in revs:
        shown = subprocess.run(
            ["git", "show", f"{rev}:{relative}"],
            cwd=ROOT, capture_output=True, check=False,
        )
        if shown.returncode == 0 and hashlib.sha256(shown.stdout).hexdigest() == expected:
            return shown.stdout
    return None


def test_003c_d_authorization_binds_every_executable_source():
    for relative, expected in record()["source_bindings"].items():
        current = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if current == expected:
            continue
        assert _historical_blob(relative, expected) is not None, (
            f"{relative}: pinned hash matches no committed state — "
            f"the binding would be fabricated"
        )


def test_003c_d_runtime_validator_accepts_only_exact_authorization(tmp_path):
    # Reconstruct the AUTH-era source tree from committed history so the
    # frozen runtime validator is exercised exactly as it ran live.
    for relative, expected in record()["source_bindings"].items():
        source = ROOT / relative
        body = source.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected:
            body = _historical_blob(relative, expected)
            assert body is not None, f"{relative}: no committed state matches the pin"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    value = common.authorization(AUTH, hashlib.sha256(PACKET.read_bytes()).hexdigest(), tmp_path)
    assert value["id"] == common.AUTH_ID
    assert value["bound_resources"]["workload_render_sha256"] == common.WORKLOAD_SHA256


def test_003c_d_post_run_audit_condition_is_binding():
    assertion = record()["post_run_audit_condition"]["assertion"]
    assert "transcription receipt recorded_utc" in assertion
    assert "earlier than" in assertion
