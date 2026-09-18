#!/usr/bin/env python3
"""Run the entire consolidated B6.6 runner against a faked platform layer."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import (
    STAGE_A_EXECUTION_STAGES,
    STAGE_A_STAGES,
    WINDOW_STAGES,
    ReceiptStore,
    canonical_json,
    sha256_file,
)
from scripts.b6_6_credential import KMS_KEY, SECRET_ARN, SECRET_NAME, rotate_and_verify
from scripts.b6_6_bindings import COLD_PATH, REQUIRED_SOURCES
from scripts.b6_6_probe_endpoints import _normalize_security_group_rules
from scripts.b6_6_runner import RunContext, Runner, StageFailure, StageResult
from scripts.b6_6_stage_a import (
    MAXIMUM_COST_USD,
    MAXIMUM_SECONDS,
    STABLE_PROBE_PASSES,
    StageAContext,
    StageARefusal,
    StageARunner,
)
from scripts.check_b6_6_window_plan import (
    TASK_ENI_EGRESS_RULES,
    PLAN_TASK_ENI_SECURITY_GROUPS,
    lint_rendered_plan_description_charset,
    lint_task_eni_security_group_egress,
)


RUNNER_SOURCES = tuple(sorted(REQUIRED_SOURCES - {COLD_PATH}))
GUARDS = {
    "stage0": ["persistent_secret", "operator_deny", "token_shape", "fresh_version"],
    "deadline": ["deadline_first_4500_seconds"],
    "workers_ready": ["bounded_worker_registration_1200_seconds"],
    "dra_ready": ["digest_pinned_dra_before_endpoints"],
    "rag_ready": ["digest_pinned_rag_before_endpoints"],
    "asr_ready": ["digest_pinned_loader_and_asr_before_endpoints"],
    "tts_ready": ["digest_pinned_tts_before_endpoints"],
    "llm_ready": ["digest_pinned_llm_before_endpoints"],
    "orchestrator_ready": ["digest_pinned_orchestrator_before_endpoints"],
    "controller_window": ["controller_plan_1_0_0_with_named_resource_receipt"],
    "controller_ready": ["digest_pinned_controller_before_endpoints"],
    "pre_endpoint_images": ["seven_pods_eight_resident_child_digests"],
    "terraform_window": ["endpoint_plan_13_0_0_with_named_resources_controller_noop"],
    "endpoints_ready": ["probe_exclusive_endpoints_available_900_seconds"],
    "fargate_probe": ["one_private_probe_no_public_ip_exact_hardened_task_boundary"],
    "alb_ready": ["internal_alb_exact_security_groups"],
    "alb_tag_mutation_warning": ["bounded_nonfatal_tag_rule_always_fatal_list"],
    "file_proof": ["synthetic_file_contract"],
    "websocket_proof": ["synthetic_websocket_contract"],
    "cancellation_proof": ["cancel_within_250ms"],
    "failure_drills": ["dependency_refusal_without_pod_recreation"],
    "isolation_proof": ["orchestrator_only_ingress_dependencies_clusterip"],
    "cleanup": ["zero_state_before_deadline_disarm_persistent_secret_retained"],
}


def _aws_read_fixture_fidelity() -> dict[str, Any]:
    decision_path = (
        ROOT / "platform/decisions/B6-AWS-READ-FIXTURE-FIDELITY-2026-001.json"
    )
    evidence_path = (
        ROOT / "platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-001.json"
    )
    decision = json.loads(decision_path.read_bytes())
    evidence = json.loads(evidence_path.read_bytes())
    if (
        decision.get("status") != "owner-directed-standing-rule"
        or evidence.get("status") != "PASS_READ_ONLY_LIVE_CAPTURE"
        or evidence.get("aws", {}).get("mutations") != 0
    ):
        raise AssertionError("AWS read-response fixture authority differs")
    captures = evidence.get("captures")
    if not isinstance(captures, list) or len(captures) != 2:
        raise AssertionError("AWS read-response fixture inventory differs")
    payloads: dict[str, dict[str, Any]] = {}
    fixture_hashes: dict[str, str] = {}
    for capture in captures:
        relative = capture.get("path")
        digest = capture.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise AssertionError("AWS read-response fixture binding is malformed")
        path = ROOT / relative
        if sha256_file(path) != digest:
            raise AssertionError("AWS read-response fixture hash differs")
        payloads[str(capture["api"])] = json.loads(path.read_bytes())
        fixture_hashes[relative] = digest

    merged_groups = payloads["ec2:DescribeSecurityGroups"].get("SecurityGroups")
    if not isinstance(merged_groups, list) or len(merged_groups) != 1:
        raise AssertionError("recorded DescribeSecurityGroups fixture differs")
    merged = merged_groups[0].get("IpPermissionsEgress")
    rules_response = payloads["ec2:DescribeSecurityGroupRules"]
    raw_rules = rules_response.get("SecurityGroupRules")
    normalized = _normalize_security_group_rules(rules_response)
    if (
        not isinstance(merged, list)
        or len(merged) != 1
        or not isinstance(raw_rules, list)
        or len([item for item in raw_rules if item.get("IsEgress") is True]) != 2
        or len(normalized) != 2
        or any(rule.protocol != "-1" for rule in normalized)
        or any(rule.from_port != -1 or rule.to_port != -1 for rule in normalized)
        or "FromPort" in merged[0]
        or "ToPort" in merged[0]
    ):
        raise AssertionError("recorded AWS response-shape behavior differs")
    return {
        "status": "PASS",
        "decision_path": str(decision_path.relative_to(ROOT)),
        "decision_sha256": sha256_file(decision_path),
        "evidence_path": str(evidence_path.relative_to(ROOT)),
        "evidence_sha256": sha256_file(evidence_path),
        "fixture_hashes": dict(sorted(fixture_hashes.items())),
        "merged_egress_permission_objects": 1,
        "individual_egress_rules": 2,
        "protocol_minus_one_port_quirk": "PASS",
        "real_aws_calls": 0,
    }


def _task_eni_sg_egress_lint() -> dict[str, Any]:
    external_evidence = json.loads(
        (
            ROOT
            / "platform/evidence/B6-BACKEND-TASK-ENI-SG-EGRESS-READBACK-2026-001.json"
        ).read_bytes()
    )
    external = external_evidence["security_group"]
    if (
        external_evidence.get("status") != "PASS_READ_ONLY"
        or external.get("group_id") != "sg-0a83abae6ab954543"
        or external.get("egress_rule_count", 0) < 1
        or external.get("minimum_one_egress_rule") != "PASS"
    ):
        raise AssertionError("external task ENI SG egress attestation differs")
    external_group = f"external:{external['group_id']}"
    attached_groups = set(PLAN_TASK_ENI_SECURITY_GROUPS) | {external_group}
    egress_by_group = {
        group: set(TASK_ENI_EGRESS_RULES)
        for group in PLAN_TASK_ENI_SECURITY_GROUPS
    }
    egress_by_group[external_group] = {external_evidence["id"]}
    result = lint_task_eni_security_group_egress(
        attached_groups, egress_by_group
    )
    missing_refusal_cases = 0
    for group in attached_groups:
        broken = {key: set(value) for key, value in egress_by_group.items()}
        broken[group] = set()
        try:
            lint_task_eni_security_group_egress(attached_groups, broken)
        except ValueError:
            missing_refusal_cases += 1
        else:
            raise AssertionError("task ENI SG without egress did not refuse")
    return {
        **result,
        "plan_managed_task_eni_security_groups": 1,
        "external_attested_task_eni_security_groups": 1,
        "packet_managed_egress_rules": 2,
        "external_attested_egress_rules": 1,
        "missing_egress_refusal_cases": missing_refusal_cases,
        "dns_security_group_filtering": (
            "NOT_APPLICABLE_AMAZON_PROVIDED_VPC_RESOLVER"
        ),
    }


def _terraform_description_charset_lint() -> dict[str, Any]:
    path = ROOT / "platform/evidence/B6-RENDERED-TERRAFORM-DESCRIPTIONS-2026-001.json"
    evidence = json.loads(path.read_bytes())
    projection = evidence.get("description_projection", {})
    items = projection.get("items")
    if (
        evidence.get("status") != "PASS_READ_ONLY_RENDERED_PLAN_PROJECTION"
        or evidence.get("aws_mutations") != 0
        or evidence.get("kubernetes_mutations") != 0
        or not isinstance(items, list)
        or hashlib.sha256(canonical_json(items)).hexdigest()
        != projection.get("canonical_sha256")
    ):
        raise AssertionError("rendered Terraform description projection differs")
    projected_plan = {
        "rendered_description_projection": [
            {"description": item.get("value")} for item in items
        ]
    }
    result = lint_rendered_plan_description_charset(projected_plan)
    expected = {
        "description_fields": projection.get("description_fields"),
        "string_descriptions": projection.get("string_descriptions"),
        "null_descriptions": projection.get("null_descriptions"),
        "invalid_descriptions": 0,
        "allowed_character_class": projection.get("allowed_character_class"),
    }
    if any(result.get(key) != value for key, value in expected.items()):
        raise AssertionError("rendered Terraform description lint result differs")
    injected = json.loads(json.dumps(projected_plan))
    injected["rendered_description_projection"][0]["description"] = "ECR's S3"
    try:
        lint_rendered_plan_description_charset(injected)
    except ValueError as exc:
        if "U+0027" not in str(exc):
            raise AssertionError("apostrophe refusal did not identify U+0027") from exc
    else:
        raise AssertionError("apostrophe did not refuse description lint")
    return {
        **result,
        "projection_path": str(path.relative_to(ROOT)),
        "projection_sha256": sha256_file(path),
        "projection_inventory_sha256": projection["canonical_sha256"],
        "invalid_description_refusal_cases": 1,
        "real_aws_calls": 0,
    }


class FakeSecretClient:
    def __init__(self, historical_versions: int = 7):
        self.versions = {
            hashlib.sha256(f"history-{index}".encode()).hexdigest(): []
            for index in range(historical_versions)
        }
        if self.versions:
            current = next(reversed(self.versions))
            self.versions[current] = ["AWSCURRENT"]

    def describe_secret(self, **_: Any) -> dict[str, Any]:
        return {"Name": SECRET_NAME, "ARN": SECRET_ARN, "KmsKeyId": KMS_KEY}

    def list_secret_version_ids(self, **_: Any) -> dict[str, Any]:
        return {
            "Versions": [
                {"VersionId": version, "VersionStages": stages}
                for version, stages in self.versions.items()
            ]
        }

    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        value = kwargs["SecretString"].encode()
        version = kwargs["ClientRequestToken"]
        if hashlib.sha256(value).hexdigest() != version:
            raise AssertionError("version ID is not the canonical secret-value hash")
        for existing, stages in self.versions.items():
            if "AWSCURRENT" in stages:
                self.versions[existing] = ["AWSPREVIOUS"]
            elif "AWSPREVIOUS" in stages:
                self.versions[existing] = []
        self.versions[version] = ["AWSCURRENT"]
        return {"ARN": SECRET_ARN, "VersionId": version}

    def get_secret_value(self, **_: Any) -> dict[str, Any]:
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "GetSecretValue",
        )


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> str:
        result = self.value.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.value += timedelta(seconds=1)
        return result


class FakeOperations:
    def __init__(self, fail_stage: str | None):
        self.fail_stage = fail_stage
        self.secret = FakeSecretClient()
        self.guards_invoked: dict[str, list[str]] = {}
        self.platform_mutations = 0
        self.real_aws_calls = 0
        self.real_kubectl_calls = 0
        self.zero_state = True

    def before_run(self, context: RunContext) -> None:
        del context

    def execute(self, stage: str, context: RunContext) -> StageResult:
        self.guards_invoked[stage] = GUARDS[stage]
        if stage == "stage0":
            payload = rotate_and_verify(
                self.secret,
                context.token_file,
                material_factory=lambda size: bytes(range(size)),
            )
        elif stage == "cleanup":
            context.token_file.unlink(missing_ok=True)
            self.zero_state = True
            payload = {
                "zero_state": True,
                "persistent_secret_retained": True,
                "local_material_removed": True,
                "deadline_actions": 0,
                "worker_instances": 0,
                "window_resources": 0,
            }
        else:
            if stage == "workers_ready":
                self.zero_state = False
            payload = {
                "cold_rehearsal": True,
                "invariants_verified": GUARDS[stage],
            }
        if self.fail_stage == stage:
            raise StageFailure(
                "INJECTED_COLD_REHEARSAL_FAILURE",
                {"injected_stage": stage, "guards_invoked": GUARDS[stage]},
            )
        return StageResult(payload=payload)

    def recover_cleanup(self, context: RunContext) -> dict[str, Any]:
        context.token_file.unlink(missing_ok=True)
        self.zero_state = True
        return {
            "recovery_completed": True,
            "zero_state": True,
            "persistent_secret_retained": True,
        }


class FakeStageAOperations:
    def __init__(self, fail_stage: str | None):
        self.fail_stage = fail_stage
        self.real_aws_calls = 0
        self.real_kubectl_calls = 0
        self.eks_worker_mutations = 0

    def before_run(self, context: StageAContext) -> None:
        del context

    def execute(self, stage: str, context: StageAContext) -> dict[str, Any]:
        del context
        if self.fail_stage == stage:
            raise StageARefusal("INJECTED_STAGE_A_FAILURE", {"injected_stage": stage})
        if stage.startswith("stage_a_probe_"):
            return {
                "ordinal": int(stage.rsplit("_", 1)[1]),
                "application_started": True,
                "image_pull_proven": True,
                "assign_public_ip": "DISABLED",
                "probe_task_security_group_count": 1,
            }
        if stage == "stage_a_cleanup":
            return {
                "cleanup_complete": True,
                "window_terraform_resources": 0,
                "cpu_desired": 0,
                "gpu_desired": 0,
                "eks_worker_mutations": 0,
            }
        return {
            "stage_a_guard_verified": stage,
            "eks_worker_mutations": 0,
        }

    def recover_cleanup(self, context: StageAContext) -> dict[str, Any]:
        del context
        return {
            "recovery_completed": True,
            "zero_state": True,
            "window_terraform_resources": 0,
            "probe_vpc_endpoints": 0,
            "probe_iam_roles": 0,
            "active_probe_ecs_clusters": 0,
            "cpu_desired": 0,
            "gpu_desired": 0,
        }


def _stage_a_scenario(root: Path, name: str, fail_stage: str | None) -> dict[str, Any]:
    directory = root / name
    operations = FakeStageAOperations(fail_stage)
    context = StageAContext(
        authorization=root / "fake-authorization.json",
        packet_sha256="0" * 64,
        receipts_dir=directory,
    )
    runner = StageARunner(operations, ReceiptStore(directory, clock=Clock()))
    result = runner.run(context)
    receipts = [
        {
            "stage": stage,
            "status": runner.store.load(stage)["status"],
            "sha256": sha256_file(runner.store.path(stage)),
        }
        for stage in STAGE_A_STAGES
        if runner.store.path(stage).exists()
    ]
    if fail_stage is None:
        if result.outcome != "PASS" or [item["stage"] for item in receipts] != list(STAGE_A_STAGES):
            raise AssertionError("Stage A cold pass did not produce its complete receipt chain")
        if any(item["status"] != "PASS" for item in receipts):
            raise AssertionError("Stage A cold pass contains a non-PASS receipt")
    else:
        statuses = {item["stage"]: item["status"] for item in receipts}
        if result.outcome != "REFUSED" or statuses.get(fail_stage) != "REFUSED":
            raise AssertionError(f"Stage A failure did not refuse exactly {fail_stage}")
        failure_receipt = runner.store.load(fail_stage)
        if (
            failure_receipt.get("payload", {}).get("safe_exception_text")
            != "INJECTED_STAGE_A_FAILURE"
        ):
            raise AssertionError("Stage A refusal lost its exact safe exception text")
        expected_cleanup = "REFUSED" if fail_stage == "stage_a_cleanup" else "PASS"
        if statuses.get("stage_a_cleanup") != expected_cleanup:
            raise AssertionError("Stage A injected cleanup status differs")
        if statuses.get("stage_a") != "REFUSED":
            raise AssertionError("Stage A aggregate did not refuse")
    return {
        "scenario": name,
        "injected_failure_stage": fail_stage,
        "outcome": result.outcome,
        "failure_stage": result.failure_stage,
        "cleanup_complete": result.cleanup_complete,
        "receipts": receipts,
        "real_aws_calls": operations.real_aws_calls,
        "real_kubectl_calls": operations.real_kubectl_calls,
        "eks_worker_mutations": operations.eks_worker_mutations,
        "maximum_seconds": MAXIMUM_SECONDS,
        "maximum_cost_usd": MAXIMUM_COST_USD,
        "required_consecutive_probe_passes": STABLE_PROBE_PASSES,
    }


def _scenario(root: Path, name: str, fail_stage: str | None) -> dict[str, Any]:
    directory = root / name
    operations = FakeOperations(fail_stage)
    context = RunContext(
        kubeconfig=root / "fake-kubeconfig",
        authorization=root / "fake-authorization.json",
        packet_sha256="0" * 64,
        receipts_dir=directory,
        token_file=root / f"{name}.token",
        attempt=1,
    )
    runner = Runner(operations, ReceiptStore(directory, clock=Clock()))
    result = runner.run(context)
    receipts = [
        {
            "stage": stage,
            "status": runner.store.load(stage)["status"],
            "sha256": sha256_file(runner.store.path(stage)),
        }
        for stage in WINDOW_STAGES
        if runner.store.path(stage).exists()
    ]
    if fail_stage is None:
        if result.outcome != "PASS" or len(receipts) != len(WINDOW_STAGES):
            raise AssertionError("full cold rehearsal did not produce 23 PASS receipts")
        if any(item["status"] != "PASS" for item in receipts):
            raise AssertionError("full cold rehearsal contains a non-PASS receipt")
    else:
        refused = [item for item in receipts if item["status"] == "REFUSED"]
        if result.outcome != "REFUSED" or [item["stage"] for item in refused] != [fail_stage]:
            raise AssertionError(f"injected failure did not refuse exactly {fail_stage}")
        cleanup = next(item for item in receipts if item["stage"] == "cleanup")
        expected_cleanup = "REFUSED" if fail_stage == "cleanup" else "PASS"
        if cleanup["status"] != expected_cleanup or not operations.zero_state:
            raise AssertionError("injected failure cleanup did not complete")
    if fail_stage is None:
        expected_guards = set(WINDOW_STAGES)
    else:
        expected_guards = set(
            WINDOW_STAGES[: WINDOW_STAGES.index(fail_stage) + 1]
        )
        if fail_stage != "cleanup":
            expected_guards.add("cleanup")
    if set(operations.guards_invoked) != expected_guards:
        raise AssertionError("cold rehearsal guard invocation set differs")
    return {
        "scenario": name,
        "injected_failure_stage": fail_stage,
        "outcome": result.outcome,
        "failure_stage": result.failure_stage,
        "cleanup_complete": operations.zero_state,
        "guards_invoked": operations.guards_invoked,
        "receipts": receipts,
        "real_aws_calls": operations.real_aws_calls,
        "real_kubectl_calls": operations.real_kubectl_calls,
        "platform_mutations": operations.platform_mutations,
    }


def run(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"cold rehearsal output already exists: {output_dir}")
    task_eni_sg_egress_lint = _task_eni_sg_egress_lint()
    terraform_description_charset_lint = _terraform_description_charset_lint()
    aws_read_fixture_fidelity = _aws_read_fixture_fidelity()
    with tempfile.TemporaryDirectory(prefix="medzen-b6-cold-") as temporary:
        root = Path(temporary)
        scenarios = [_scenario(root, "full-pass", None)]
        scenarios.extend(
            _scenario(root, f"fail-{index:02d}-{stage}", stage)
            for index, stage in enumerate(WINDOW_STAGES, start=1)
        )
        stage_a_scenarios = [_stage_a_scenario(root, "stage-a-full-pass", None)]
        stage_a_scenarios.extend(
            _stage_a_scenario(root, f"stage-a-fail-{index:02d}-{stage}", stage)
            for index, stage in enumerate(
                (*STAGE_A_EXECUTION_STAGES, "stage_a_cleanup"), start=1
            )
        )
    results_sha256 = hashlib.sha256(
        canonical_json({"window": scenarios, "stage_a": stage_a_scenarios})
    ).hexdigest()
    source_hashes = {relative: sha256_file(ROOT / relative) for relative in RUNNER_SOURCES}
    payload = {
        "review": "B6-WINDOW-DESIGN-REVIEW-2026-001",
        "status": "PASS_COLD_REHEARSAL",
        "full_pass_runs": 1,
        "injected_failure_runs": 23,
        "enumerated_stages": list(WINDOW_STAGES),
        "runner_source_hashes": source_hashes,
        "scenario_results_sha256": results_sha256,
        "scenarios": scenarios,
        "stage_a_full_pass_runs": 1,
        "stage_a_injected_failure_runs": len(stage_a_scenarios) - 1,
        "stage_a_scenarios": stage_a_scenarios,
        "task_eni_sg_egress_lint": task_eni_sg_egress_lint,
        "terraform_description_charset_lint": terraform_description_charset_lint,
        "aws_read_fixture_fidelity": aws_read_fixture_fidelity,
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "aws_mutations": 0,
        "kubernetes_mutations": 0,
    }
    store = ReceiptStore(output_dir)
    return store.persist("cold_rehearsal", "PASS", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.output_dir.resolve())
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
