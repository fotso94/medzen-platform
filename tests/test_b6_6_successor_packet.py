from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _policy(sid: str, actions, resources, principal="*") -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": sid,
            "Effect": "Allow",
            "Principal": principal,
            "Action": actions,
            "Resource": resources,
        }],
    })


def test_custom_endpoint_policy_keeps_required_principal_without_role_reference() -> None:
    source = (ROOT / "infra/b6_6_endpoint_policy_override.tf").read_text()
    assert source.count('type        = "*"') == 2
    assert source.count('identifiers = ["*"]') == 2
    assert "medzen-b6-window-probe-execution" not in source
    assert 'actions   = ["ecr:GetAuthorizationToken"]' in source
    assert "resources = [local.b6_probe_repository_arn]" in source


@pytest.mark.parametrize("principal", [None, {"AWS": "arn:aws:iam::1:role/wrong"}])
def test_endpoint_policy_verifier_refuses_missing_or_role_principal(principal) -> None:
    from scripts.b6_6_successor_probe_endpoints import EndpointRefusal, _verify_policy

    value = json.loads(_policy("ProbeNetworkRegistryToken", "ecr:GetAuthorizationToken", "*"))
    if principal is None:
        value["Statement"][0].pop("Principal")
    else:
        value["Statement"][0]["Principal"] = principal
    with pytest.raises(EndpointRefusal):
        _verify_policy(
            json.dumps(value),
            sid="ProbeNetworkRegistryToken",
            actions={"ecr:GetAuthorizationToken"},
            resources={"*"},
        )


def test_endpoint_policy_verifier_accepts_only_exact_wildcard_boundary() -> None:
    from scripts.b6_6_successor_probe_endpoints import _verify_policy

    _verify_policy(
        _policy("ProbeNetworkRegistryToken", "ecr:GetAuthorizationToken", "*"),
        sid="ProbeNetworkRegistryToken",
        actions={"ecr:GetAuthorizationToken"},
        resources={"*"},
    )


def _successor_plan() -> dict:
    from scripts.check_b6_6_successor_window_plan import proven

    changes = [
        {"address": address, "change": {"actions": ["create"], "after": {}}}
        for address in sorted(proven.ADDRESSES)
    ]
    by_address = {item["address"]: item["change"]["after"] for item in changes}
    by_address["aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]"].update({
        "from_port": 443,
        "to_port": 443,
        "ip_protocol": "tcp",
    })
    by_address["aws_vpc_endpoint.b6_probe_ecr_api[0]"].update({
        "policy": _policy(
            "ProbeNetworkRegistryToken", "ecr:GetAuthorizationToken", "*"
        )
    })
    by_address["aws_vpc_endpoint.b6_probe_ecr_dkr[0]"].update({
        "policy": _policy(
            "ProbeNetworkQualifiedImagePull",
            [
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            ],
            "arn:aws:ecr:eu-central-1:558069890522:repository/medzen-rag-index",
        )
    })
    return {
        "resource_changes": changes,
        "configuration": {"root_module": {"resources": [{
            "address": "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints",
            "expressions": {
                "security_group_id": {
                    "references": ["aws_security_group.b6_probe_endpoints"]
                },
                "referenced_security_group_id": {
                    "references": ["aws_security_group.b6_probe_endpoints"]
                },
            },
        }] }},
    }


def test_successor_plan_guard_normalizes_only_reviewed_delta(monkeypatch) -> None:
    from scripts import check_b6_6_successor_window_plan as guard

    observed = {}
    monkeypatch.setattr(
        guard.proven,
        "validate_create",
        lambda value: observed.setdefault("compatible", value),
    )
    guard.validate_create(_successor_plan())
    compatible = observed["compatible"]
    source = guard.proven._after(
        compatible,
        "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]",
    )
    assert source["referenced_security_group_id"] == guard.proven.PROBE_SG
    for purpose in ("ecr_api", "ecr_dkr"):
        statement = guard.proven._policy_statement(
            guard.proven._after(
                compatible, f"aws_vpc_endpoint.b6_probe_{purpose}[0]"
            )["policy"]
        )
        assert guard.proven._principal_set(statement) == {guard.ROLE_ARN}


def test_successor_plan_guard_refuses_reintroduced_role_principal(monkeypatch) -> None:
    from scripts import check_b6_6_successor_window_plan as guard

    monkeypatch.setattr(guard.proven, "validate_create", lambda _: None)
    plan = _successor_plan()
    guard.proven._after(plan, "aws_vpc_endpoint.b6_probe_ecr_api[0]")["policy"] = (
        _policy(
            "ProbeNetworkRegistryToken",
            "ecr:GetAuthorizationToken",
            "*",
            {"AWS": guard.ROLE_ARN},
        )
    )
    with pytest.raises(ValueError, match="successor endpoint policy differs"):
        guard.validate_create(plan)


def test_fargate_probe_attaches_backend_and_temporary_endpoint_groups(monkeypatch) -> None:
    from scripts import b6_6_successor_fargate_probe as probe

    monkeypatch.setattr(
        probe, "verify_available", lambda _: {"endpoint_security_group_id": "sg-temp"}
    )
    monkeypatch.setattr(probe.proven, "_task_definition", lambda _: "task-definition")
    monkeypatch.setattr(
        probe.proven,
        "_safe_task_result",
        lambda _: {"status": "PASS", "readyz_request_completed": True},
    )

    class ECS:
        network = None

        def run_task(self, **kwargs):
            self.network = kwargs["networkConfiguration"]["awsvpcConfiguration"]
            return {"tasks": [{"taskArn": "arn:task"}], "failures": []}

        def describe_tasks(self, **_):
            return {"tasks": [{"lastStatus": "STOPPED"}], "failures": []}

    ecs = ECS()
    result = probe.run_probe(
        ecs,
        object(),
        "http://internal-medzen-b6-window-123.eu-central-1.elb.amazonaws.com/readyz",
        60,
    )
    assert result["status"] == "PASS"
    assert ecs.network["securityGroups"] == sorted(
        [probe.BACKEND_SECURITY_GROUP, "sg-temp"]
    )
    assert ecs.network["assignPublicIp"] == "DISABLED"


def test_dynamic_material_binding_uses_receipt_hash_and_mode(tmp_path, monkeypatch) -> None:
    from scripts import b6_6_successor_token_binding as binding

    material = b"A" * 43
    path = tmp_path / "synthetic"
    path.write_bytes(material + b"\n")
    path.chmod(0o600)
    receipt = tmp_path / "verification.json"
    receipt.write_text(json.dumps({
        "bearer_token_sha256": hashlib.sha256(material).hexdigest()
    }))
    monkeypatch.setattr(binding, "TOKEN_PATH", path)
    monkeypatch.setattr(sys, "argv", ["binding", str(path), str(receipt)])
    assert binding.main() == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_successor_runner_places_credential_stage_before_deadline_and_compute() -> None:
    runner = (ROOT / "scripts/b6_6_successor_window.sh").read_text()
    stage0 = runner.index("b6_6_successor_credential_stage.sh")
    deadline = runner.index("b6_6_successor_deadline.py arm")
    workers = runner.index("nodegroup-name cpu --scaling-config")
    assert stage0 < deadline < workers
    assert "WINDOW_SECONDS = 4500" in (
        ROOT / "scripts/b6_6_successor_deadline.py"
    ).read_text()


def test_stage0_failure_cleanup_is_recoverable_and_precompute() -> None:
    source = (ROOT / "scripts/b6_6_successor_credential_stage.sh").read_text()
    assert "cleanup_after_refusal" in source
    assert "check_b6_client_secret_restoration_2026_015_plan.py --mode cleanup" in source
    assert "b6_6_successor_deadline.py" not in source
    assert "update-nodegroup-config" not in source


def test_successor_cleanup_retains_exact_fifteen_resource_boundary() -> None:
    source = (ROOT / "scripts/b6_6_successor_cleanup.sh").read_text()
    target_lines = [line for line in source.splitlines() if line.strip().startswith("-target=")]
    assert len(target_lines) == 15
    assert "check_b6_6_successor_window_plan.py destroy" in source
    assert source.index("b6_6_successor_probe_endpoints.py absent") < source.index(
        "b6_6_successor_deadline.py disarm"
    )
    assert 'if [[ -e "$receipts_dir/deadline.json" ]]' in source
    assert "PASS_B6_6_SUCCESSOR_PRE_DEADLINE_ZERO_STATE" in source


def test_allowance_arithmetic_leaves_one_near_full_attempt() -> None:
    assert 14400 - 5985 == 8415
    assert 8415 - 4500 == 3915
    from scripts.b6_6_successor_deadline import WINDOW_SECONDS

    assert WINDOW_SECONDS == 4500


def test_historical_packet_016_and_refusal_are_immutable() -> None:
    expected = {
        "platform/decisions/B6-AWS-CHANGE-PACKET-2026-016-b6-6-final-window.md": (
            "1560c5b6a775377cff43bf46a236bdd5da0c645cf3f846b33bc63ed50c670f6d"
        ),
        "platform/evidence/B6-PACKET-2026-016-REFUSED-ECR-ENDPOINT-POLICY.json": (
            "7538b6a3f9d80201b8161f43aef0115d0d3424d7daff33caa58e460308b940f3"
        ),
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest


def test_manifest_forbids_reuse_and_compute_before_dynamic_verification() -> None:
    value = json.loads(
        (ROOT / "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-004.json").read_bytes()
    )
    assert value["status"] == "PROPOSED_IN_PACKET_STAGE_0_NOT_AUTHORIZED"
    assert value["authorized_only_after_packet_approval"]["old_value_reuse"] is False
    assert value["dynamic_binding_rule"]["later_window_stages_may_start_only_after_verified_receipt"] is True
    assert value["failure_boundary"]["stage_0_failure_starts_compute"] is False


def test_authorization_validator_requires_reviewed_commit_identity() -> None:
    source = (ROOT / "scripts/b6_6_successor_bindings.py").read_text()
    assert 'review.get("reviewed_repository_commit")' in source
    assert 'value.get("prepared_repository_commit") != reviewed_commit' in source


def test_successor_packet_preserves_review_boundary_and_has_exact_authorization() -> None:
    packet = (
        ROOT
        / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-017-b6-6-principal-independent-successor.md"
    ).read_text()
    assert "DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL" in packet
    assert "Principal element in every custom endpoint policy" in packet
    assert "12 add / 0 change / 0 destroy" in packet
    assert "0 add / 0 change / 15 destroy" in packet
    assert "Remaining before this packet | 8,415" in packet
    assert "Maximum packet-2026-017 worker deadline | 4,500" in packet
    assert "Approve B6 AWS change packet 2026-017 only." in packet
    authorization_path = (
        ROOT
        / "platform/decisions/B6-AWS-AUTH-2026-017-b6-6-principal-independent-successor.json"
    )
    authorization = json.loads(authorization_path.read_bytes())
    assert authorization["id"] == "B6-AWS-AUTH-2026-017"
    assert authorization["status"] == "owner-approved"
    assert authorization["packet"]["sha256"] == (
        "8fa32f4013445fd18ad353119ddd10a1c5c199935059a63afedf951c61a045b6"
    )


def test_local_preparation_evidence_is_non_authorizing_and_packet_bound() -> None:
    evidence = json.loads(
        (
            ROOT
            / "platform/evidence/B6-6-PRINCIPAL-INDEPENDENT-SUCCESSOR-LOCAL-PREPARATION-2026-001.json"
        ).read_bytes()
    )
    packet = ROOT / evidence["packet"]["path"]
    assert evidence["packet"]["authorized"] is False
    assert evidence["packet"]["executable"] is False
    assert hashlib.sha256(packet.read_bytes()).hexdigest() == evidence["packet"]["sha256"]
    assert evidence["verification"]["canonical_local_suite"]["failed"] == 0
    assert evidence["non_events"]["aws_mutations"] == 0
    assert evidence["non_events"]["compute_started"] is False
