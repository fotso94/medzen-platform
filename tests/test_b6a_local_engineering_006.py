from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

def _pin_matches_committed_history(relative: str, expected: str) -> bool:
    """The packet's claim is that the pin matched a REVIEWED committed state,
    not that the file may never evolve (same rule as test_b6a_auth_003c_d)."""
    import hashlib, subprocess
    revs = subprocess.run(["git", "rev-list", "HEAD", "--", relative],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for rev in revs:
        shown = subprocess.run(["git", "show", f"{rev}:{relative}"],
                               cwd=ROOT, capture_output=True, check=False)
        if shown.returncode == 0 and hashlib.sha256(shown.stdout).hexdigest() == expected:
            return True
    return False

EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-006.json"


def record():
    return json.loads(EVIDENCE.read_bytes())


def test_local_record_binds_every_runtime_source_and_design():
    value = record()
    assert value["status"] == "LOCAL_ENGINEERING_COMPLETE_IAM_REVIEW_AND_PACKET_APPROVAL_REQUIRED"
    for relative, expected in value["source_bindings"].items():
        assert (hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
            or _pin_matches_committed_history(relative, expected))
    for key in ("design", "supplemental_design", "least_privilege_refinement"):
        binding = value[key]
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == binding["sha256"]


def test_local_record_proves_no_aws_execution_and_keeps_proofs_separate():
    value = record()
    boundary = value["execution_boundary"]
    assert all(flag is False for flag in boundary.values() if isinstance(flag, bool))
    separation = value["proof_separation"]
    assert separation["transcription_receipt_before_sampler_start"] is True
    assert separation["memory_failure_status"] == "INCOMPLETE_MEASUREMENT"
    assert separation["memory_failure_voids_transcription"] is False
    assert value["live_read_only_platform_snapshot"]["gpu_nodegroup"]["desired"] == 0
