from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COST = ROOT / "platform/finance/COST-REGISTRY-2026-004.json"
MANIFEST = ROOT / "platform/k8s/b6-6/integration-window.yaml"
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-008-b6-6-integration-window-executable.md"


def documents():
    return [value for value in yaml.safe_load_all(MANIFEST.read_text()) if value]


def test_cost_registry_activates_exactly_one_ten_dollar_reservation():
    value = json.loads(COST.read_bytes())
    summary = value["guardrail_summary"]
    assert value["id"] == "COST-REGISTRY-2026-004"
    assert summary["recognized_committed_guardrail_usd"] == 63.5288
    assert summary["active_reservations_usd"] == 10.0
    assert summary["committed_plus_reserved_usd"] == 73.5288
    assert summary["guardrail_headroom_after_reservations_usd"] == 226.4712
    active = [
        item for item in value["allocations"]
        if item["active_reservation_usd"] > 0
    ]
    assert len(active) == 1
    assert active[0]["allocation_id"] == "B6-INTEGRATION-WINDOW-2026-001"
    assert value["controls"]["reservation_is_aws_authorization"] is False


def test_manifest_is_digest_pinned_zero_replica_and_dependency_private():
    docs = documents()
    deployments = {
        item["metadata"]["name"]: item
        for item in docs if item["kind"] == "Deployment"
    }
    assert set(deployments) == {
        "rag-index", "asr-runtime", "tts-gateway", "llm-gateway",
        "speech-orchestrator",
    }
    assert all(item["spec"]["replicas"] == 0 for item in deployments.values())
    images = []
    for item in deployments.values():
        pod = item["spec"]["template"]["spec"]
        images.extend(container["image"] for container in pod.get("initContainers", []))
        images.extend(container["image"] for container in pod["containers"])
    assert len(images) == 6
    assert all("@sha256:" in image and ":PLACEHOLDER_TAG" not in image for image in images)
    services = {
        item["metadata"]["name"]: item
        for item in docs if item["kind"] == "Service"
    }
    assert set(services) == set(deployments)
    assert all(item["spec"]["type"] == "ClusterIP" for item in services.values())
    ingresses = [item for item in docs if item["kind"] == "Ingress"]
    assert len(ingresses) == 1
    ingress = ingresses[0]
    assert ingress["metadata"]["name"] == "speech-orchestrator-b6-window"
    annotations = ingress["metadata"]["annotations"]
    assert annotations["alb.ingress.kubernetes.io/scheme"] == "internal"
    assert annotations["alb.ingress.kubernetes.io/security-groups"] == "sg-0f0f6c66852830013"
    assert annotations["alb.ingress.kubernetes.io/manage-backend-security-group-rules"] == "false"
    backends = {
        path["backend"]["service"]["name"]
        for path in ingress["spec"]["rules"][0]["http"]["paths"]
    }
    assert backends == {"speech-orchestrator"}


def test_manifest_keeps_real_providers_and_production_pointer_absent():
    raw = MANIFEST.read_text()
    assert "MEDZEN_LLM_PROVIDER, value: fake" in raw
    assert "MEDZEN_SPEECH_TTS_PROVIDER, value: text_only" in raw
    assert "/medzen/registry/test/b6/d4f9696d" in raw
    assert "/medzen/registry/serving/current" not in raw
    assert "approved/asr" not in raw


def test_window_terraform_has_exact_ephemeral_network_and_probe_boundary():
    raw = (ROOT / "infra/b6_integration_window.tf").read_text()
    assert 'b6_backend_security_group_id = "sg-0a83abae6ab954543"' in raw
    assert 'b6_node_security_group_id    = "sg-070fc00321934eacb"' in raw
    assert raw.count("var.enable_b6_integration_window ? 1 : 0") == 6
    assert "ecr:GetAuthorizationToken" in raw
    assert "ecr:BatchGetImage" in raw
    for forbidden in (
        "secretsmanager:GetSecretValue", "ssm:GetParameter", "kms:Decrypt",
        "logs:CreateLogGroup", "bedrock:", "s3:PutObject",
    ):
        assert forbidden not in raw
    assert "readonlyRootFilesystem = true" in raw
    assert "assignPublicIp" not in raw


def test_runner_and_cleanup_are_shell_valid_and_deadline_first():
    runner = ROOT / "scripts/run_b6_6_integration_window.sh"
    cleanup = ROOT / "scripts/b6_6_cleanup.sh"
    subprocess.run(["bash", "-n", str(runner), str(cleanup)], check=True)
    value = runner.read_text()
    assert value.index("scripts/b6_6_deadline.py arm") < value.index("desiredSize=2")
    assert value.index("scripts/b6_6_deadline.py arm") < value.index("desiredSize=1")
    assert "trap cleanup EXIT INT TERM" in value
    closed = cleanup.read_text()
    assert closed.index("delete ingress/speech-orchestrator-b6-window") < closed.index("enable_b6_load_balancer_controller=false")
    assert closed.index("enable_b6_load_balancer_controller=false") < closed.index("nodegroup-name cpu")
    assert "SCHEDULED_RECOVERABLE_DELETION" in closed


def test_receipts_are_write_once_ordered_and_reject_sensitive_fields(tmp_path):
    from pipeline.b6_integration_receipts import ReceiptRefusal, ReceiptStore

    store = ReceiptStore(tmp_path)
    store.persist("local_bindings", "PASS", {"bindings_verified": True})
    store.persist("deadline", "PASS", {"deadline_utc": "2026-08-09T23:00:00Z"})
    with pytest.raises(ReceiptRefusal, match="overwrite"):
        store.persist("deadline", "PASS", {"deadline_utc": "2026-08-09T23:00:00Z"})
    with pytest.raises(ReceiptRefusal, match="absent"):
        store.persist("controller_ready", "PASS", {"ready": True})
    with pytest.raises(ReceiptRefusal, match="prohibited"):
        ReceiptStore(tmp_path / "sensitive").persist(
            "local_bindings", "PASS", {"token": "must-not-persist"}
        )


class FakeAutoScaling:
    def __init__(self):
        self.groups = {
            "eks-cpu-32cfd795-fa28-d1d9-1b8c-2ed678be1772": {
                "AutoScalingGroupName": "eks-cpu-32cfd795-fa28-d1d9-1b8c-2ed678be1772",
                "MinSize": 0, "DesiredCapacity": 0, "MaxSize": 4, "Instances": [],
            },
            "eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26": {
                "AutoScalingGroupName": "eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26",
                "MinSize": 0, "DesiredCapacity": 0, "MaxSize": 1, "Instances": [],
            },
        }
        self.actions: dict[str, list[dict]] = {name: [] for name in self.groups}

    def describe_auto_scaling_groups(self, AutoScalingGroupNames):
        return {"AutoScalingGroups": [self.groups[name] for name in AutoScalingGroupNames]}

    def describe_scheduled_actions(self, AutoScalingGroupName):
        return {"ScheduledUpdateGroupActions": self.actions[AutoScalingGroupName]}

    def put_scheduled_update_group_action(self, AutoScalingGroupName, ScheduledActionName, StartTime, MinSize, DesiredCapacity, MaxSize):
        self.actions[AutoScalingGroupName] = [{
            "ScheduledActionName": ScheduledActionName, "StartTime": StartTime,
            "MinSize": MinSize, "DesiredCapacity": DesiredCapacity, "MaxSize": MaxSize,
        }]

    def delete_scheduled_action(self, AutoScalingGroupName, ScheduledActionName):
        self.actions[AutoScalingGroupName] = []


class FakeEKS:
    def describe_nodegroup(self, clusterName, nodegroupName):
        maximum = 4 if nodegroupName == "cpu" else 1
        return {"nodegroup": {
            "status": "ACTIVE", "health": {"issues": []},
            "scalingConfig": {"minSize": 0, "maxSize": maximum, "desiredSize": 0},
        }}


def test_deadline_arms_both_groups_at_the_same_time_and_disarms_only_after_zero():
    from scripts.b6_6_deadline import DeadlineControl

    autoscaling = FakeAutoScaling()
    control = DeadlineControl(autoscaling, FakeEKS())
    now = datetime(2026, 8, 9, 20, 0, tzinfo=timezone.utc)
    result = control.arm(now)
    assert result["window_seconds"] == 14400
    starts = {
        actions[0]["StartTime"] for actions in autoscaling.actions.values()
    }
    assert starts == {now + timedelta(seconds=14400)}
    assert control.disarm_after_zero()["deadlines_removed_after_zero"] is True
    assert all(not actions for actions in autoscaling.actions.values())


def test_bindings_require_exact_source_set_and_owner_review(tmp_path):
    from scripts.b6_6_bindings import REQUIRED_SOURCES, BindingRefusal, validate

    root = tmp_path / "repo"
    for relative in REQUIRED_SOURCES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    sources = {
        relative: __import__("hashlib").sha256((root / relative).read_bytes()).hexdigest()
        for relative in REQUIRED_SOURCES
    }
    packet_sha = "a" * 64
    record = {
        "id": "B6-AWS-AUTH-2026-008",
        "status": "owner-approved",
        "packet": {"id": "B6-AWS-CHANGE-PACKET-2026-008", "sha256": packet_sha},
        "independent_review": {"status": "PASS", "reviewer": "independent"},
        "cost": {"registry_id": "COST-REGISTRY-2026-004", "allocation_id": "B6-INTEGRATION-WINDOW-2026-001", "maximum_usd": 10.0},
        "source_bindings": sources,
    }
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(record))
    assert validate(authorization, packet_sha, root)["id"] == "B6-AWS-AUTH-2026-008"
    record["source_bindings"].pop(next(iter(REQUIRED_SOURCES)))
    authorization.write_text(json.dumps(record))
    with pytest.raises(BindingRefusal, match="set differs"):
        validate(authorization, packet_sha, root)


def test_executable_packet_binds_every_source_and_still_requires_approval():
    import hashlib
    from scripts.b6_6_bindings import REQUIRED_SOURCES

    value = PACKET.read_text()
    assert "Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**" in value
    assert "This packet is not authorized by its preparation" in value
    assert "Approve B6 AWS change packet 2026-008 only." in value
    assert "exactly `7 add / 0 change / 0 destroy`" in value
    assert "`B6-INTEGRATION-WINDOW-2026-001` — exactly `$10.00`" in value
    assert "production serving pointer absent" in value
    assert "B5 remains `BLOCKED`" in value
    for relative in REQUIRED_SOURCES:
        expected = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert f"`{relative}`" in value
        assert f"`{expected}`" in value
