#!/usr/bin/env python3
"""Canonical B6.6 runner; every path uses the one receipt engine."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import (
    EXECUTION_STAGES,
    STAGE_A_EXECUTION_STAGES,
    WINDOW_STAGES,
    ReceiptStore,
    sha256_file,
)


WARNING_STAGE = "alb_tag_mutation_warning"
REASON_CODES = {
    "stage0": "STAGE0_CREDENTIAL_OR_PREFLIGHT_REFUSED",
    "deadline": "DEADLINE_ARM_REFUSED",
    "workers_ready": "WORKER_REGISTRATION_REFUSED",
    "dra_ready": "PRE_ENDPOINT_DRA_READINESS_REFUSED",
    "rag_ready": "PRE_ENDPOINT_RAG_READINESS_REFUSED",
    "asr_ready": "PRE_ENDPOINT_ASR_READINESS_REFUSED",
    "tts_ready": "PRE_ENDPOINT_TTS_READINESS_REFUSED",
    "llm_ready": "PRE_ENDPOINT_LLM_READINESS_REFUSED",
    "orchestrator_ready": "PRE_ENDPOINT_ORCHESTRATOR_READINESS_REFUSED",
    "controller_window": "PRE_ENDPOINT_CONTROLLER_PLAN_REFUSED",
    "controller_ready": "PRE_ENDPOINT_CONTROLLER_READINESS_REFUSED",
    "pre_endpoint_images": "PRE_ENDPOINT_IMAGE_RESIDENCY_REFUSED",
    "terraform_window": "ENDPOINT_TERRAFORM_WINDOW_REFUSED",
    "endpoints_ready": "ENDPOINT_AVAILABILITY_REFUSED",
    "fargate_probe": "FARGATE_PROBE_REFUSED",
    "alb_ready": "ALB_READINESS_REFUSED",
    "alb_tag_mutation_warning": "ALB_TAG_CLASSIFICATION_REFUSED",
    "file_proof": "FILE_CONVERSATION_PROOF_REFUSED",
    "websocket_proof": "WEBSOCKET_CONVERSATION_PROOF_REFUSED",
    "cancellation_proof": "CANCELLATION_PROOF_REFUSED",
    "failure_drills": "FAILURE_DRILL_REFUSED",
    "isolation_proof": "ISOLATION_PROOF_REFUSED",
    "cleanup": "CLEANUP_REFUSED",
}
SAFE_FARGATE_REFUSAL_CODES = {
    "ECR_IMAGE_PULL_FAILURE",
    "IMAGE_PULL_FAILURE",
    "PROBE_CONTAINER_BOUNDARY_DIFFERS",
    "PROBE_CONTAINER_NONZERO_OR_UNKNOWN_STOP",
    "PROBE_TARGET_URL_DIFFERS",
    "PROBE_TASK_ARN_ABSENT",
    "PROBE_TASK_CONTAINER_COUNT_DIFFERS",
    "PROBE_TASK_DEFINITION_ARN_DIFFERS",
    "PROBE_TASK_DEFINITION_BOUNDARY_DIFFERS",
    "PROBE_TASK_READBACK_DIFFERS",
    "PROBE_TASK_TIMEOUT",
    "PROBE_TASK_WAIT_BOUND_DIFFERS",
    "RUN_TASK_REFUSED",
    "RUN_TASK_RESPONSE_HAS_NO_TASK_ARN",
    "TASK_FAILED_TO_START",
    "TASK_STOPPED_BY_SERVICE",
}


def safe_fargate_refusal(path: Path) -> dict[str, Any] | None:
    """Return only allowlisted, non-PHI probe refusal fields."""
    try:
        value = json.loads(path.read_bytes())
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    reason = value.get("reason_code")
    if (
        value.get("status") != "REFUSED"
        or reason not in SAFE_FARGATE_REFUSAL_CODES
        or not isinstance(value.get("application_started"), bool)
        or not isinstance(value.get("readyz_request_completed"), bool)
        or re.fullmatch(r"[A-Z0-9_]{1,80}", str(reason)) is None
    ):
        return None
    return {
        "reason_code": reason,
        "application_started": value["application_started"],
        "readyz_request_completed": value["readyz_request_completed"],
    }


class StageFailure(RuntimeError):
    def __init__(self, reason_code: str, payload: dict[str, Any] | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.payload = payload or {}


@dataclass(frozen=True)
class StageResult:
    payload: dict[str, Any]
    status: str = "PASS"


@dataclass(frozen=True)
class RunContext:
    kubeconfig: Path
    authorization: Path
    packet_sha256: str
    receipts_dir: Path
    token_file: Path
    attempt: int


@dataclass(frozen=True)
class RunResult:
    outcome: str
    failure_stage: str | None
    receipt_hashes: dict[str, str]
    cleanup_complete: bool


class Operations(Protocol):
    def before_run(self, context: RunContext) -> None: ...
    def execute(self, stage: str, context: RunContext) -> StageResult: ...
    def recover_cleanup(self, context: RunContext) -> dict[str, Any]: ...


class RealOperations:
    """Execute the reviewed real-operation dispatcher without logging its output."""

    def __init__(self) -> None:
        self.endpoints_enabled = False

    def before_run(self, context: RunContext) -> None:
        from scripts.b6_6_bindings import COLD_PATH, validate

        expected_directory = (
            ROOT
            / "platform/evidence/receipts"
            / f"B6-2026-025-A{context.attempt}-LIVE"
        )
        if context.receipts_dir != expected_directory or context.receipts_dir.exists():
            raise StageFailure("EXECUTION_RECEIPT_DIRECTORY_DIFFERS")
        if context.token_file != Path("/private/tmp/medzen-b6-6-client-token"):
            raise StageFailure("TOKEN_PATH_DIFFERS")
        if context.token_file.exists():
            raise StageFailure("PREEXISTING_LOCAL_TOKEN_REFUSED")
        authorization = validate(context.authorization, context.packet_sha256, ROOT)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        reviewed = authorization["prepared_repository_commit"]
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", reviewed, head],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if ancestry.returncode != 0 or dirty:
            raise StageFailure("REVIEWED_CLEAN_COMMIT_REQUIRED")
        bridge_path = (
            ROOT
            / "platform/evidence/receipts/B6-2026-020A-BRIDGE"
            / "persistent_secret_bridge.json"
        )
        try:
            bridge = ReceiptStore(bridge_path.parent).load("persistent_secret_bridge")
        except Exception as exc:
            raise StageFailure("PERSISTENT_SECRET_BRIDGE_RECEIPT_REQUIRED") from exc
        if bridge.get("stage") != "persistent_secret_bridge" or bridge.get("status") != "PASS":
            raise StageFailure("PERSISTENT_SECRET_BRIDGE_RECEIPT_REQUIRED")

        from scripts.b6_6_stage_a import (
            MAXIMUM_COST_USD as STAGE_A_MAXIMUM_COST_USD,
            MAXIMUM_SECONDS as STAGE_A_MAXIMUM_SECONDS,
            RECEIPTS as STAGE_A_RECEIPTS,
            STABLE_PROBE_PASSES,
        )

        stage_a_store = ReceiptStore(STAGE_A_RECEIPTS)
        try:
            stage_a = stage_a_store.load("stage_a")
            stage_a_cleanup = stage_a_store.load("stage_a_cleanup")
            stage_a_predecessors = (*STAGE_A_EXECUTION_STAGES, "stage_a_cleanup")
            predecessor_hashes = {
                stage: sha256_file(stage_a_store.path(stage))
                for stage in stage_a_predecessors
            }
        except Exception as exc:
            raise StageFailure("PASSING_STAGE_A_RECEIPT_REQUIRED") from exc
        if (
            stage_a.get("status") != "PASS"
            or stage_a_cleanup.get("status") != "PASS"
            or any(
                stage_a_store.load(stage).get("status") != "PASS"
                for stage in STAGE_A_EXECUTION_STAGES
            )
            or stage_a.get("dependencies") != predecessor_hashes
            or not isinstance(stage_a.get("payload"), dict)
            or any(
                stage_a["payload"].get(key) != expected
                for key, expected in {
                    "packet_sha256": context.packet_sha256,
                    "stable_probe_passes": STABLE_PROBE_PASSES,
                    "required_consecutive_probe_passes": STABLE_PROBE_PASSES,
                    "cleanup_complete": True,
                    "eks_worker_mutations": 0,
                    "maximum_seconds": STAGE_A_MAXIMUM_SECONDS,
                    "maximum_cost_usd": STAGE_A_MAXIMUM_COST_USD,
                    "window_attempts_unlocked": True,
                    "failure_stage": None,
                }.items()
            )
        ):
            raise StageFailure("PASSING_STAGE_A_RECEIPT_REQUIRED")

        with tempfile.TemporaryDirectory(prefix="medzen-b6-025-pre-attempt-cold-") as temporary:
            cold_directory = Path(temporary) / "receipt"
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/b6_6_cold_rehearsal.py",
                    "--output-dir",
                    str(cold_directory),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise StageFailure("PRE_ATTEMPT_COLD_REHEARSAL_REFUSED")
            fresh = json.loads((cold_directory / "cold_rehearsal.json").read_bytes())
        bound = json.loads((ROOT / COLD_PATH).read_bytes())
        compared = (
            "status",
            "full_pass_runs",
            "injected_failure_runs",
            "enumerated_stages",
            "runner_source_hashes",
            "scenario_results_sha256",
            "real_aws_calls",
            "real_kubectl_calls",
            "aws_mutations",
            "kubernetes_mutations",
            "stage_a_full_pass_runs",
            "stage_a_injected_failure_runs",
            "task_eni_sg_egress_lint",
            "terraform_description_charset_lint",
            "aws_read_fixture_fidelity",
        )
        if any(fresh["payload"].get(key) != bound["payload"].get(key) for key in compared):
            raise StageFailure("PRE_ATTEMPT_COLD_REHEARSAL_DIFFERS")

    def execute(self, stage: str, context: RunContext) -> StageResult:
        with tempfile.NamedTemporaryFile(prefix="medzen-b6-payload-", delete=False) as stream:
            payload_path = Path(stream.name)
        try:
            command = [
                "bash",
                "scripts/b6_6_operations.sh",
                stage,
                str(context.kubeconfig),
                str(context.authorization),
                context.packet_sha256,
                str(context.receipts_dir),
                str(context.token_file),
                str(context.attempt),
                str(payload_path),
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                diagnosis: dict[str, Any] = {
                    "reason_code": REASON_CODES[stage],
                    "command_exit_code": completed.returncode,
                }
                if stage == "fargate_probe":
                    probe_refusal = safe_fargate_refusal(payload_path)
                    if probe_refusal is not None:
                        diagnosis["probe_refusal"] = probe_refusal
                if self.endpoints_enabled and stage != "cleanup":
                    post = subprocess.run(
                        [
                            sys.executable,
                            "scripts/b6_6_pre_endpoint_images.py",
                            "post-failure",
                            "--kubeconfig",
                            str(context.kubeconfig),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    try:
                        post_value = json.loads(post.stdout)
                    except json.JSONDecodeError:
                        post_value = {
                            "status": "NOT_CHECKED",
                            "reason_code": "POST_ENDPOINT_DIAGNOSIS_MALFORMED",
                        }
                    diagnosis["post_endpoint_image_diagnosis"] = post_value
                    if post.returncode == 3:
                        diagnosis["reason_code"] = (
                            "POST_ENDPOINT_NEW_KUBERNETES_IMAGE_PULL_FATAL"
                        )
                raise StageFailure(str(diagnosis["reason_code"]), diagnosis)
            try:
                payload = json.loads(payload_path.read_bytes())
            except Exception as exc:
                raise StageFailure("STAGE_PAYLOAD_MALFORMED") from exc
            if not isinstance(payload, dict):
                raise StageFailure("STAGE_PAYLOAD_MALFORMED")
            status = payload.pop("receipt_status", "PASS")
            if status not in {"PASS", "WARNING_NON_FATAL"}:
                raise StageFailure("STAGE_STATUS_MALFORMED")
            if status == "WARNING_NON_FATAL" and stage != WARNING_STAGE:
                raise StageFailure("WARNING_OUTSIDE_APPROVED_STAGE")
            if stage == "terraform_window":
                self.endpoints_enabled = True
            return StageResult(payload=payload, status=status)
        finally:
            payload_path.unlink(missing_ok=True)

    def recover_cleanup(self, context: RunContext) -> dict[str, Any]:
        completed = subprocess.run(
            [
                "bash",
                "scripts/b6_6_operations.sh",
                "cleanup-recovery",
                str(context.kubeconfig),
                str(context.authorization),
                context.packet_sha256,
                str(context.receipts_dir),
                str(context.token_file),
                str(context.attempt),
                "/dev/null",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        return {
            "recovery_completed": completed.returncode == 0,
            "recovery_exit_code": completed.returncode,
        }


class Runner:
    def __init__(self, operations: Operations, store: ReceiptStore):
        self.operations = operations
        self.store = store
        self.previous: dict[str, str] = {}
        self.failure_stage: str | None = None

    def _dependencies(self, stage: str) -> dict[str, str]:
        if stage == "cleanup":
            return self.store.hashes()
        if not self.previous:
            return {}
        previous_stage = next(reversed(self.previous))
        return {previous_stage: self.previous[previous_stage]}

    def _persist(
        self, stage: str, status: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        value = self.store.persist(
            stage,
            status,
            payload,
            dependencies=self._dependencies(stage),
        )
        self.previous[stage] = value["receipt_sha256"]
        return value

    def _run_stage(self, stage: str, context: RunContext) -> bool:
        status = "REFUSED"
        payload: dict[str, Any] = {"reason_code": REASON_CODES[stage]}
        try:
            result = self.operations.execute(stage, context)
            status = result.status
            payload = result.payload
        except StageFailure as exc:
            payload = {"reason_code": exc.reason_code, **exc.payload}
            self.failure_stage = stage
        except Exception as exc:
            payload = {
                "reason_code": REASON_CODES[stage],
                "exception_class": type(exc).__name__,
            }
            self.failure_stage = stage
        finally:
            if stage == "cleanup" and status == "REFUSED":
                try:
                    recovery = self.operations.recover_cleanup(context)
                except Exception as exc:
                    recovery = {
                        "recovery_completed": False,
                        "recovery_exception_class": type(exc).__name__,
                    }
                payload = {**payload, "cleanup_recovery": recovery}
            self._persist(stage, status, payload)
        return status in {"PASS", "WARNING_NON_FATAL"}

    def run(self, context: RunContext) -> RunResult:
        cleanup_complete = False
        try:
            self.operations.before_run(context)
            for stage in EXECUTION_STAGES:
                if not self._run_stage(stage, context):
                    break
        except Exception as exc:
            self.failure_stage = self.failure_stage or "runner_exception"
            if not self.store.path("runner_exception").exists():
                self.store.persist(
                    "runner_exception",
                    "REFUSED",
                    {
                        "reason_code": "RUNNER_TOP_LEVEL_EXCEPTION",
                        "terminal_classification": "EXCEPTION",
                        "exception_class": type(exc).__name__,
                    },
                    dependencies=self.store.hashes(),
                )
        finally:
            if not self.store.path("cleanup").exists():
                cleanup_complete = self._run_stage("cleanup", context)
            else:
                cleanup_complete = self.store.load("cleanup")["status"] == "PASS"
        outcome = "PASS" if self.failure_stage is None and cleanup_complete else "REFUSED"
        return RunResult(
            outcome=outcome,
            failure_stage=self.failure_stage,
            receipt_hashes=self.store.hashes(),
            cleanup_complete=cleanup_complete,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--attempt", type=int, choices=(1, 2), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    context = RunContext(
        kubeconfig=args.kubeconfig.resolve(),
        authorization=args.authorization.resolve(),
        packet_sha256=args.packet_sha256,
        receipts_dir=args.receipts_dir.resolve(),
        token_file=args.token_file.resolve(),
        attempt=args.attempt,
    )
    result = Runner(RealOperations(), ReceiptStore(context.receipts_dir)).run(context)
    print(json.dumps(result.__dict__, sort_keys=True, separators=(",", ":")))
    return 0 if result.outcome == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
