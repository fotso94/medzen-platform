#!/usr/bin/env python3
"""Run packet-2026-023 Stage A isolated Fargate qualification."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import (
    STAGE_A_EXECUTION_STAGES,
    ReceiptStore,
)
from scripts.b6_6_deadline import GROUPS
from scripts.b6_6_fargate_probe import CLUSTER, run_isolated_probe
from scripts.b6_6_probe_endpoints import verify_absent, wait_available
from scripts.check_b6_6_window_plan import QUALIFICATION_ADDRESSES, changes, load


ACCOUNT = "558069890522"
PROFILE = "medzen"
REGION = "eu-central-1"
MAXIMUM_SECONDS = 1800
OPERATION_SECONDS = 1200
CLEANUP_SECONDS = 600
MAXIMUM_COST_USD = 0.50
STABLE_PROBE_PASSES = 3
RECEIPTS = ROOT / "platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE"
PLAN = Path("/private/tmp/b6-023-stage-a.tfplan")
CLEANUP_PLAN = Path("/private/tmp/b6-023-stage-a-cleanup.tfplan")
TARGETS = tuple(f"-target={address.removesuffix('[0]')}" for address in sorted(QUALIFICATION_ADDRESSES))
REASONS = {
    "stage_a_preflight": "STAGE_A_PREFLIGHT_REFUSED",
    "stage_a_terraform": "STAGE_A_TERRAFORM_REFUSED",
    "stage_a_endpoints": "STAGE_A_ENDPOINTS_REFUSED",
    "stage_a_probe_1": "STAGE_A_PROBE_1_REFUSED",
    "stage_a_probe_2": "STAGE_A_PROBE_2_REFUSED",
    "stage_a_probe_3": "STAGE_A_PROBE_3_REFUSED",
    "stage_a_cleanup": "STAGE_A_CLEANUP_REFUSED",
}


class StageARefusal(RuntimeError):
    def __init__(self, reason_code: str, payload: dict[str, Any] | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.payload = payload or {}


@dataclass(frozen=True)
class StageAContext:
    authorization: Path
    packet_sha256: str
    receipts_dir: Path


@dataclass(frozen=True)
class StageAResult:
    outcome: str
    failure_stage: str | None
    receipt_hashes: dict[str, str]
    cleanup_complete: bool


class StageAOperations(Protocol):
    def before_run(self, context: StageAContext) -> None: ...
    def execute(self, stage: str, context: StageAContext) -> dict[str, Any]: ...
    def recover_cleanup(self, context: StageAContext) -> dict[str, Any]: ...


class RealStageAOperations:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.terraform_applied = False
        self.session: Any | None = None

    def _remaining(self) -> int:
        remaining = int(OPERATION_SECONDS - (time.monotonic() - self.started))
        if remaining < 1:
            raise StageARefusal("STAGE_A_DEADLINE_EXPIRED")
        return remaining

    @staticmethod
    def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "AWS_PROFILE": PROFILE},
        )

    def _clients(self) -> tuple[Any, Any, Any, Any, Any, Any]:
        if self.session is None:
            import boto3

            self.session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        return tuple(
            self.session.client(name)
            for name in ("sts", "ec2", "ecs", "eks", "autoscaling", "iam")
        )  # type: ignore[return-value]

    @staticmethod
    def _assert_workers_zero(eks: Any, autoscaling: Any) -> None:
        for name, binding in GROUPS.items():
            nodegroup = eks.describe_nodegroup(
                clusterName="medzen-speech", nodegroupName=binding["nodegroup"]
            )["nodegroup"]
            if (
                nodegroup.get("status") != "ACTIVE"
                or nodegroup.get("health", {}).get("issues")
                or nodegroup.get("scalingConfig", {}).get("minSize") != 0
                or nodegroup.get("scalingConfig", {}).get("maxSize")
                != binding["maximum"]
                or nodegroup.get("scalingConfig", {}).get("desiredSize") != 0
            ):
                raise StageARefusal(f"STAGE_A_{name.upper()}_NODEGROUP_NOT_ZERO")
            groups = autoscaling.describe_auto_scaling_groups(
                AutoScalingGroupNames=[binding["asg"]]
            ).get("AutoScalingGroups", [])
            if (
                len(groups) != 1
                or groups[0].get("DesiredCapacity") != 0
                or groups[0].get("Instances")
            ):
                raise StageARefusal(f"STAGE_A_{name.upper()}_ASG_NOT_ZERO")

    def before_run(self, context: StageAContext) -> None:
        from scripts.b6_6_bindings import validate

        if context.receipts_dir != RECEIPTS or context.receipts_dir.exists():
            raise StageARefusal("STAGE_A_RECEIPT_DIRECTORY_DIFFERS")
        authorization = validate(context.authorization, context.packet_sha256, ROOT)
        head = self._run(["git", "rev-parse", "HEAD"], 30).stdout.strip()
        dirty = self._run(["git", "status", "--porcelain=v1"], 30).stdout
        reviewed = authorization["prepared_repository_commit"]
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", reviewed, head],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if ancestry.returncode != 0 or dirty:
            raise StageARefusal("STAGE_A_REVIEWED_CLEAN_COMMIT_REQUIRED")
        if os.environ.get("AWS_PROFILE") != PROFILE:
            raise StageARefusal("STAGE_A_AWS_PROFILE_DIFFERS")

    def _preflight(self) -> dict[str, Any]:
        sts, ec2, ecs, eks, autoscaling, iam = self._clients()
        if sts.get_caller_identity().get("Account") != ACCOUNT:
            raise StageARefusal("STAGE_A_AWS_ACCOUNT_DIFFERS")
        self._assert_workers_zero(eks, autoscaling)
        verify_absent(ec2)
        active = ecs.describe_clusters(clusters=[CLUSTER]).get("clusters", [])
        if any(item.get("status") == "ACTIVE" for item in active):
            raise StageARefusal("STAGE_A_PROBE_CLUSTER_ALREADY_ACTIVE")
        try:
            iam.get_role(RoleName="medzen-b6-window-probe-execution")
        except iam.exceptions.NoSuchEntityException:
            pass
        else:
            raise StageARefusal("STAGE_A_PROBE_ROLE_ALREADY_EXISTS")
        return {
            "aws_account": ACCOUNT,
            "cpu_desired": 0,
            "gpu_desired": 0,
            "eks_worker_mutations": 0,
            "probe_vpc_endpoints": 0,
            "probe_iam_roles": 0,
            "maximum_seconds": MAXIMUM_SECONDS,
            "maximum_cost_usd": MAXIMUM_COST_USD,
        }

    def _terraform(self) -> dict[str, Any]:
        command = [
            "scripts/terraform_medzen.sh",
            "plan",
            "-input=false",
            f"-out={PLAN}",
            f"-var=account_id={ACCOUNT}",
            "-var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso",
            "-var=enable_b6_load_balancer_controller=false",
            "-var=enable_b6_integration_window=false",
            "-var=enable_b6_probe_qualification=true",
            *TARGETS,
        ]
        self._run(command, min(self._remaining(), 600))
        self._run(
            [sys.executable, "scripts/check_b6_6_window_plan.py", "qualification", str(PLAN)],
            min(self._remaining(), 120),
        )
        plan = load(PLAN)
        delta = changes(plan)
        names = sorted(delta)
        if delta != {address: ["create"] for address in QUALIFICATION_ADDRESSES}:
            raise StageARefusal("STAGE_A_TERRAFORM_DELTA_DIFFERS")
        self._run(
            ["scripts/terraform_medzen.sh", "apply", "-input=false", "-auto-approve", str(PLAN)],
            min(self._remaining(), 600),
        )
        self.terraform_applied = True
        return {
            "adds": 11,
            "changes": 0,
            "destroys": 0,
            "resource_names": names,
            "eks_resources": 0,
            "alb_resources": 0,
            "service_resources": 0,
        }

    def _endpoints(self) -> dict[str, Any]:
        _, ec2, _, _, _, _ = self._clients()
        value = wait_available(ec2, min(self._remaining(), 900))
        return {
            "interface_endpoint_count": value["interface_endpoint_count"],
            "gateway_endpoint_count": value["gateway_endpoint_count"],
            "endpoint_ingress_source_mode": value["endpoint_ingress_source_mode"],
            "private_dns_interface_endpoints": value["private_dns_interface_endpoints"],
            "endpoint_egress_rule_count": value["endpoint_egress_rule_count"],
            "ecr_egress_destination": value["ecr_egress_destination"],
            "s3_egress_prefix_list_id": value["s3_egress_prefix_list_id"],
            "dns_security_group_filtering": value["dns_security_group_filtering"],
        }

    def _probe(self, ordinal: int) -> dict[str, Any]:
        _, ec2, ecs, _, _, _ = self._clients()
        value = run_isolated_probe(ecs, ec2, min(self._remaining(), 300))
        if value.get("status") != "PASS":
            safe = {
                key: value[key]
                for key in (
                    "reason_code",
                    "application_started",
                    "image_pull_proven",
                    "assign_public_ip",
                    "private_endpoint_count",
                    "probe_task_security_group_count",
                )
                if key in value
            }
            raise StageARefusal(f"STAGE_A_PROBE_{ordinal}_DID_NOT_PASS", safe)
        return {
            "ordinal": ordinal,
            "reason_code": value["reason_code"],
            "task_arn_sha256": value["task_arn_sha256"],
            "container_exit_code": value["container_exit_code"],
            "application_started": value["application_started"],
            "image_pull_proven": value["image_pull_proven"],
            "assign_public_ip": value["assign_public_ip"],
            "private_endpoint_count": value["private_endpoint_count"],
            "probe_task_security_group_count": value["probe_task_security_group_count"],
            "qualification_mode": value["qualification_mode"],
        }

    def _cleanup(self) -> dict[str, Any]:
        cleanup_deadline = time.monotonic() + CLEANUP_SECONDS

        def cleanup_remaining(limit: int) -> int:
            remaining = int(cleanup_deadline - time.monotonic())
            if remaining < 1:
                raise StageARefusal("STAGE_A_CLEANUP_DEADLINE_EXPIRED")
            return min(remaining, limit)

        _, ec2, ecs, eks, autoscaling, iam = self._clients()
        try:
            tasks = ecs.list_tasks(cluster=CLUSTER).get("taskArns", [])
        except ecs.exceptions.ClusterNotFoundException:
            tasks = []
        for task in tasks:
            ecs.stop_task(cluster=CLUSTER, task=task, reason="B6_STAGE_A_CLEANUP")

        command = [
            "scripts/terraform_medzen.sh",
            "plan",
            "-input=false",
            f"-out={CLEANUP_PLAN}",
            f"-var=account_id={ACCOUNT}",
            "-var=registry_publisher_principal_arn=arn:aws:iam::558069890522:user/s.fotso",
            "-var=enable_b6_load_balancer_controller=false",
            "-var=enable_b6_integration_window=false",
            "-var=enable_b6_probe_qualification=false",
            *TARGETS,
        ]
        self._run(command, cleanup_remaining(300))
        plan = load(CLEANUP_PLAN)
        delta = changes(plan)
        if delta:
            mode = (
                "qualification-destroy"
                if set(delta) == QUALIFICATION_ADDRESSES
                else "qualification-cleanup"
            )
            self._run(
                [sys.executable, "scripts/check_b6_6_window_plan.py", mode, str(CLEANUP_PLAN)],
                cleanup_remaining(60),
            )
            self._run(
                [
                    "scripts/terraform_medzen.sh",
                    "apply",
                    "-input=false",
                    "-auto-approve",
                    str(CLEANUP_PLAN),
                ],
                cleanup_remaining(300),
            )
        verify_absent(ec2)
        self._assert_workers_zero(eks, autoscaling)
        try:
            iam.get_role(RoleName="medzen-b6-window-probe-execution")
        except iam.exceptions.NoSuchEntityException:
            pass
        else:
            raise StageARefusal("STAGE_A_PROBE_ROLE_REMAINS")
        clusters = ecs.describe_clusters(clusters=[CLUSTER]).get("clusters", [])
        if any(item.get("status") == "ACTIVE" for item in clusters):
            raise StageARefusal("STAGE_A_PROBE_CLUSTER_REMAINS_ACTIVE")
        return {
            "cleanup_complete": True,
            "resource_names_removed": sorted(delta),
            "window_terraform_resources": 0,
            "probe_vpc_endpoints": 0,
            "probe_endpoint_security_groups": 0,
            "probe_iam_roles": 0,
            "active_probe_ecs_clusters": 0,
            "cpu_desired": 0,
            "gpu_desired": 0,
            "eks_worker_mutations": 0,
        }

    def execute(self, stage: str, context: StageAContext) -> dict[str, Any]:
        del context
        if stage != "stage_a_cleanup":
            self._remaining()
        if stage == "stage_a_preflight":
            return self._preflight()
        if stage == "stage_a_terraform":
            return self._terraform()
        if stage == "stage_a_endpoints":
            return self._endpoints()
        if stage.startswith("stage_a_probe_"):
            return self._probe(int(stage.rsplit("_", 1)[1]))
        if stage == "stage_a_cleanup":
            return self._cleanup()
        raise StageARefusal("UNKNOWN_STAGE_A_STAGE")

    def recover_cleanup(self, context: StageAContext) -> dict[str, Any]:
        del context
        value = self._cleanup()
        return {
            "recovery_completed": value.get("cleanup_complete") is True,
            "zero_state": value.get("cleanup_complete") is True,
            "window_terraform_resources": value.get("window_terraform_resources"),
            "probe_vpc_endpoints": value.get("probe_vpc_endpoints"),
            "probe_iam_roles": value.get("probe_iam_roles"),
            "active_probe_ecs_clusters": value.get("active_probe_ecs_clusters"),
            "cpu_desired": value.get("cpu_desired"),
            "gpu_desired": value.get("gpu_desired"),
        }


class StageARunner:
    def __init__(self, operations: StageAOperations, store: ReceiptStore):
        self.operations = operations
        self.store = store
        self.failure_stage: str | None = None
        self.previous: dict[str, str] = {}

    def _persist(self, stage: str, status: str, payload: dict[str, Any]) -> None:
        receipt = self.store.persist(
            stage,
            status,
            payload,
            dependencies=dict(self.previous),
        )
        self.previous[stage] = receipt["receipt_sha256"]

    def _run_stage(self, stage: str, context: StageAContext) -> bool:
        status = "REFUSED"
        payload: dict[str, Any] = {"reason_code": REASONS[stage]}
        try:
            payload = self.operations.execute(stage, context)
            status = "PASS"
        except StageARefusal as exc:
            payload = {"reason_code": exc.reason_code, **exc.payload}
            self.failure_stage = self.failure_stage or stage
        except Exception as exc:
            payload = {
                "reason_code": REASONS[stage],
                "exception_class": type(exc).__name__,
            }
            self.failure_stage = self.failure_stage or stage
        finally:
            if stage == "stage_a_cleanup" and status == "REFUSED":
                try:
                    recovery = self.operations.recover_cleanup(context)
                except Exception as exc:
                    recovery = {
                        "recovery_completed": False,
                        "zero_state": False,
                        "recovery_exception_class": type(exc).__name__,
                    }
                payload = {**payload, "cleanup_recovery": recovery}
            self._persist(stage, status, payload)
        return status == "PASS"

    def run(self, context: StageAContext) -> StageAResult:
        cleanup_complete = False
        try:
            self.operations.before_run(context)
            for stage in STAGE_A_EXECUTION_STAGES:
                if not self._run_stage(stage, context):
                    break
        except Exception as exc:
            self.failure_stage = self.failure_stage or "stage_a_preflight"
            if not self.store.path("stage_a_preflight").exists():
                self._persist(
                    "stage_a_preflight",
                    "REFUSED",
                    {
                        "reason_code": "STAGE_A_TOP_LEVEL_REFUSED",
                        "exception_class": type(exc).__name__,
                    },
                )
        finally:
            cleanup_passed = self._run_stage("stage_a_cleanup", context)
            cleanup_receipt = self.store.load("stage_a_cleanup")
            recovery = cleanup_receipt.get("payload", {}).get("cleanup_recovery", {})
            cleanup_complete = cleanup_passed or (
                recovery.get("recovery_completed") is True
                and recovery.get("zero_state") is True
            )

        passed_probes = sum(
            self.store.path(f"stage_a_probe_{ordinal}").exists()
            and self.store.load(f"stage_a_probe_{ordinal}")["status"] == "PASS"
            for ordinal in range(1, STABLE_PROBE_PASSES + 1)
        )
        outcome = (
            "PASS"
            if self.failure_stage is None
            and cleanup_complete
            and passed_probes == STABLE_PROBE_PASSES
            else "REFUSED"
        )
        aggregate = {
            "packet_sha256": context.packet_sha256,
            "stable_probe_passes": passed_probes,
            "required_consecutive_probe_passes": STABLE_PROBE_PASSES,
            "cleanup_complete": cleanup_complete,
            "eks_worker_mutations": 0,
            "maximum_seconds": MAXIMUM_SECONDS,
            "maximum_cost_usd": MAXIMUM_COST_USD,
            "window_attempts_unlocked": outcome == "PASS",
            "failure_stage": self.failure_stage,
        }
        self._persist("stage_a", outcome, aggregate)
        return StageAResult(
            outcome=outcome,
            failure_stage=self.failure_stage,
            receipt_hashes=self.store.hashes(),
            cleanup_complete=cleanup_complete,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--receipts-dir", type=Path, default=RECEIPTS)
    args = parser.parse_args()
    result = StageARunner(
        RealStageAOperations(), ReceiptStore(args.receipts_dir)
    ).run(
        StageAContext(
            authorization=args.authorization,
            packet_sha256=args.packet_sha256,
            receipts_dir=args.receipts_dir,
        )
    )
    print(
        json.dumps(
            {
                "outcome": result.outcome,
                "failure_stage": result.failure_stage,
                "cleanup_complete": result.cleanup_complete,
                "receipt_hashes": result.receipt_hashes,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if result.outcome == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
