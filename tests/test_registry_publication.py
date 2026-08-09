import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.registry_publication import (
    PRODUCTION_PREFIX,
    RegistryPublicationError,
    activate_snapshot,
    plan_activation,
    plan_production_snapshot,
    publish_snapshot,
)


KMS_ARN = (
    "arn:aws:kms:eu-central-1:558069890522:"
    "key/9c336116-c648-4548-95c6-1b926478ae57"
)


def approved_bindings():
    return {
        "gate_report_sha256": "1" * 64,
        "signed_manifest_sha256": "2" * 64,
        "approval_record_sha256": "3" * 64,
        "registry_source_sha256": "4" * 64,
        "generated_registry_tree_sha256": "5" * 64,
        "git_commit": "6" * 40,
        "gate_outcome": "PASS",
        "signature_verified": True,
        "manual_approval_recorded": True,
        "approval_identity": "OWNER-APPROVAL-2026-001",
        "approval_timestamp_utc": "2026-08-04T00:00:00Z",
    }


class ParameterNotFound(Exception):
    pass


class FakeSsm:
    class exceptions:
        ParameterNotFound = ParameterNotFound

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.puts = []
        self.version = 0

    def put_parameter(self, **request):
        name = request["Name"]
        if not request["Overwrite"] and name in self.values:
            raise AssertionError("test publisher attempted an overwrite")
        self.puts.append(request)
        self.values[name] = request["Value"]
        self.version += 1
        return {"Version": self.version}

    def get_parameter(self, Name, WithDecryption):
        assert WithDecryption is True
        if Name not in self.values:
            raise ParameterNotFound(Name)
        return {"Parameter": {"Name": Name, "Value": self.values[Name]}}


def registry():
    return {
        "lingala": {"asr": {"approved_version": None}, "state": "deferred"},
        "oromo": {"asr": {"approved_version": None}, "state": "deferred"},
    }


def test_snapshot_is_deterministic_and_content_addressed():
    first = plan_production_snapshot(registry(), approved_bindings())
    second = plan_production_snapshot(
        dict(reversed(list(registry().items()))), approved_bindings())
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.parameters == second.parameters
    assert first.manifest_name == (
        f"{PRODUCTION_PREFIX}/snapshots/{first.snapshot_sha256}/_manifest")
    assert all(name.startswith(
        f"{PRODUCTION_PREFIX}/snapshots/{first.snapshot_sha256}/")
        for name in first.parameters)


def test_missing_or_blocked_approval_fails_closed():
    bindings = approved_bindings()
    bindings["gate_outcome"] = "BLOCKED"
    with pytest.raises(RegistryPublicationError, match="PASS"):
        plan_production_snapshot(registry(), bindings)
    bindings = approved_bindings()
    bindings["signature_verified"] = False
    with pytest.raises(RegistryPublicationError, match="verified signature"):
        plan_production_snapshot(registry(), bindings)
    bindings = approved_bindings()
    del bindings["gate_report_sha256"]
    with pytest.raises(RegistryPublicationError, match="gate_report_sha256"):
        plan_production_snapshot(registry(), bindings)


def test_immutable_snapshot_reuse_and_collision_rules():
    plan = plan_production_snapshot(registry(), approved_bindings())
    same = plan_production_snapshot(
        registry(), approved_bindings(), dict(plan.parameters))
    assert set(same.actions.values()) == {"REUSE_IDENTICAL"}
    collision = dict(plan.parameters)
    collision[plan.manifest_name] = "tampered"
    with pytest.raises(RegistryPublicationError, match="immutable snapshot collision"):
        plan_production_snapshot(registry(), approved_bindings(), collision)


def test_snapshot_apply_is_securestring_create_only_and_does_not_activate():
    plan = plan_production_snapshot(registry(), approved_bindings())
    client = FakeSsm()
    result = publish_snapshot(client, plan, KMS_ARN, dry_run=False)
    assert result["serving_pointer_changes"] == 0
    assert result["writes_performed"] == len(plan.parameters)
    assert all(request["Type"] == "SecureString" for request in client.puts)
    assert all(request["KeyId"] == KMS_ARN for request in client.puts)
    assert all(request["Overwrite"] is False for request in client.puts)
    assert all(request["Tier"] == "Standard" for request in client.puts)
    assert f"{PRODUCTION_PREFIX}/serving/current" not in client.values


def test_dry_runs_perform_no_writes():
    plan = plan_production_snapshot(registry(), approved_bindings())
    client = FakeSsm()
    result = publish_snapshot(client, plan, KMS_ARN)
    assert result["writes_performed"] == 0
    assert client.puts == []
    activation = plan_activation(plan, approved_bindings(), None)
    result = activate_snapshot(client, activation, KMS_ARN)
    assert result["writes_performed"] == 0
    assert result["production_namespace_changes"] == 0
    assert client.puts == []


def test_activation_is_separate_and_preserves_rollback_value():
    plan = plan_production_snapshot(registry(), approved_bindings())
    old = '{"snapshot_sha256":"old"}\n'
    client = FakeSsm({f"{PRODUCTION_PREFIX}/serving/current": old})
    activation = plan_activation(plan, approved_bindings(), old)
    result = activate_snapshot(client, activation, KMS_ARN, dry_run=False)
    assert result["production_namespace_changes"] == 1
    assert result["rollback_value"] == old
    assert client.puts[0]["Overwrite"] is True
    assert client.puts[0]["Name"] == f"{PRODUCTION_PREFIX}/serving/current"


def test_activation_refuses_a_stale_approved_pointer():
    plan = plan_production_snapshot(registry(), approved_bindings())
    activation = plan_activation(plan, approved_bindings(), "approved-old")
    client = FakeSsm({activation.parameter_name: "changed-after-approval"})
    with pytest.raises(RegistryPublicationError, match="changed after approval"):
        activate_snapshot(client, activation, KMS_ARN, dry_run=False)
    assert client.puts == []


@pytest.mark.parametrize("role", [
    "medzen-orch-role.json",
    "medzen-llm-role.json",
    "medzen-tts-role.json",
])
def test_runtime_registry_roles_are_read_only_and_can_decrypt_only_via_ssm(role):
    policy = json.loads((ROOT / "platform/iam" / role).read_text())
    actions = {
        action
        for statement in policy["Statement"]
        for action in statement["Action"]
    }
    assert "ssm:GetParameter" in actions
    assert "ssm:GetParametersByPath" in actions
    assert "ssm:PutParameter" not in actions
    assert "ssm:DeleteParameter" not in actions
    registry_kms = next(
        statement for statement in policy["Statement"]
        if statement.get("Sid", "").startswith("RegistryKmsDataFor"))
    assert registry_kms["Action"] == ["kms:Decrypt"]
    assert registry_kms["Resource"] == [KMS_ARN]
    assert registry_kms["Condition"]["StringEquals"]["kms:ViaService"] == (
        "ssm.eu-central-1.amazonaws.com")
    assert registry_kms["Condition"]["StringLike"][
        "kms:EncryptionContext:PARAMETER_ARN"] == (
            "arn:aws:ssm:eu-central-1:558069890522:"
            "parameter/medzen/registry/*")


def test_dedicated_publisher_terraform_is_prefix_bound_and_delete_denied():
    source = (ROOT / "infra/ssm.tf").read_text()
    assert 'registry_parameter_prefix = "/medzen/registry"' in source
    assert 'name                 = "medzen-registry-publisher-role"' in source
    assert 'not_resources = [local.registry_parameter_arn]' in source
    assert '"ssm:DeleteParameter", "ssm:DeleteParameters"' in source
    assert 'variable = "kms:ViaService"' in source
    assert 'variable = "kms:EncryptionContext:PARAMETER_ARN"' in source
    assert 'actions   = ["ssm:AddTagsToResource"]' in source
    assert 'actions   = ["ssm:ListTagsForResource"]' in source
    assert 'variable = "aws:TagKeys"' in source


def test_trainer_still_has_no_ssm_capability():
    policy = json.loads((ROOT / "platform/iam/medzen-trainer-role.json").read_text())
    assert all(
        not action.startswith("ssm:")
        for statement in policy["Statement"]
        for action in statement["Action"]
    )
