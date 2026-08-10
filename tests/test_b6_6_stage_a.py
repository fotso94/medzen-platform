from __future__ import annotations

from pathlib import Path

import pytest

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
from scripts.check_b6_6_window_plan import (
    TASK_ENI_EGRESS_RULES,
    PLAN_TASK_ENI_SECURITY_GROUPS,
    lint_task_eni_security_group_egress,
)
from scripts.b6_6_probe_endpoints import (
    EndpointRefusal,
    _verify_security_group_egress,
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
    assert terraform.count("local.b6_probe_resources_enabled ? 1 : 0") == 11


def test_probe_task_eni_sg_has_ecr_and_s3_tls_egress_and_dns_exemption() -> None:
    terraform = (ROOT / "infra/b6_integration_window.tf").read_text()
    assert 'resource "aws_vpc_security_group_egress_rule" "b6_probe_to_ecr_endpoints"' in terraform
    assert 'resource "aws_vpc_security_group_egress_rule" "b6_probe_to_s3"' in terraform
    assert "referenced_security_group_id = aws_security_group.b6_probe_endpoints[0].id" in terraform
    assert "prefix_list_id    = aws_vpc_endpoint.b6_probe_s3[0].prefix_list_id" in terraform
    assert terraform.count("from_port") >= 5
    assert "security groups cannot filter" in terraform


def test_static_lint_refuses_any_task_eni_security_group_without_egress() -> None:
    group = next(iter(PLAN_TASK_ENI_SECURITY_GROUPS))
    result = lint_task_eni_security_group_egress(
        set(PLAN_TASK_ENI_SECURITY_GROUPS), {group: set(TASK_ENI_EGRESS_RULES)}
    )
    assert result == {
        "status": "PASS",
        "task_eni_security_groups": 1,
        "egress_rules": 2,
        "missing_egress_security_groups": 0,
    }
    with pytest.raises(ValueError, match="task ENI security group has no egress rule"):
        lint_task_eni_security_group_egress(set(PLAN_TASK_ENI_SECURITY_GROUPS), {})


def test_cold_rehearsal_executes_the_static_task_eni_egress_lint() -> None:
    source = (ROOT / "scripts/b6_6_cold_rehearsal.py").read_text()
    assert "_task_eni_sg_egress_lint()" in source
    assert "missing_egress_refusal_cases" in source
    assert "NOT_APPLICABLE_AMAZON_PROVIDED_VPC_RESOLVER" in source


def test_runtime_egress_verifier_requires_exact_ecr_self_and_s3_prefix_rules() -> None:
    group = {
        "GroupId": "sg-probe",
        "IpPermissionsEgress": [
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "UserIdGroupPairs": [{"GroupId": "sg-probe"}],
                "PrefixListIds": [],
                "IpRanges": [],
                "Ipv6Ranges": [],
            },
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "UserIdGroupPairs": [],
                "PrefixListIds": [{"PrefixListId": "pl-s3"}],
                "IpRanges": [],
                "Ipv6Ranges": [],
            },
        ],
    }
    _verify_security_group_egress(group, "pl-s3")
    group["IpPermissionsEgress"].pop()
    with pytest.raises(EndpointRefusal, match="egress count"):
        _verify_security_group_egress(group, "pl-s3")
