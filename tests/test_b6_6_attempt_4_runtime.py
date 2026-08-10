from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeElbv2:
    alb = "arn:aws:elasticloadbalancing:eu-central-1:558069890522:loadbalancer/app/medzen-b6-window/1111111111111111"
    listener = "arn:aws:elasticloadbalancing:eu-central-1:558069890522:listener/app/medzen-b6-window/1111111111111111/2222222222222222"
    rules = [
        "arn:aws:elasticloadbalancing:eu-central-1:558069890522:listener-rule/app/medzen-b6-window/1111111111111111/2222222222222222/3333333333333331",
        "arn:aws:elasticloadbalancing:eu-central-1:558069890522:listener-rule/app/medzen-b6-window/1111111111111111/2222222222222222/3333333333333332",
        "arn:aws:elasticloadbalancing:eu-central-1:558069890522:listener-rule/app/medzen-b6-window/1111111111111111/2222222222222222/3333333333333333",
    ]
    rule = rules[0]
    target_group = "arn:aws:elasticloadbalancing:eu-central-1:558069890522:targetgroup/k8s-medzen-test/4444444444444444"

    def describe_load_balancers(self, Names):
        assert Names == ["medzen-b6-window"]
        return {"LoadBalancers": [{
            "LoadBalancerArn": self.alb,
            "LoadBalancerName": "medzen-b6-window",
            "Scheme": "internal",
            "Type": "application",
            "SecurityGroups": ["sg-0f0f6c66852830013"],
            "State": {"Code": "active"},
        }]}

    def describe_listeners(self, LoadBalancerArn):
        assert LoadBalancerArn == self.alb
        return {"Listeners": [{
            "ListenerArn": self.listener, "Port": 80, "Protocol": "HTTP",
        }]}

    def describe_rules(self, ListenerArn):
        assert ListenerArn == self.listener
        paths = {
            "1": ["/v1/conversations/speech", "/v1/conversations/speech/*"],
            "2": ["/v1/conversations/stream", "/v1/conversations/stream/*"],
            "3": ["/readyz", "/readyz/*"],
        }
        rules = [
            {
                "RuleArn": arn,
                "Priority": priority,
                "IsDefault": False,
                "Conditions": [{
                    "Field": "path-pattern",
                    "PathPatternConfig": {"Values": paths[priority]},
                }],
                "Actions": [{
                    "Type": "forward",
                    "ForwardConfig": {"TargetGroups": [{
                        "TargetGroupArn": self.target_group,
                    }]},
                }],
            }
            for priority, arn in zip(("1", "2", "3"), self.rules)
        ]
        rules.append({"RuleArn": self.rule + "/default", "IsDefault": True})
        return {"Rules": rules}

    def describe_target_groups(self, LoadBalancerArn):
        assert LoadBalancerArn == self.alb
        return {"TargetGroups": [{"TargetGroupArn": self.target_group}]}

    def describe_target_health(self, TargetGroupArn):
        assert TargetGroupArn == self.target_group
        return {"TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]}

    def describe_tags(self, ResourceArns):
        from scripts.b6_6_lbc_runtime import REQUIRED_TAGS

        return {"TagDescriptions": [
            {
                "ResourceArn": arn,
                "Tags": [{"Key": key, "Value": value} for key, value in REQUIRED_TAGS.items()],
            }
            for arn in ResourceArns
        ]}


def test_attempt_4_alb_live_proof_requires_exact_tags_and_healthy_target() -> None:
    from scripts.b6_6_lbc_runtime import verify_live

    proof = verify_live(FakeElbv2())
    assert proof["internal_alb"] is True
    assert proof["target_healthy"] is True
    assert proof["creation_time_exact_tags"] is True
    assert proof["required_tag_count"] == 6
    assert proof["tagged_resource_count"] == 5
    assert proof["route_count"] == 3
    assert set(proof["tag_mutation_resource_arns"]) == {
        FakeElbv2.listener, *FakeElbv2.rules,
    }


def test_attempt_4_parses_only_exact_child_resource_tag_denials() -> None:
    from scripts.b6_6_lbc_runtime import RuntimeEvidenceRefusal, parse_denials

    valid = (
        '2026-08-10T04:00:00Z api error AccessDeniedException: not authorized to perform: '
        'elasticloadbalancing:AddTags on resource: '
        f'{FakeElbv2.listener}'
    )
    assert parse_denials(valid) == [{
        "operation": "elasticloadbalancing:AddTags",
        "error_code": "AccessDeniedException",
        "resource_arn": FakeElbv2.listener,
        "observed_utc": "2026-08-10T04:00:00Z",
        "timing": "POST_CREATE",
    }]
    with pytest.raises(RuntimeEvidenceRefusal, match="ambiguous"):
        parse_denials("2026-08-10T04:00:00Z AccessDenied without operation or ARN")
    with pytest.raises(RuntimeEvidenceRefusal, match="unknown"):
        parse_denials(
            '2026-08-10T04:00:00Z api error UnauthorizedOperation: '
            f'elasticloadbalancing:AddTags {FakeElbv2.listener}'
        )


def test_attempt_4_tag_warning_is_ordered_and_does_not_void_functional_proof(tmp_path) -> None:
    from pipeline.b6_integration_receipts import ReceiptStore
    from scripts.b6_6_lbc_runtime import classify_runtime

    store = ReceiptStore(tmp_path)
    for stage in (
        "local_bindings", "deadline", "workers_ready", "terraform_window",
        "endpoints_ready", "controller_ready", "dra_ready", "rag_ready",
        "asr_ready", "tts_ready", "llm_ready", "orchestrator_ready",
    ):
        store.persist(stage, "PASS", {"proven": True})
    store.persist("fargate_probe", "PASS", {"readyz_request_completed": True})
    alb = store.persist("alb_ready", "PASS", {
        "internal_alb": True,
        "alb_security_group": "sg-0f0f6c66852830013",
        "listener_port": 80,
        "route_count": 3,
        "target_healthy": True,
        "creation_time_exact_tags": True,
        "tagged_resource_count": 5,
        "tag_mutation_resource_arns": [FakeElbv2.listener, *FakeElbv2.rules],
    })
    log = (
        '2026-08-10T04:00:00Z AccessDenied: elasticloadbalancing:RemoveTags resource '
        f'{FakeElbv2.rule}'
    )
    result = classify_runtime(
        receipt=store.load("alb_ready"),
        receipt_sha256=alb["receipt_sha256"],
        controller_logs=log,
    )
    assert result["status"] == "WARNING_NON_FATAL"
    store.persist("alb_tag_mutation_warning", "WARNING_NON_FATAL", result)
    store.persist("file_proof", "PASS", {"synthetic": True})
    assert store.require_pass("file_proof")["status"] == "PASS"


def test_attempt_4_binds_rotated_token_and_remaining_allowance() -> None:
    from scripts.b6_6_deadline import WINDOW_SECONDS
    from scripts.b6_6_token_binding import BEARER_SHA256

    evidence = json.loads(
        (ROOT / "platform/evidence/B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json").read_bytes()
    )
    assert BEARER_SHA256 == evidence["rotation_and_verification"]["bearer_token_sha256"]
    assert WINDOW_SECONDS == 14400 - 4819 == 9581
    assert hashlib.sha256((ROOT / "scripts/b6_6_token_binding.py").read_bytes()).hexdigest()


def test_attempt_4_secret_preflight_requires_exact_version_and_policies() -> None:
    from scripts.b6_6_secret_preflight import NEW_VERSION, verify
    from scripts.check_b6_client_secret_restoration_plan import expected_kms_policy
    from scripts.run_b6_client_secret_restoration import (
        EXPECTED_ACCOUNT,
        EXPECTED_OPERATOR,
        KMS_KEY,
        OLD_VERSION,
        SECRET_ARN,
        SECRET_NAME,
        expected_resource_policy,
        expected_tags,
    )

    class Secrets:
        def describe_secret(self, SecretId):
            assert SecretId == SECRET_ARN
            return {
                "ARN": SECRET_ARN,
                "Name": SECRET_NAME,
                "KmsKeyId": KMS_KEY,
                "Tags": [{"Key": key, "Value": value} for key, value in expected_tags().items()],
            }

        def list_secret_version_ids(self, SecretId, IncludeDeprecated):
            assert SecretId == SECRET_ARN and IncludeDeprecated is True
            return {"Versions": [
                {"VersionId": NEW_VERSION, "VersionStages": ["AWSCURRENT"]},
                {"VersionId": OLD_VERSION, "VersionStages": []},
            ]}

        def get_resource_policy(self, SecretId):
            assert SecretId == SECRET_ARN
            return {"ResourcePolicy": json.dumps(expected_resource_policy())}

    class Iam:
        def get_role_policy(self, RoleName, PolicyName):
            assert RoleName == "medzen-orch-role"
            assert PolicyName == "medzen-orch-b6-client-secret-kms"
            return {"PolicyDocument": expected_kms_policy()}

    result = verify(
        Secrets(), Iam(), {"Account": EXPECTED_ACCOUNT, "Arn": EXPECTED_OPERATOR}
    )
    assert result["status"] == "PASS"
    assert result["secret_version_id"] == NEW_VERSION
    assert result["plaintext_read"] is False


def test_attempt_4_runner_refuses_secret_or_tag_rule_drift_before_probes() -> None:
    source = (ROOT / "scripts/run_b6_6_integration_window.sh").read_text()
    secret_gate = (ROOT / "scripts/b6_6_secret_preflight.py").read_text()
    assert "d09d567e-9bde-482a-b95a-3cab990a1006" in secret_gate
    assert "f78c8aa8-2765-4788-9928-dd1ba7c406bf" not in secret_gate
    assert "scripts/b6_6_secret_preflight.py --profile medzen" in source
    assert source.index("scripts/b6_6_lbc_runtime.py verify") < source.index("scripts/b6_6_probe.py file")
    assert source.index("alb_tag_mutation_warning") < source.index("scripts/b6_6_probe.py file")


def test_attempt_4_packet_keeps_its_approved_source_bindings() -> None:
    packet = (
        ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-013-b6-6-attempt-4.md"
    ).read_text()
    authorization = json.loads(
        (ROOT / "platform/decisions/B6-AWS-AUTH-2026-013-b6-6-attempt-4.json").read_bytes()
    )
    assert "Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**" in packet
    assert "This draft itself authorizes no AWS or Kubernetes mutation" in packet
    assert "Approve B6 AWS change packet 2026-013 only." in packet
    assert "Maximum attempt-4 window: `11,243 seconds`" in packet
    assert "Charged before attempt 4: `3,157 seconds`" in packet
    assert "new reservation: `$0`" in packet
    assert "d09d567e-9bde-482a-b95a-3cab990a1006" in packet
    assert "3a30b00fc96111490c2b471eec5eebe1c9d26bf991508428cf2f5511e306b84a" in packet
    assert "WARNING_NON_FATAL" in packet
    assert "B5 remains `BLOCKED`" in packet
    for relative, expected in authorization["source_bindings"].items():
        assert f"`{relative}` | `{expected}`" in packet


def test_attempt_4_preparation_evidence_is_non_authorizing_and_hash_bound() -> None:
    evidence = json.loads(
        (ROOT / "platform/evidence/B6-6-ATTEMPT-4-LOCAL-PREPARATION-2026-001.json").read_bytes()
    )
    packet = ROOT / evidence["packet"]["path"]
    assert evidence["packet"]["authorized"] is False
    assert hashlib.sha256(packet.read_bytes()).hexdigest() == evidence["packet"]["sha256"]
    assert evidence["tests"]["canonical"]["passed"] == 1348
    assert evidence["live_read_only_preview"]["status"] == (
        "NOT_COMPLETED_LOCAL_DNS_TO_AWS_STS_UNAVAILABLE"
    )
    assert evidence["live_read_only_preview"]["aws_mutations"] == 0
    assert all(value == 0 for value in evidence["explicit_non_events"].values())
