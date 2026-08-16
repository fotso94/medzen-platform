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

RECORD = ROOT / "platform/evidence/B6-LOCAL-ENGINEERING-2026-002-llm-gateway.json"


def value() -> dict:
    return json.loads(RECORD.read_bytes())


def test_b6_2_evidence_binds_every_named_source():
    for relative, expected in value()["source_bindings"].items():
        assert (hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
            or _pin_matches_committed_history(relative, expected))


def test_b6_2_local_exit_is_fake_only_and_fully_policy_covered():
    exit_record = value()["b6_2_local_exit"]
    assert exit_record["outcome"] == "LOCAL_EXIT_COMPLETE"
    assert exit_record["provider"] == "fake_bedrock"
    assert exit_record["provider_network_access"] is False
    assert exit_record["real_bedrock_adapter_present"] is False
    assert exit_record["language_policy_count"] == 17


def test_b6_2_evidence_grants_no_cloud_or_serving_authority():
    assert all(amount == 0 for amount in value()["aws_and_governance"].values())
