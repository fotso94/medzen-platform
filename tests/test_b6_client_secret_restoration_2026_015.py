from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_module(
    "b6_015_runner", "scripts/run_b6_client_secret_restoration_2026_015.py"
)
guard = load_module(
    "b6_015_guard", "scripts/check_b6_client_secret_restoration_2026_015_plan.py"
)
proven = guard.proven


def file_sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def exact_secret_after() -> dict:
    return {
        "arn": proven.SECRET_ARN,
        "id": proven.SECRET_ARN,
        "name": "medzen/client-api-keys",
        "kms_key_id": proven.KMS_KEY,
        "recovery_window_in_days": 7,
        "force_overwrite_replica_secret": False,
        "tags": proven.expected_explicit_tags(),
        "tags_all": proven.expected_tags_all(),
    }


def exact_policy_after() -> dict:
    return {
        "secret_arn": proven.SECRET_ARN,
        "block_public_policy": True,
        "policy": json.dumps(proven.expected_resource_policy()),
    }


def exact_kms_after() -> dict:
    return {
        "name": "medzen-orch-b6-client-secret-kms",
        "role": "medzen-orch-role",
        "policy": json.dumps(proven.expected_kms_policy()),
    }


def no_op_plan(include_policies: bool = False) -> dict:
    changes = [
        {
            "address": proven.SECRET,
            "change": {
                "actions": ["no-op"],
                "after": exact_secret_after(),
                "after_unknown": {},
            },
        }
    ]
    if include_policies:
        changes.extend(
            [
                {
                    "address": proven.POLICY,
                    "change": {"actions": ["no-op"], "after": exact_policy_after()},
                },
                {
                    "address": proven.KMS,
                    "change": {"actions": ["no-op"], "after": exact_kms_after()},
                },
            ]
        )
    return {"resource_changes": changes}


def normalization_plan() -> dict:
    plan = no_op_plan()
    change = plan["resource_changes"][0]["change"]
    change["actions"] = ["update"]
    change["before"] = {
        **change["after"],
        "force_overwrite_replica_secret": None,
        "recovery_window_in_days": None,
        "tags": proven.imported_explicit_tags(),
    }
    return plan


def reconcile_plan() -> dict:
    plan = no_op_plan()
    plan["resource_changes"].extend(
        [
            {
                "address": proven.POLICY,
                "change": {"actions": ["create"], "after": exact_policy_after()},
            },
            {
                "address": proven.KMS,
                "change": {"actions": ["create"], "after": exact_kms_after()},
            },
        ]
    )
    return plan


def test_manifest_starts_from_attempt_4_cleanup_and_never_reuses_old_values():
    value = json.loads(
        (
            ROOT / "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-003.json"
        ).read_bytes()
    )
    assert value["status"] == "PROPOSED_NOT_AUTHORIZED"
    assert value["source_cleanup"]["sha256"] == (
        "daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a"
    )
    start = value["required_starting_state"]
    assert start["secret"] == "PENDING_RECOVERABLE_DELETION_EXACT_ARN"
    assert start["current_version_id"] == runner.PRIOR_CURRENT_VERSION
    assert start["older_version_id"] == runner.OLDER_VERSION
    assert value["normalization"]["mode"] == "NORMALIZE_IF_NEEDED_FAIL_CLOSED"
    assert value["new_key_material"]["previous_plaintext_read_or_reused"] is False
    assert value["cost"]["new_reservation_usd"] == 0.0


def test_reviewed_draft_and_historical_restoration_records_remain_immutable():
    assert file_sha(
        "platform/decisions/B6-AWS-CHANGE-PACKET-2026-014-b6-6-private-probe-successor.md"
    ) == "f31cb8f36d76d32884639bbe8bfb750ca807a92847d24f0abf4e1eef7d8c6428"
    assert file_sha(
        "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-002.json"
    ) == "1dc091bf4ee3bcbb93b329839a341c66a787e26e79e4fb2b8de97a34364dc291"
    assert file_sha(
        "platform/evidence/B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json"
    ) == "1d949f019ce0b2e69f1fba525d535d61fc19ed07e99f08d11729c1c099784c89"


def test_normalize_if_needed_accepts_only_exact_update_or_exact_noop():
    assert guard.validate(normalization_plan(), "normalize-if-needed") == (
        "APPLY_EXACT_NORMALIZATION"
    )
    assert guard.validate(no_op_plan(), "normalize-if-needed") == (
        "NO_NORMALIZATION_REQUIRED"
    )
    wrong = no_op_plan()
    wrong["resource_changes"][0]["change"]["after"]["kms_key_id"] = "wrong"
    with pytest.raises(ValueError):
        guard.validate(wrong, "normalize-if-needed")
    extra = normalization_plan()
    extra["resource_changes"].append(
        {"address": "aws_eks_node_group.cpu", "change": {"actions": ["update"]}}
    )
    with pytest.raises(ValueError, match="delta differs"):
        guard.validate(extra, "normalize-if-needed")


def test_reconcile_residual_and_cleanup_reuse_proven_exact_guards():
    assert guard.validate(reconcile_plan(), "reconcile") == (
        "APPLY_EXACT_BOUNDARY_CREATES"
    )
    assert guard.validate(no_op_plan(), "residual-secret") == "NO_CHANGES_SECRET"
    assert guard.validate(no_op_plan(True), "residual-all") == (
        "NO_CHANGES_ALL_BOUNDARIES"
    )
    cleanup = {
        "resource_changes": [
            {
                "address": proven.SECRET,
                "change": {
                    "actions": ["delete"],
                    "before": exact_secret_after(),
                    "after": None,
                },
            }
        ]
    }
    assert guard.validate(cleanup, "cleanup") == "APPLY_EXACT_CLEANUP_SUBSET"


class FakeSecret:
    def __init__(self):
        self.pending = True
        self.policy_present = False
        self.versions = {
            runner.PRIOR_CURRENT_VERSION: ["AWSCURRENT"],
            runner.OLDER_VERSION: [],
        }
        self.restore_calls = 0
        self.delete_calls = 0
        self.new_version = "fresh-version-2026-015"

    def describe_secret(self, **_):
        value = {
            "Name": runner.SECRET_NAME,
            "ARN": runner.SECRET_ARN,
            "KmsKeyId": runner.KMS_KEY,
            "Tags": [
                {"Key": key, "Value": value}
                for key, value in runner.expected_tags().items()
            ],
        }
        if self.pending:
            value["DeletedDate"] = "2026-08-17T03:00:00Z"
        return value

    def list_secret_version_ids(self, **_):
        return {
            "Versions": [
                {"VersionId": key, "VersionStages": list(stages)}
                for key, stages in self.versions.items()
            ]
        }

    def get_resource_policy(self, **_):
        if not self.policy_present:
            raise ClientError(
                {"Error": {"Code": "InvalidRequestException", "Message": "absent"}},
                "GetResourcePolicy",
            )
        return {"ResourcePolicy": json.dumps(runner.expected_resource_policy())}

    def restore_secret(self, **_):
        self.restore_calls += 1
        self.pending = False
        return {"ARN": runner.SECRET_ARN}

    def validate_resource_policy(self, **_):
        return {"PolicyValidationPassed": True}

    def put_secret_value(self, **kwargs):
        value = json.loads(kwargs["SecretString"])
        assert value["clients"][0]["key_sha256"] == runner.sha(b"A" * 43)
        self.versions = {
            self.new_version: ["AWSCURRENT"],
            runner.PRIOR_CURRENT_VERSION: ["AWSPREVIOUS"],
            runner.OLDER_VERSION: [],
        }
        return {"VersionId": self.new_version}

    def update_secret_version_stage(self, **kwargs):
        assert kwargs == {
            "SecretId": runner.SECRET_ARN,
            "VersionStage": "AWSPREVIOUS",
            "RemoveFromVersionId": runner.PRIOR_CURRENT_VERSION,
        }
        self.versions[runner.PRIOR_CURRENT_VERSION] = []

    def get_secret_value(self, **_):
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "expected"}},
            "GetSecretValue",
        )

    def delete_secret(self, **kwargs):
        assert kwargs == {"SecretId": runner.SECRET_ARN, "RecoveryWindowInDays": 7}
        self.delete_calls += 1
        self.pending = True


class FakeIam:
    def __init__(self):
        self.policy_present = False

    def get_role_policy(self, **_):
        if not self.policy_present:
            raise ClientError(
                {"Error": {"Code": "NoSuchEntity", "Message": "absent"}},
                "GetRolePolicy",
            )
        return {"PolicyDocument": runner.expected_kms_policy()}


class FakeEks:
    def describe_nodegroup(self, clusterName, nodegroupName):
        assert clusterName == "medzen-speech"
        maximum = 4 if nodegroupName == "cpu" else 1
        return {
            "nodegroup": {
                "status": "ACTIVE",
                "scalingConfig": {"minSize": 0, "maxSize": maximum, "desiredSize": 0},
                "health": {"issues": []},
            }
        }


class FakeAutoScaling:
    def __init__(self, instances=False):
        self.instances = instances

    def describe_auto_scaling_groups(self, AutoScalingGroupNames):
        assert len(AutoScalingGroupNames) == 2
        values = []
        for name in AutoScalingGroupNames:
            maximum = 4 if "cpu" in name else 1
            values.append(
                {
                    "AutoScalingGroupName": name,
                    "MinSize": 0,
                    "MaxSize": maximum,
                    "DesiredCapacity": 0,
                    "Instances": [{"InstanceId": "i-unexpected"}] if self.instances else [],
                }
            )
        return {"AutoScalingGroups": values}


class FakeSsm:
    def get_parameter(self, **_):
        raise ClientError(
            {"Error": {"Code": "ParameterNotFound", "Message": "expected"}},
            "GetParameter",
        )


class FakeSession:
    def __init__(self, instances=False):
        self.clients = {
            "eks": FakeEks(),
            "autoscaling": FakeAutoScaling(instances),
            "ssm": FakeSsm(),
        }

    def client(self, name):
        return self.clients[name]


def test_zero_boundary_verifier_binds_nodegroups_instances_and_production_pointer():
    assert runner.verify_zero_boundaries(FakeSession()) == runner.ZERO_BOUNDARY
    with pytest.raises(runner.RestorationRefusal, match="zero boundary differs"):
        runner.verify_zero_boundaries(FakeSession(instances=True))


def test_preflight_restore_rotate_verify_receipts_are_ordered_and_plaintext_free(
    tmp_path: Path,
):
    secret = FakeSecret()
    iam = FakeIam()
    authorization = {"id": "B6-AWS-AUTH-2026-015"}
    runner.TOKEN_PATH = tmp_path / "token"
    receipts = tmp_path / "receipts"

    runner.preflight(
        secret,
        iam,
        {"Account": runner.ACCOUNT, "Arn": runner.OPERATOR},
        set(),
        dict(runner.ZERO_BOUNDARY),
        receipts / "preflight.json",
        authorization,
    )
    with pytest.raises(runner.RestorationRefusal, match="expired"):
        runner.preflight(
            FakeSecret(),
            FakeIam(),
            {"Account": runner.ACCOUNT, "Arn": runner.OPERATOR},
            set(),
            dict(runner.ZERO_BOUNDARY),
            tmp_path / "expired.json",
            authorization,
            current_time=datetime(2026, 8, 17, 4, 0, tzinfo=timezone.utc),
        )
    runner.restore(secret, receipts / "restore.json", authorization)
    assert secret.restore_calls == 1
    assert secret.pending is False

    runner.record_terraform(
        "terraform_import",
        {
            "state_lineage": "lineage",
            "state_serial": 40,
            "address": "aws_secretsmanager_secret.b6_client_keys[0]",
            "secret_arn": runner.SECRET_ARN,
        },
        receipts / "terraform_import.json",
        authorization,
    )
    runner.record_terraform(
        "terraform_normalization",
        {
            "mode": "NO_NORMALIZATION_REQUIRED",
            "plan_sha256": "a" * 64,
            "residual_plan_sha256": "b" * 64,
            "state_lineage": "lineage",
            "state_serial": 40,
        },
        receipts / "terraform_normalization.json",
        authorization,
    )
    runner.record_terraform(
        "terraform_reconciliation",
        {
            "plan_sha256": "c" * 64,
            "residual_plan_sha256": "d" * 64,
            "state_lineage": "lineage",
            "state_serial": 41,
            "resource_policy_sha256": "e" * 64,
            "kms_policy_sha256": "f" * 64,
        },
        receipts / "terraform_reconciliation.json",
        authorization,
    )

    secret.policy_present = True
    iam.policy_present = True
    rotation = runner.rotate(
        secret,
        iam,
        receipts / "rotation.json",
        authorization,
        lambda _: "A" * 43,
    )
    assert rotation["bearer_token_sha256"] != (
        "3a30b00fc96111490c2b471eec5eebe1c9d26bf991508428cf2f5511e306b84a"
    )
    assert stat.S_IMODE(runner.TOKEN_PATH.stat().st_mode) == 0o600
    result = runner.verify(
        secret,
        receipts / "rotation.json",
        receipts / "verification.json",
        authorization,
    )
    assert result["new_version_id"] == secret.new_version
    assert secret.versions == {
        secret.new_version: ["AWSCURRENT"],
        runner.PRIOR_CURRENT_VERSION: [],
        runner.OLDER_VERSION: [],
    }
    combined = "".join(path.read_text() for path in sorted(receipts.glob("*.json")))
    assert "A" * 43 not in combined
    assert '"plaintext_recorded":false' in combined


def test_failure_cleanup_removes_token_and_reschedules_recoverable_deletion(tmp_path):
    secret = FakeSecret()
    secret.pending = False
    iam = FakeIam()
    authorization = {"id": "B6-AWS-AUTH-2026-015"}
    runner.TOKEN_PATH = tmp_path / "token"
    runner.TOKEN_PATH.write_text("temporary")
    result = runner.cleanup(
        secret,
        iam,
        set(),
        tmp_path / "cleanup.json",
        authorization,
    )
    assert result["status"] == "PASS_RECOVERABLE_ZERO_STATE"
    assert secret.delete_calls == 1
    assert secret.pending is True
    assert not runner.TOKEN_PATH.exists()


def test_binding_validator_requires_packet_manifest_review_cost_and_every_source(tmp_path):
    from scripts.b6_client_secret_restoration_2026_015_bindings import (
        REQUIRED_SOURCES,
        BindingRefusal,
        validate,
    )

    root = tmp_path / "repo"
    for relative in REQUIRED_SOURCES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    packet_path = root / "platform/decisions/packet-015.md"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text("packet")
    packet_sha = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    manifest_path = root / "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-003.json"
    record = {
        "id": "B6-AWS-AUTH-2026-015",
        "status": "owner-approved",
        "packet": {
            "id": "B6-AWS-CHANGE-PACKET-2026-015",
            "path": "platform/decisions/packet-015.md",
            "sha256": packet_sha,
        },
        "request_manifest": {
            "path": "platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-003.json",
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
        "independent_review": {
            "status": "PASS",
            "reviewer": "independent",
            "reviewed_packet_sha256": packet_sha,
        },
        "cost": {
            "registry_id": "COST-REGISTRY-2026-004",
            "allocation_id": "B6-INTEGRATION-WINDOW-2026-001",
            "maximum_incremental_usd": 0.1,
            "new_reservation_usd": 0.0,
        },
        "source_bindings": {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in REQUIRED_SOURCES
        },
    }
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps(record))
    assert validate(authorization, root)["id"] == "B6-AWS-AUTH-2026-015"
    record["source_bindings"].pop(next(iter(REQUIRED_SOURCES)))
    authorization.write_text(json.dumps(record))
    with pytest.raises(BindingRefusal, match="set differs"):
        validate(authorization, root)


def test_execution_script_is_staged_no_compute_and_has_fail_closed_cleanup():
    path = ROOT / "scripts/run_b6_client_secret_restoration_2026_015.sh"
    subprocess.run(["bash", "-n", str(path)], check=True)
    source = path.read_text()
    assert source.index("2026_015.py preflight") < source.index("2026_015.py restore")
    assert source.index("2026_015.py restore") < source.index("terraform_medzen.sh import")
    assert source.index("normalize-if-needed") < source.index("--mode reconcile")
    assert source.index("record-terraform-reconciliation") < source.index(
        "2026_015.py rotate"
    ) < source.index("2026_015.py verify")
    assert "trap cleanup_after_refusal EXIT INT TERM" in source
    assert "RecoveryWindowInDays=7" not in source
    assert "enable_b6_client_keys=false" in source
    assert "desiredSize=1" not in source
    assert "desiredSize=2" not in source
    assert "kubectl" not in source


def test_runner_refuses_without_explicit_apply():
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_b6_client_secret_restoration_2026_015.py"),
            "preflight",
            "--authorization",
            "/does/not/exist",
            "--receipts-dir",
            "/private/tmp/unused-b6-015",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 2
    assert "--apply is required" in process.stderr


def test_packet_and_local_evidence_are_hash_bound_and_non_authorizing():
    from scripts.b6_client_secret_restoration_2026_015_bindings import REQUIRED_SOURCES

    packet_path = (
        ROOT
        / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-015-synthetic-credential-restoration.md"
    )
    packet = packet_path.read_text()
    assert "Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**" in packet
    assert "This draft itself authorizes no AWS or Terraform mutation" in packet
    assert "Approve B6 AWS change packet 2026-015 only." in packet
    assert "exactly `0 add / 1 update / 0 destroy`" in packet
    assert "exact delta is `2 add / 0 change / 0 destroy`" in packet
    for relative in REQUIRED_SOURCES:
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert f"| `{relative}` | `{digest}` |" in packet

    evidence = json.loads(
        (
            ROOT
            / "platform/evidence/B6-CREDENTIAL-RESTORATION-2026-015-LOCAL-PREPARATION.json"
        ).read_bytes()
    )
    assert evidence["packet"]["authorized"] is False
    assert evidence["packet"]["executed"] is False
    assert evidence["packet"]["owner_authorization_record_created"] is False
    assert hashlib.sha256(packet_path.read_bytes()).hexdigest() == evidence["packet"]["sha256"]
    assert all(value == 0 for value in evidence["explicit_non_events"].values())
