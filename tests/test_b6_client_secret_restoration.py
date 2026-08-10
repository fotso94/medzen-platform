from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load_module("b6_secret_restoration_guard", "scripts/check_b6_client_secret_restoration_plan.py")
runner = load_module("b6_secret_restoration_runner", "scripts/run_b6_client_secret_restoration.py")


def good_plan() -> dict:
    return {
        "resource_changes": [
            {
                "address": guard.SECRET,
                "change": {
                    "actions": ["no-op"],
                    "after": {
                        "arn": guard.SECRET_ARN,
                        "name": "medzen/client-api-keys",
                        "kms_key_id": guard.KMS_KEY,
                        "recovery_window_in_days": 7,
                        "tags": {
                            "Project": "medzen-speech",
                            "Environment": "dev",
                            "CostCenter": "speech-platform",
                            "Stage": "B6.6",
                            "Workstream": "integration-window-auth",
                            "BudgetRegistry": "COST-REGISTRY-2026-003",
                            "Classification": "SYNTHETIC_TEST_ONLY",
                        },
                    },
                },
            },
            {
                "address": guard.POLICY,
                "change": {
                    "actions": ["create"],
                    "after": {
                        "secret_arn": guard.SECRET_ARN,
                        "block_public_policy": True,
                        "policy": json.dumps(guard.expected_resource_policy()),
                    },
                },
            },
            {
                "address": guard.KMS,
                "change": {
                    "actions": ["create"],
                    "after": {
                        "name": "medzen-orch-b6-client-secret-kms",
                        "role": "medzen-orch-role",
                        "policy": json.dumps(guard.expected_kms_policy()),
                    },
                },
            },
        ]
    }


def test_restoration_manifest_is_exact_and_non_authorizing() -> None:
    manifest = json.loads((ROOT / "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-001.json").read_text())
    assert manifest["status"] == "PROPOSED_NOT_AUTHORIZED"
    assert manifest["aws"]["secret_arn"] == runner.SECRET_ARN
    assert manifest["recovery_precondition"]["state"] == "PENDING_RECOVERABLE_DELETION"
    assert manifest["historical_version"]["must_not_be_reused"] is True
    assert manifest["terraform_reconciliation"]["expected_plan"]["adds"] == 2
    assert manifest["new_key_material"]["random_bytes"] == 32
    assert manifest["new_key_material"]["bearer_characters"] == 43
    assert manifest["receipt_order"] == [
        "restore",
        "terraform_reconciliation",
        "rotation",
        "verification",
    ]
    assert manifest["failure_cleanup"]["force_delete_without_recovery"] is False


def test_restoration_plan_guard_accepts_only_import_plus_two_creates() -> None:
    guard.validate(good_plan())

    extra = good_plan()
    extra["resource_changes"].append(
        {"address": "aws_eks_node_group.cpu", "change": {"actions": ["update"], "after": {}}}
    )
    try:
        guard.validate(extra)
    except ValueError:
        pass
    else:
        raise AssertionError("extra Terraform action was accepted")

    secret_create = good_plan()
    secret_create["resource_changes"][0]["change"]["actions"] = ["create"]
    try:
        guard.validate(secret_create)
    except ValueError:
        pass
    else:
        raise AssertionError("secret recreation was accepted instead of exact import")


def test_restoration_plan_guard_refuses_policy_drift_and_plaintext() -> None:
    wrong_reader = good_plan()
    policy = json.loads(wrong_reader["resource_changes"][1]["change"]["after"]["policy"])
    policy["Statement"][0]["Principal"]["AWS"] = "*"
    wrong_reader["resource_changes"][1]["change"]["after"]["policy"] = json.dumps(policy)
    try:
        guard.validate(wrong_reader)
    except ValueError:
        pass
    else:
        raise AssertionError("resource-policy reader drift was accepted")

    plaintext = good_plan()
    plaintext["resource_changes"][0]["change"]["after"]["secret_string"] = "forbidden"
    try:
        guard.validate(plaintext)
    except ValueError:
        pass
    else:
        raise AssertionError("secret plaintext in Terraform was accepted")


def test_restoration_cleanup_guard_accepts_only_exact_subset_deletes() -> None:
    original = good_plan()["resource_changes"]
    cleanup_resources = []
    for resource in original:
        after = resource["change"]["after"]
        cleanup_resources.append(
            {
                "address": resource["address"],
                "change": {"actions": ["delete"], "before": after, "after": None},
            }
        )
    guard.validate({"resource_changes": cleanup_resources}, "cleanup")
    guard.validate({"resource_changes": cleanup_resources[:1]}, "cleanup")

    cleanup_resources.append(
        {
            "address": "aws_eks_node_group.cpu",
            "change": {"actions": ["delete"], "before": {}, "after": None},
        }
    )
    try:
        guard.validate({"resource_changes": cleanup_resources}, "cleanup")
    except ValueError:
        pass
    else:
        raise AssertionError("an unrelated cleanup delete was accepted")


class FakeSecretClient:
    def __init__(self) -> None:
        self.pending = True
        self.versions = {runner.OLD_VERSION: ["AWSCURRENT"]}
        self.new_version = "new-version-id"

    def describe_secret(self, **_: object) -> dict:
        value = {
            "Name": runner.SECRET_NAME,
            "ARN": runner.SECRET_ARN,
            "KmsKeyId": runner.KMS_KEY,
            "Tags": [{"Key": key, "Value": value} for key, value in runner.expected_tags().items()],
            "VersionIdsToStages": {key: list(value) for key, value in self.versions.items()},
        }
        if self.pending:
            value["DeletedDate"] = "pending"
        return value

    def restore_secret(self, **_: object) -> dict:
        self.pending = False
        return {"ARN": runner.SECRET_ARN, "Name": runner.SECRET_NAME}

    def get_resource_policy(self, **_: object) -> dict:
        return {"ResourcePolicy": json.dumps(runner.expected_resource_policy())}

    def validate_resource_policy(self, **_: object) -> dict:
        return {"PolicyValidationPassed": True}

    def put_secret_value(self, **kwargs: object) -> dict:
        value = json.loads(str(kwargs["SecretString"]))
        assert value["clients"][0]["key_sha256"] == runner.sha(b"A" * 43)
        self.versions = {runner.OLD_VERSION: ["AWSPREVIOUS"], self.new_version: ["AWSCURRENT"]}
        return {"VersionId": self.new_version}

    def list_secret_version_ids(self, **_: object) -> dict:
        return {
            "Versions": [
                {"VersionId": version, "VersionStages": list(stages)}
                for version, stages in self.versions.items()
            ]
        }

    def update_secret_version_stage(self, **kwargs: object) -> None:
        assert kwargs["VersionStage"] == "AWSPREVIOUS"
        assert kwargs["RemoveFromVersionId"] == runner.OLD_VERSION
        self.versions[runner.OLD_VERSION] = []

    def get_secret_value(self, **_: object) -> dict:
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "expected"}},
            "GetSecretValue",
        )


class FakeIamClient:
    def get_role_policy(self, **_: object) -> dict:
        return {
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DescribeExistingB6ClientKeyKmsKey",
                        "Effect": "Allow",
                        "Action": "kms:DescribeKey",
                        "Resource": runner.KMS_KEY,
                    },
                    {
                        "Sid": "DecryptOnlyB6ClientKeyViaSecretsManager",
                        "Effect": "Allow",
                        "Action": "kms:Decrypt",
                        "Resource": runner.KMS_KEY,
                        "Condition": {
                            "StringEquals": {
                                "kms:ViaService": "secretsmanager.eu-central-1.amazonaws.com"
                            },
                            "StringLike": {
                                "kms:EncryptionContext:SecretARN": "arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys*"
                            },
                        },
                    },
                ],
            }
        }


def test_receipts_are_durable_per_stage_and_contain_no_plaintext(tmp_path: Path) -> None:
    secret = FakeSecretClient()
    authorization = {"id": "B6-AWS-AUTH-2026-012"}
    runner.TOKEN_PATH = tmp_path / "token"

    restore_receipt = tmp_path / "receipts" / "restore.json"
    restored = runner.restore(secret, restore_receipt, authorization, {})
    assert restored["status"] == "PASS_RESTORED_AWAITING_BOUNDARY_RECONSTRUCTION"
    assert restore_receipt.is_file()

    rotation_receipt = tmp_path / "receipts" / "rotation.json"
    rotated = runner.rotate(secret, FakeIamClient(), rotation_receipt, authorization, lambda _: "A" * 43)
    assert rotated["status"] == "PASS_ROTATED_AWAITING_VERIFICATION"
    assert rotation_receipt.is_file()
    assert stat.S_IMODE(runner.TOKEN_PATH.stat().st_mode) == 0o600
    assert runner.TOKEN_PATH.stat().st_size == 44

    verification_receipt = tmp_path / "receipts" / "verification.json"
    verified = runner.verify(secret, rotation_receipt, verification_receipt, authorization)
    assert verified["status"] == "VERIFIED_COMPLETE"
    assert verification_receipt.is_file()
    assert secret.versions[runner.OLD_VERSION] == []

    combined = restore_receipt.read_text() + rotation_receipt.read_text() + verification_receipt.read_text()
    assert "A" * 43 not in combined
    assert "plaintext_recorded\":false" in combined


def test_restoration_runner_refuses_without_apply() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_b6_client_secret_restoration.py"),
            "restore",
            "--authorization",
            "/does/not/exist",
            "--receipts-dir",
            "/private/tmp/unused-b6-restoration",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--apply is required" in result.stderr


def test_rotation_requires_durable_restore_and_terraform_receipts(tmp_path: Path) -> None:
    restore = tmp_path / "restore.json"
    terraform = tmp_path / "terraform_reconciliation.json"
    runner.persist(restore, {"status": "PASS_RESTORED_AWAITING_BOUNDARY_RECONSTRUCTION"})
    assert runner.require_receipt(
        restore, "PASS_RESTORED_AWAITING_BOUNDARY_RECONSTRUCTION"
    )["status"].startswith("PASS_")
    try:
        runner.require_receipt(terraform, "PASS_TERRAFORM_RECONCILED")
    except runner.RestorationRefusal:
        pass
    else:
        raise AssertionError("rotation prerequisite accepted a missing Terraform receipt")
