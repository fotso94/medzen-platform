from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pipeline.b6_integration_receipts import (
    ReceiptStore,
    STAGE_A_EXECUTION_STAGES,
    STAGE_A_STAGES,
)
from scripts.b6_6_cold_rehearsal import (
    _aws_read_fixture_fidelity,
    _stage_a_scenario,
    _task_eni_sg_egress_lint,
    _terraform_description_charset_lint,
)
from scripts.b6_6_stage_a import (
    MAXIMUM_COST_USD,
    MAXIMUM_SECONDS,
    STABLE_PROBE_PASSES,
    StageAContext,
    StageARunner,
)
from scripts.check_b6_6_window_plan import (
    AWS_DESCRIPTION_CHARSET,
    TASK_ENI_EGRESS_RULES,
    PLAN_TASK_ENI_SECURITY_GROUPS,
    lint_rendered_plan_description_charset,
    lint_task_eni_security_group_egress,
)
from scripts.b6_6_probe_endpoints import (
    EgressRule,
    EndpointRefusal,
    _normalize_security_group_rules,
    _read_security_group_egress_rules,
    _verify_security_group_egress_rules,
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
    result = _task_eni_sg_egress_lint()
    assert result["status"] == "PASS"
    assert result["task_eni_security_groups"] == 2
    assert result["egress_rules"] == 3
    assert result["missing_egress_refusal_cases"] == 2


def test_cold_rehearsal_lints_every_projected_rendered_plan_description() -> None:
    result = _terraform_description_charset_lint()
    assert result["status"] == "PASS"
    assert result["description_fields"] == 50
    assert result["string_descriptions"] == 48
    assert result["null_descriptions"] == 2
    assert result["invalid_descriptions"] == 0
    assert result["invalid_description_refusal_cases"] == 1
    assert result["real_aws_calls"] == 0


def _required_domain_rules() -> list[EgressRule]:
    return [
        EgressRule(
            rule_id="sgr-domain-ecr",
            group_id="sg-probe",
            protocol="tcp",
            from_port=443,
            to_port=443,
            referenced_group_id="sg-probe",
            prefix_list_id=None,
            cidr_ipv4=None,
            cidr_ipv6=None,
        ),
        EgressRule(
            rule_id="sgr-domain-s3",
            group_id="sg-probe",
            protocol="tcp",
            from_port=443,
            to_port=443,
            referenced_group_id=None,
            prefix_list_id="pl-s3",
            cidr_ipv4=None,
            cidr_ipv6=None,
        ),
    ]


def test_recorded_real_responses_prove_merged_shape_and_minus_one_port_quirk() -> None:
    merged_path = (
        ROOT
        / "tests/fixtures/aws/ec2-describe-security-groups-sg-070fc00321934eacb.json"
    )
    rules_path = (
        ROOT
        / "tests/fixtures/aws/ec2-describe-security-group-rules-sg-070fc00321934eacb.json"
    )
    evidence = json.loads(
        (ROOT / "platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-001.json").read_bytes()
    )
    expected = {item["path"]: item["sha256"] for item in evidence["captures"]}
    for path in (merged_path, rules_path):
        relative = str(path.relative_to(ROOT))
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected[relative]

    merged = json.loads(merged_path.read_bytes())["SecurityGroups"][0]
    per_rule = json.loads(rules_path.read_bytes())
    merged_egress = merged["IpPermissionsEgress"]
    individual_egress = [
        item for item in per_rule["SecurityGroupRules"] if item["IsEgress"] is True
    ]
    assert len(merged_egress) == 1
    assert len(merged_egress[0]["UserIdGroupPairs"]) == 1
    assert len(merged_egress[0]["IpRanges"]) == 1
    assert "FromPort" not in merged_egress[0]
    assert "ToPort" not in merged_egress[0]
    assert len(individual_egress) == 2
    assert all(item["IpProtocol"] == "-1" for item in individual_egress)
    assert all(item["FromPort"] == -1 for item in individual_egress)
    assert all(item["ToPort"] == -1 for item in individual_egress)
    assert len(_normalize_security_group_rules(per_rule)) == 2


def test_cold_rehearsal_binds_recorded_aws_read_response_fixtures() -> None:
    result = _aws_read_fixture_fidelity()
    assert result["status"] == "PASS"
    assert result["merged_egress_permission_objects"] == 1
    assert result["individual_egress_rules"] == 2
    assert result["protocol_minus_one_port_quirk"] == "PASS"
    assert result["real_aws_calls"] == 0


def test_runtime_reader_uses_per_rule_api_with_recorded_real_response() -> None:
    response = json.loads(
        (
            ROOT
            / "tests/fixtures/aws/ec2-describe-security-group-rules-sg-070fc00321934eacb.json"
        ).read_bytes()
    )

    class RecordedResponseClient:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def describe_security_group_rules(self, **kwargs: object) -> dict[str, object]:
            self.requests.append(kwargs)
            return response

        def describe_security_groups(self, **_: object) -> dict[str, object]:
            raise AssertionError("merged permission API must not verify egress")

    client = RecordedResponseClient()
    rules = _read_security_group_egress_rules(client, "sg-070fc00321934eacb")
    assert len(rules) == 2
    assert client.requests == [
        {
            "Filters": [
                {"Name": "group-id", "Values": ["sg-070fc00321934eacb"]}
            ]
        }
    ]


def test_runtime_egress_policy_requires_exact_ecr_self_and_s3_prefix_rules() -> None:
    rules = _required_domain_rules()
    _verify_security_group_egress_rules(rules, "sg-probe", "pl-s3")

    with pytest.raises(EndpointRefusal, match="egress count"):
        _verify_security_group_egress_rules(rules[1:], "sg-probe", "pl-s3")
    with pytest.raises(EndpointRefusal, match="egress count"):
        _verify_security_group_egress_rules(rules[:1], "sg-probe", "pl-s3")
    with pytest.raises(EndpointRefusal, match="egress count"):
        _verify_security_group_egress_rules(
            [*rules, rules[0]], "sg-probe", "pl-s3"
        )

    missing_ecr_destination = [
        EgressRule(**{**rules[0].__dict__, "referenced_group_id": None}),
        rules[1],
    ]
    with pytest.raises(EndpointRefusal, match="egress destination"):
        _verify_security_group_egress_rules(
            missing_ecr_destination, "sg-probe", "pl-s3"
        )
    missing_s3_destination = [
        rules[0],
        EgressRule(**{**rules[1].__dict__, "prefix_list_id": None}),
    ]
    with pytest.raises(EndpointRefusal, match="egress destination"):
        _verify_security_group_egress_rules(
            missing_s3_destination, "sg-probe", "pl-s3"
        )


def test_runtime_egress_policy_refuses_unexpected_or_minus_one_rule() -> None:
    rules = _required_domain_rules()
    rules[1] = EgressRule(
        **{
            **rules[1].__dict__,
            "protocol": "-1",
            "from_port": -1,
            "to_port": -1,
        }
    )
    with pytest.raises(EndpointRefusal, match="egress transport"):
        _verify_security_group_egress_rules(rules, "sg-probe", "pl-s3")


def test_injected_pre_model_refusal_persists_exact_safe_error_text(
    tmp_path: Path,
) -> None:
    _stage_a_scenario(tmp_path, "safe-error", "stage_a_endpoints")
    receipt = json.loads((tmp_path / "safe-error/stage_a_endpoints.json").read_bytes())
    assert receipt["status"] == "REFUSED"
    assert receipt["payload"]["safe_exception_text"] == "INJECTED_STAGE_A_FAILURE"


def test_live_verifier_exception_persists_exact_safe_error_text(tmp_path: Path) -> None:
    exact = "probe endpoint SG egress count or identity differs"

    class EndpointFailureOperations:
        def before_run(self, context: StageAContext) -> None:
            del context

        def execute(self, stage: str, context: StageAContext) -> dict[str, object]:
            del context
            if stage == "stage_a_endpoints":
                raise EndpointRefusal(exact)
            if stage == "stage_a_cleanup":
                return {"cleanup_complete": True}
            return {"stage": stage}

        def recover_cleanup(self, context: StageAContext) -> dict[str, object]:
            del context
            return {"recovery_completed": True, "zero_state": True}

    receipts = tmp_path / "live-verifier-error"
    context = StageAContext(
        authorization=tmp_path / "unused.json",
        packet_sha256="0" * 64,
        receipts_dir=receipts,
    )
    result = StageARunner(
        EndpointFailureOperations(), ReceiptStore(receipts)
    ).run(context)
    receipt = json.loads((receipts / "stage_a_endpoints.json").read_bytes())
    assert result.outcome == "REFUSED"
    assert receipt["payload"]["exception_class"] == "EndpointRefusal"
    assert receipt["payload"]["safe_exception_text"] == exact


def test_rendered_plan_description_lint_accepts_full_aws_charset() -> None:
    value = "Letters 0123456789. _-:/()#,@[]+=&;{}!$*"
    assert AWS_DESCRIPTION_CHARSET.fullmatch(value)
    result = lint_rendered_plan_description_charset(
        {
            "planned_values": {
                "description": value,
                "nullable": {"description": None},
            },
            "configuration": {
                "description": {"constant_value": "Also valid; value"}
            },
        }
    )
    assert result == {
        "status": "PASS",
        "description_fields": 3,
        "string_descriptions": 2,
        "null_descriptions": 1,
        "invalid_descriptions": 0,
        "allowed_character_class": "A-Za-z0-9. _-:/()#,@[]+=&;{}!$*",
    }


def test_rendered_plan_description_lint_refuses_apostrophe_anywhere() -> None:
    plan = {
        "resource_changes": [
            {"change": {"after": {"description": "ECR's S3 layer endpoint"}}}
        ]
    }
    with pytest.raises(ValueError, match=r"U\+0027"):
        lint_rendered_plan_description_charset(plan)


def test_rendered_plan_description_lint_fails_closed_on_unknown_shape() -> None:
    with pytest.raises(ValueError, match="not a known string"):
        lint_rendered_plan_description_charset(
            {"configuration": {"description": {"references": ["var.value"]}}}
        )
