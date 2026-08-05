from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6A-LOCAL-ENGINEERING-2026-005.json"


def _evidence():
    return json.loads(EVIDENCE.read_text())


def test_local_005_binds_failure_design_and_sources():
    evidence = _evidence()
    assert evidence["status"] == "LOCAL_REMEDIATION_COMPLETE_GPU_RETRY_NOT_AUTHORIZED"
    for binding in (evidence["trigger"], evidence["design"]):
        assert hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() == (
            binding["sha256"]
        )
    for relative, expected in evidence["source_bindings"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_local_005_requires_stable_dra_before_workload():
    readiness = _evidence()["stable_readiness"]
    assert readiness["required_consecutive_identical_reads"] == 3
    assert readiness["poll_seconds"] == 2
    assert readiness["maximum_wait_seconds"] == 300
    assert readiness["required_pod_ready"] is True
    assert readiness["minimum_devices"] == 1
    assert readiness["workload_applied_before_stable_readiness"] is False


def test_local_005_cumulative_gpu_allowance_does_not_reset():
    budget = _evidence()["budget_control"]
    assert budget["prior_attempt_conservative_billed_seconds"] == 60
    assert budget["future_retry_maximum_seconds"] == 7140
    assert budget["conservative_cumulative_maximum_seconds"] == 7200
    assert budget["new_reservation_created"] is False


def test_local_005_performed_no_retry_mutation():
    boundary = _evidence()["execution_boundary"]
    for key, value in boundary.items():
        if key in {"b6a_complete", "b6_complete"}:
            assert value is False
        else:
            assert value is False
