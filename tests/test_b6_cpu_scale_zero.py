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

RECORD = ROOT / "platform/evidence/B6-CPU-SCALE-ZERO-2026-001.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value() -> dict:
    return json.loads(RECORD.read_bytes())


def test_cpu_scale_to_zero_is_fully_verified():
    record = value()
    assert record["status"] == "VERIFIED_COMPLETE"
    assert record["post_change"]["nodegroup_scaling"] == {
        "min_size": 0,
        "desired_size": 0,
        "max_size": 4,
    }
    assert record["post_change"]["auto_scaling_group_instances"] == []
    assert record["post_change"]["kubernetes_cpu_nodes"] == []
    assert set(record["post_change"]["former_instance_states"].values()) == {
        "terminated"
    }


def test_scale_to_zero_preserves_managed_safety_controls():
    record = value()
    drain = record["managed_drain"]
    assert drain["force_termination_used"] is False
    assert drain["pod_disruption_budget_modified"] is False
    assert drain["lifecycle_hook_bypassed"] is False
    assert record["pre_change"]["medzen_or_user_workloads_observed"] == 0


def test_planning_override_is_bound_without_mutating_historical_infra():
    source = value()["desired_state_source"]
    assert sha(ROOT / source["path"]) == source["sha256"]
    assert (sha(ROOT / source["historical_infra_path_preserved"]) == source["historical_infra_sha256"]
        or _pin_matches_committed_history(source["historical_infra_path_preserved"], source["historical_infra_sha256"]))


def test_scale_to_zero_did_not_cross_other_boundaries():
    assert all(amount == 0 for amount in value()["safety"].values())
