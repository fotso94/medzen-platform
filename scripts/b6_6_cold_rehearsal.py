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
    WINDOW_STAGES,
    ReceiptStore,
    canonical_json,
    sha256_file,
)
from scripts.b6_6_credential import KMS_KEY, SECRET_ARN, SECRET_NAME, rotate_and_verify
from scripts.b6_6_bindings import COLD_PATH, REQUIRED_SOURCES
from scripts.b6_6_runner import RunContext, Runner, StageFailure, StageResult


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
    "terraform_window": ["endpoint_plan_11_0_0_with_named_resources_controller_noop"],
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
    with tempfile.TemporaryDirectory(prefix="medzen-b6-cold-") as temporary:
        root = Path(temporary)
        scenarios = [_scenario(root, "full-pass", None)]
        scenarios.extend(
            _scenario(root, f"fail-{index:02d}-{stage}", stage)
            for index, stage in enumerate(WINDOW_STAGES, start=1)
        )
    results_sha256 = hashlib.sha256(canonical_json(scenarios)).hexdigest()
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
