from __future__ import annotations

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
    lint_rendered_plan_description_charset,
)
from scripts.b6_6_probe_endpoints import (
    EndpointRefusal,
    _s3_prefix_list_id,
    _verify_policy,
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


def test_empirical_gate_replaces_network_shape_assertions() -> None:
    result = _aws_read_fixture_fidelity()["network_reduction"]
    assert result == {
        "status": "PASS",
        "network_shape_assertions": 0,
        "policy_documents_verified": 3,
        "gateway_route_table_assertion": 1,
        "s3_prefix_list_api": "ec2:DescribePrefixLists",
        "connectivity_evidence": "THREE_CONSECUTIVE_PRIVATE_PROBE_LAUNCHES",
        "required_consecutive_probe_passes": 3,
    }


def test_cold_rehearsal_lints_every_projected_rendered_plan_description() -> None:
    result = _terraform_description_charset_lint()
    assert result["status"] == "PASS"
    assert result["description_fields"] == 50
    assert result["string_descriptions"] == 48
    assert result["null_descriptions"] == 2
    assert result["invalid_descriptions"] == 0
    assert result["invalid_description_refusal_cases"] == 1
    assert result["real_aws_calls"] == 0


def test_cold_rehearsal_binds_recorded_aws_read_response_fixtures() -> None:
    result = _aws_read_fixture_fidelity()
    assert result["status"] == "PASS"
    assert result["runtime_read_api_count"] == 23
    assert result["fixture_count"] == 30
    assert result["recorded_healthy_target_health_fixture_count"] == 1
    assert result["uncovered_read_apis"] == 0
    assert result["describe_vpc_endpoints_prefix_list_id_present"] is False
    assert result["s3_prefix_list_id"] == "pl-6ea54007"
    assert result["real_aws_calls"] == 0


def test_prefix_list_reader_uses_recorded_real_describe_prefix_lists_response() -> None:
    response = json.loads(
        (
            ROOT
            / "tests/fixtures/aws/ec2-describe-prefix-lists-s3-eu-central-1.json"
        ).read_bytes()
    )

    class RecordedResponseClient:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def describe_prefix_lists(self, **kwargs: object) -> dict[str, object]:
            self.requests.append(kwargs)
            return response

    client = RecordedResponseClient()
    assert _s3_prefix_list_id(client) == "pl-6ea54007"
    assert client.requests == [
        {
            "Filters": [
                {
                    "Name": "prefix-list-name",
                    "Values": ["com.amazonaws.eu-central-1.s3"],
                }
            ]
        }
    ]


def test_policy_document_checks_remain_exact_and_fail_closed() -> None:
    allowed = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "MinimumEcrLayerBucketRead",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                    "Resource": "arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*",
                }
            ],
        }
    )
    _verify_policy(
        allowed,
        sid="MinimumEcrLayerBucketRead",
        actions={"s3:GetObject"},
        resources={"arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*"},
    )
    with pytest.raises(EndpointRefusal, match="policy boundary"):
        _verify_policy(
            allowed,
            sid="wrong",
            actions={"s3:GetObject"},
            resources={"arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*"},
        )


def test_injected_pre_model_refusal_persists_exact_safe_error_text(
    tmp_path: Path,
) -> None:
    _stage_a_scenario(tmp_path, "safe-error", "stage_a_endpoints")
    receipt = json.loads((tmp_path / "safe-error/stage_a_endpoints.json").read_bytes())
    assert receipt["status"] == "REFUSED"
    assert receipt["payload"]["safe_exception_text"] == "INJECTED_STAGE_A_FAILURE"


def test_live_verifier_exception_persists_exact_safe_error_text(tmp_path: Path) -> None:
    exact = "s3 gateway route-table boundary differs"

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
