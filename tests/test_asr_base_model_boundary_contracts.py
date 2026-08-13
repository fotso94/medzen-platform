from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_boundary_contracts import (
    BOUNDARY_CONTRACTS,
    DRA_WAIT_MAX_SECONDS,
    BoundaryContractRefusal,
    audit_bounded_helper_calls,
    invoke_dra_waiter,
    validate_boundary_parameters,
)
from scripts.asr_base_model_pilot_fake import build_rehearsal_operations


def test_attempt_thirteen_dra_mismatch_is_now_impossible() -> None:
    assert DRA_WAIT_MAX_SECONDS == 300
    with pytest.raises(BoundaryContractRefusal) as captured:
        validate_boundary_parameters(
            "dra_wait", timeout_seconds=600, poll_seconds=2
        )
    assert captured.value.reason_code == "BOUNDARY_PARAMETER_OUT_OF_RANGE"


def test_real_and_fake_dra_waiters_share_validation_before_invocation(tmp_path: Path) -> None:
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {"status": "DRA_STABLE_READY"}

    with pytest.raises(BoundaryContractRefusal):
        invoke_dra_waiter(
            fake, kubeconfig=tmp_path / "kubeconfig", timeout_seconds=301
        )
    assert calls == []
    assert invoke_dra_waiter(
        fake, kubeconfig=tmp_path / "kubeconfig", timeout_seconds=300
    )["status"] == "DRA_STABLE_READY"
    assert calls[0]["timeout_seconds"] == 300


def test_rehearsal_operation_cannot_bypass_shared_dra_contract(tmp_path: Path) -> None:
    bindings = json.loads(
        (ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002L.json").read_bytes()
    )
    operations, _ = build_rehearsal_operations(bindings)
    calls = []
    operations._dra_waiter = lambda **kwargs: calls.append(kwargs) or {"status": "PASS"}
    with pytest.raises(BoundaryContractRefusal):
        operations._dra_readiness(
            operations._dra_waiter,
            kubeconfig=tmp_path / "kubeconfig",
            timeout_seconds=301,
        )
    assert calls == []


def test_static_call_site_contract_audit_covers_every_bounded_helper() -> None:
    result = audit_bounded_helper_calls(ROOT)
    assert result["status"] == "PASS_ALL_BOUNDED_HELPER_CALLS"
    assert result["call_site_count"] >= 25
    assert result["fake_may_bypass_validation"] is False
    assert {item["boundary"] for item in result["call_sites"]} == set(
        BOUNDARY_CONTRACTS
    )
    assert all(
        item["parameters"].get("timeout_seconds") != 600
        for item in result["call_sites"]
        if item["boundary"] == "dra_wait"
    )


def test_injected_boundaries_are_called_only_from_validating_wrappers() -> None:
    result = audit_bounded_helper_calls(ROOT)
    assert {item["wrapper"] for item in result["direct_injected_boundaries"]} == {
        "_command",
        "_kubectl",
        "_ssm",
    }
    assert any(
        item["helper"] == "invoke_dra_waiter"
        and item["path"] == "scripts/asr_base_model_pilot_live.py"
        for item in result["call_sites"]
    )


def test_historical_dra_helper_remains_byte_compatible_and_is_executor_bound() -> None:
    from scripts.asr_base_model_pilot_integrity import (
        SHARED_BOUNDARY_GATED_EXECUTOR_MODULE_PATHS,
    )

    assert "scripts/run_b6a_003c_c_proof.py" in SHARED_BOUNDARY_GATED_EXECUTOR_MODULE_PATHS
    assert (ROOT / "scripts/run_b6a_003c_c_proof.py").read_bytes() == (
        __import__("subprocess").run(
            [
                "git",
                "show",
                "d0d2afc0f8a137b5819d1c84d3e9cdc48ec76676:scripts/run_b6a_003c_c_proof.py",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
    )


def test_every_boundary_rejects_below_and_above_its_integer_range() -> None:
    for boundary, parameters in BOUNDARY_CONTRACTS.items():
        baseline = {name: bound.default for name, bound in parameters.items()}
        assert validate_boundary_parameters(boundary, **baseline)["status"] == "PASS_SHARED_BOUNDARY_PARAMETERS"
        for name, bound in parameters.items():
            for invalid in (bound.minimum - 1, bound.maximum + 1):
                candidate = dict(baseline)
                candidate[name] = invalid
                with pytest.raises(BoundaryContractRefusal):
                    validate_boundary_parameters(boundary, **candidate)


def test_nodegroup_desired_capacity_is_part_of_the_shared_contract() -> None:
    assert validate_boundary_parameters(
        "nodegroup", desired=0, timeout_seconds=1200
    )["status"] == "PASS_SHARED_BOUNDARY_PARAMETERS"
    assert validate_boundary_parameters(
        "nodegroup", desired=1, timeout_seconds=1200
    )["status"] == "PASS_SHARED_BOUNDARY_PARAMETERS"
    with pytest.raises(BoundaryContractRefusal):
        validate_boundary_parameters(
            "nodegroup", desired=2, timeout_seconds=1200
        )
