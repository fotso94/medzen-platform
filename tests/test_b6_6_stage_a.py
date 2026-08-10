from __future__ import annotations

from pathlib import Path

from pipeline.b6_integration_receipts import (
    STAGE_A_EXECUTION_STAGES,
    STAGE_A_STAGES,
)
from scripts.b6_6_cold_rehearsal import _stage_a_scenario
from scripts.b6_6_stage_a import (
    MAXIMUM_COST_USD,
    MAXIMUM_SECONDS,
    STABLE_PROBE_PASSES,
)


ROOT = Path(__file__).resolve().parents[1]


def test_stage_a_full_pass_has_three_probes_cleanup_and_aggregate(tmp_path: Path) -> None:
    result = _stage_a_scenario(tmp_path, "pass", None)
    assert result["outcome"] == "PASS"
    assert result["cleanup_complete"] is True
    assert [item["stage"] for item in result["receipts"]] == list(STAGE_A_STAGES)
    assert all(item["status"] == "PASS" for item in result["receipts"])
    assert result["required_consecutive_probe_passes"] == 3


def test_every_stage_a_operation_failure_persists_refusal_cleanup_and_aggregate(
    tmp_path: Path,
) -> None:
    for index, stage in enumerate((*STAGE_A_EXECUTION_STAGES, "stage_a_cleanup")):
        result = _stage_a_scenario(tmp_path, f"fail-{index}", stage)
        statuses = {item["stage"]: item["status"] for item in result["receipts"]}
        assert result["outcome"] == "REFUSED"
        assert statuses[stage] == "REFUSED"
        assert statuses["stage_a"] == "REFUSED"
        expected_cleanup = "REFUSED" if stage == "stage_a_cleanup" else "PASS"
        assert statuses["stage_a_cleanup"] == expected_cleanup
        assert result["cleanup_complete"] is True


def test_stage_a_cleanup_refusal_receipt_contains_successful_bounded_recovery(
    tmp_path: Path,
) -> None:
    result = _stage_a_scenario(tmp_path, "cleanup-recovery", "stage_a_cleanup")
    cleanup = next(
        item for item in result["receipts"] if item["stage"] == "stage_a_cleanup"
    )
    assert cleanup["status"] == "REFUSED"
    assert result["cleanup_complete"] is True


def test_stage_a_has_no_kubernetes_worker_or_service_mutation_path() -> None:
    source = (ROOT / "scripts/b6_6_stage_a.py").read_text()
    assert "kubectl" not in source
    assert "update-nodegroup-config" not in source
    assert "enable_b6_load_balancer_controller=false" in source
    assert "enable_b6_integration_window=false" in source
    assert "enable_b6_probe_qualification=true" in source
    assert "QUALIFICATION_ADDRESSES" in source
    assert STABLE_PROBE_PASSES == 3
    assert MAXIMUM_SECONDS == 1800
    assert MAXIMUM_COST_USD == 0.50


def test_window_runner_requires_passing_stage_a_receipt() -> None:
    source = (ROOT / "scripts/b6_6_runner.py").read_text()
    assert "PASSING_STAGE_A_RECEIPT_REQUIRED" in source
    assert 'stage_a.get("status") != "PASS"' in source
    assert 'stage_a_cleanup.get("status") != "PASS"' in source
    assert '"window_attempts_unlocked": True' in source


def test_stage_a_terraform_flag_is_mutually_exclusive_with_window() -> None:
    terraform = (ROOT / "infra/b6_integration_window.tf").read_text()
    variables = (ROOT / "infra/variables.tf").read_text()
    assert 'check "b6_probe_mode_is_exclusive"' in terraform
    assert "!(var.enable_b6_probe_qualification && var.enable_b6_integration_window)" in terraform
    assert 'variable "enable_b6_probe_qualification"' in variables
    assert terraform.count("local.b6_probe_resources_enabled ? 1 : 0") == 9
