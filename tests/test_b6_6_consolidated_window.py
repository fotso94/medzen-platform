from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from pipeline.b6_integration_receipts import (
    WINDOW_STAGES,
    ReceiptRefusal,
    ReceiptStore,
)
from scripts.b6_6_cold_rehearsal import GUARDS, FakeSecretClient, _scenario
from scripts.b6_6_credential import CredentialRefusal, rotate_and_verify
from scripts.b6_6_persistent_secret_bridge import (
    BridgeRefusal,
    ORCHESTRATOR_ROLE_ARN,
    REGISTRY_PUBLISHER_USER_ARN,
    _permanent_resource_policy,
    _verify_referenced_principals,
)
from scripts.b6_6_runner import RunContext, Runner, StageResult


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_STAGES = (
    "stage0",
    "deadline",
    "workers_ready",
    "dra_ready",
    "rag_ready",
    "asr_ready",
    "tts_ready",
    "llm_ready",
    "orchestrator_ready",
    "controller_window",
    "controller_ready",
    "pre_endpoint_images",
    "terraform_window",
    "endpoints_ready",
    "alb_ready",
    "fargate_probe",
    "alb_tag_mutation_warning",
    "file_proof",
    "websocket_proof",
    "cancellation_proof",
    "failure_drills",
    "isolation_proof",
    "cleanup",
)


def test_exact_23_stage_chain_and_invariant_lists_are_complete() -> None:
    assert WINDOW_STAGES == EXPECTED_STAGES
    assert set(GUARDS) == set(EXPECTED_STAGES)
    assert all(GUARDS[stage] for stage in EXPECTED_STAGES)


def test_full_pass_and_every_induced_failure_persist_receipts_and_cleanup(tmp_path: Path) -> None:
    passed = _scenario(tmp_path, "pass", None)
    assert passed["outcome"] == "PASS"
    assert [item["stage"] for item in passed["receipts"]] == list(EXPECTED_STAGES)
    assert all(item["status"] == "PASS" for item in passed["receipts"])
    for index, stage in enumerate(EXPECTED_STAGES, start=1):
        result = _scenario(tmp_path, f"fail-{index}", stage)
        receipts = {item["stage"]: item for item in result["receipts"]}
        assert result["outcome"] == "REFUSED"
        assert receipts[stage]["status"] == "REFUSED"
        assert "cleanup" in receipts
        assert result["cleanup_complete"] is True
        assert result["real_aws_calls"] == 0
        assert result["real_kubectl_calls"] == 0


@pytest.mark.parametrize("historical_versions", [0, 1, 2, 3, 7, 25])
def test_rotation_ignores_historical_cardinality_and_writes_exact_token(
    tmp_path: Path, historical_versions: int
) -> None:
    token = tmp_path / "token"
    result = rotate_and_verify(
        FakeSecretClient(historical_versions),
        token,
        material_factory=lambda size: bytes(range(size)),
        sleep=lambda _: None,
    )
    assert result["status"] == "PASS"
    assert result["historical_version_count_evaluated"] is False
    assert result["secret_tag_count_evaluated"] is False
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert len(token.read_bytes()) == 44
    assert token.read_bytes().endswith(b"\n")
    assert token.read_bytes().count(b"\n") == 1


def test_operator_plaintext_read_is_a_refusal(tmp_path: Path) -> None:
    client = FakeSecretClient()
    client.get_secret_value = lambda **_: {"SecretString": "forbidden"}  # type: ignore[method-assign]
    with pytest.raises(CredentialRefusal):
        rotate_and_verify(client, tmp_path / "token", sleep=lambda _: None)


def test_bridge_policy_uses_the_established_orchestrator_role() -> None:
    policy = json.loads(_permanent_resource_policy())
    assert policy["Statement"][0]["Principal"]["AWS"] == (
        "arn:aws:iam::558069890522:role/medzen-orch-role"
    )
    assert policy["Statement"][1]["Condition"]["ArnNotEquals"] == {
        "aws:PrincipalArn": "arn:aws:iam::558069890522:role/medzen-orch-role"
    }
    assert "medzen-speech-orchestrator" not in _permanent_resource_policy()


class FakeIam:
    def __init__(self, role_arn: str = ORCHESTRATOR_ROLE_ARN) -> None:
        self.role_arn = role_arn

    def get_role(self, **_: str) -> dict:
        return {"Role": {"Arn": self.role_arn}}

    def get_user(self, **_: str) -> dict:
        return {"User": {"Arn": REGISTRY_PUBLISHER_USER_ARN}}


class FakeIamSession:
    def __init__(self, iam: FakeIam) -> None:
        self.iam = iam

    def client(self, service: str) -> FakeIam:
        assert service == "iam"
        return self.iam


def test_bridge_resolves_every_exact_principal_before_any_mutation() -> None:
    assert _verify_referenced_principals(FakeIamSession(FakeIam())) == (
        ORCHESTRATOR_ROLE_ARN,
        REGISTRY_PUBLISHER_USER_ARN,
    )
    with pytest.raises(BridgeRefusal, match="does not resolve exactly"):
        _verify_referenced_principals(
            FakeIamSession(FakeIam("arn:aws:iam::558069890522:role/wrong"))
        )
    source = (ROOT / "scripts/b6_6_persistent_secret_bridge.py").read_text()
    verification = source.index("referenced_principals = _verify_referenced_principals")
    assert verification < source.index("client.restore_secret")
    assert verification < source.index("client.put_resource_policy")


def test_receipt_engine_is_write_once_and_fails_closed(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    receipt = store.persist("stage0", "PASS", {"safe": True})
    assert receipt["status"] == "PASS"
    with pytest.raises(ReceiptRefusal):
        store.persist("stage0", "PASS", {"safe": True})
    with pytest.raises(ReceiptRefusal):
        store.persist("stage0", "UNKNOWN", {})
    with pytest.raises(ReceiptRefusal):
        store.persist("unknown", "PASS", {})
    with pytest.raises(ReceiptRefusal):
        store.persist("deadline", "PASS", {"stdout": "not allowed"})


class BeforeRunFailure:
    def before_run(self, context: RunContext) -> None:
        del context
        raise RuntimeError("injected")

    def execute(self, stage: str, context: RunContext) -> StageResult:
        del context
        assert stage == "cleanup"
        return StageResult({"zero_state": True})

    def recover_cleanup(self, context: RunContext) -> dict:
        del context
        return {"recovery_completed": True}


def test_top_level_exception_gets_terminal_receipt_and_cleanup(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path / "receipts")
    context = RunContext(
        kubeconfig=tmp_path / "kubeconfig",
        authorization=tmp_path / "authorization",
        packet_sha256="0" * 64,
        receipts_dir=tmp_path / "receipts",
        token_file=tmp_path / "token",
        attempt=1,
    )
    result = Runner(BeforeRunFailure(), store).run(context)
    assert result.outcome == "REFUSED"
    assert store.load("runner_exception")["payload"]["terminal_classification"] == "EXCEPTION"
    assert store.load("cleanup")["status"] == "PASS"


def test_r1_persistent_secret_and_cleanup_boundary_are_structural() -> None:
    terraform = (ROOT / "infra/b6_client_secret.tf").read_text()
    override = (ROOT / "infra/b6_6_persistent_secret_override.tf").read_text()
    cleanup = (ROOT / "scripts/b6_6_cleanup.sh").read_text()
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    bridge = (ROOT / "scripts/b6_6_persistent_secret_bridge.py").read_text()
    assert 'resource "aws_secretsmanager_secret" "b6_client_keys"' in terraform
    assert "DenyEveryOtherPrincipalRead" in terraform
    assert override.count("prevent_destroy = true") == 3
    assert "SCHEDULED_RECOVERABLE_DELETION" not in cleanup
    assert "restore-secret" not in operations
    assert "delete-secret" not in operations
    assert "b6_6_credential.py" in operations
    assert 'persistent_synthetic_secret:"RETAINED_OPERATOR_DENIED"' in cleanup
    assert bridge.index("put_resource_policy") < bridge.index(
        '["terraform", "-chdir=infra", "state", "list"]'
    )
    assert "BlockPublicPolicy=True" in bridge


def test_endpoint_plan_includes_controller_noop_and_cleanup_uses_stage_status() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    cleanup = (ROOT / "scripts/b6_6_cleanup.sh").read_text()
    endpoint_stage = operations[operations.index("stage_terraform_window()") :]
    endpoint_stage = endpoint_stage[: endpoint_stage.index("stage_endpoints_ready()")]
    assert "-target=helm_release.b6_load_balancer_controller" in endpoint_stage
    assert "check_b6_6_window_plan.py endpoints" in endpoint_stage
    assert "terraform_window_status=" in cleanup
    assert '"$terraform_window_status" == "PASS"' in cleanup
    assert '[[ -e "$receipts_dir/terraform_window.json" ]]' not in cleanup


def test_runtime_and_cleanup_bind_the_same_packet_029_hostname_file() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    cleanup = (ROOT / "scripts/b6_6_cleanup.sh").read_text()
    expected = 'alb_hostname_file="/private/tmp/b6-029-attempt-${attempt}-alb-hostname"'
    assert expected in operations
    assert expected in cleanup


def test_terraform_receipts_bind_plan_counts_and_exact_resource_names() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    assert "terraform_plan_receipt" in operations
    assert "resource_names" in operations
    assert ".adds == 1 and .changes == 0 and .destroys == 0" in operations
    assert ".adds == 13 and .changes == 0 and .destroys == 0" in operations
    assert "jq -e --argjson expected" in operations
    for address in (
        "helm_release.b6_load_balancer_controller[0]",
        "aws_ecs_cluster.b6_probe[0]",
        "aws_ecs_task_definition.b6_probe[0]",
        "aws_iam_role.b6_probe_execution[0]",
        "aws_iam_role_policy.b6_probe_execution[0]",
        "aws_security_group.b6_probe_endpoints[0]",
        "aws_vpc_endpoint.b6_probe_ecr_api[0]",
        "aws_vpc_endpoint.b6_probe_ecr_dkr[0]",
        "aws_vpc_endpoint.b6_probe_s3[0]",
        "aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]",
        "aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]",
        "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]",
        "aws_vpc_security_group_egress_rule.b6_probe_to_ecr_endpoints[0]",
        "aws_vpc_security_group_egress_rule.b6_probe_to_s3[0]",
    ):
        assert address in operations


def test_fargate_refusal_payload_is_written_before_nonzero_return() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    stage = operations[operations.index("stage_fargate_probe()") :]
    stage = stage[: stage.index("stage_tag_result()")]
    assert stage.index('write_payload "$payload"') < stage.index(
        '[[ "$probe_status" == "0" ]] || return "$probe_status"'
    )


def test_alb_stable_health_receipt_precedes_fargate_launch() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    assert operations.index("stage_alb_ready()") < operations.index(
        "stage_fargate_probe()"
    )
    alb = operations[operations.index("stage_alb_ready()") :]
    alb = alb[: alb.index("stage_fargate_probe()")]
    assert "wait-ready --profile medzen --wait-seconds 900" in alb
    assert alb.index('write_payload "$payload"') < alb.index(
        '[[ "$ready_status" == "0" ]] || return "$ready_status"'
    )


def test_r6_settled_controls_remain_in_canonical_sources() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    endpoints = (ROOT / "scripts/b6_6_probe_endpoints.py").read_text()
    deadline = (ROOT / "scripts/b6_6_deadline.py").read_text()
    runner = (ROOT / "scripts/b6_6_runner.py").read_text()
    assert operations.index("stage_pre_endpoint_images") < operations.index("stage_terraform_window")
    assert 'identifiers = ["*"]' in (ROOT / "infra/b6_6_endpoint_policy_override.tf").read_text()
    assert "DEFERRED_TO_THREE_PROBE_LAUNCHES" in endpoints
    assert "describe_prefix_lists" in endpoints
    assert "describe_security_group_rules" not in endpoints
    assert '"tag:Boundary"' not in endpoints
    assert 'item.get("ServiceName") in set(SERVICES.values())' in endpoints
    assert "wait-seconds 1200" in operations
    assert "WINDOW_SECONDS = 4500" in deadline
    assert "WARNING_OUTSIDE_APPROVED_STAGE" in runner
    fatal_rule = (ROOT / "platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json").read_text()
    for action in ("CreateListener", "CreateRule", "DeleteListener", "DeleteRule"):
        assert action in fatal_rule


def test_only_one_b6_6_script_family_and_receipt_engine_remain() -> None:
    scripts = [path.name for path in (ROOT / "scripts").glob("b6_6_*")]
    assert not any("successor" in name or "images_before_endpoints" in name for name in scripts)
    assert not (ROOT / "pipeline/b6_integration_receipts_v2.py").exists()
    assert not (ROOT / "scripts/b6_6_receipt_v2.py").exists()
    assert not (ROOT / "scripts/b6_6_stage_runtime.sh").exists()


def test_standing_verifier_policy_prohibits_incidental_checks() -> None:
    value = json.loads(
        (ROOT / "platform/decisions/B6-WINDOW-VERIFIER-POLICY-2026-001.json").read_bytes()
    )
    assert value["source_review"].endswith("R5")
    assert "Historical secret-version cardinality" in value["prohibited"]
    assert "Every packet enumerates each stage's invariant list." in value["required"]
