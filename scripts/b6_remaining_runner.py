#!/usr/bin/env python3
"""Canonical two-attempt remaining-proofs runner for B6 packet 2026-034."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import EXECUTION_STAGES, ReceiptStore, sha256_file
from scripts.b6_6_runner import (
    REASON_CODES,
    SAFE_PROOF_STAGES,
    WARNING_STAGE,
    RunContext,
    RunResult,
    Runner,
    StageFailure,
    StageResult,
    safe_alb_refusal,
    safe_fargate_refusal,
    safe_proof_refusal,
    safe_stage0_refusal,
)
from scripts.b6_remaining_bindings import COLD_PATH, FILE_RECEIPT_PATH, validate


REMAINING_EXECUTION_STAGES = tuple(
    stage for stage in EXECUTION_STAGES if stage != "file_proof"
)
REMAINING_WINDOW_STAGES = (*REMAINING_EXECUTION_STAGES, "cleanup")
REMAINING_STAGE0_CODES = {
    "STAGE0_WORKER_CAPACITY_ZERO_REFUSED",
    "STAGE0_SYNTHETIC_DEPLOYMENT_ZERO_REFUSED",
    "STAGE0_ALB_ABSENCE_REFUSED",
}


def safe_remaining_stage0_refusal(
    path: Path, command_exit_code: int
) -> dict[str, Any] | None:
    inherited = safe_stage0_refusal(path, command_exit_code)
    if inherited is not None:
        return inherited
    try:
        value = json.loads(path.read_bytes())
    except Exception:
        return None
    reason = value.get("reason_code")
    assertion = value.get("failed_assertion")
    detail = value.get("safe_error_text")
    exit_code = value.get("stage_exit_code")
    if (
        value.get("status") != "REFUSED"
        or reason not in REMAINING_STAGE0_CODES
        or re.fullmatch(r"[A-Z0-9_]{1,96}", str(assertion)) is None
        or not isinstance(detail, str)
        or len(detail.encode("utf-8")) > 1024
        or exit_code != command_exit_code
        or not isinstance(exit_code, int)
        or not 1 <= exit_code <= 125
    ):
        return None
    return {
        "reason_code": reason,
        "failed_assertion": assertion,
        "stage_exit_code": exit_code,
        "safe_error_text": detail,
        "pre_model_and_audio": True,
    }


class RemainingRunner(Runner):
    """Run the reviewed sequence with the already-passed file proof omitted."""

    def run(self, context: RunContext) -> RunResult:
        cleanup_complete = False
        try:
            self.operations.before_run(context)
            for stage in REMAINING_EXECUTION_STAGES:
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


def _prior_attempt_continuity(
    attempt: int,
    prior: Path | None = None,
) -> None:
    if attempt == 1:
        return
    if attempt != 2:
        raise StageFailure("PACKET_2026_034_ATTEMPT_OUT_OF_RANGE")
    prior = prior or (
        ROOT / "platform/evidence/receipts/B6-2026-034-A1-LIVE"
    )
    store = ReceiptStore(prior)
    try:
        cleanup = store.load("cleanup")
        observed = {
            stage: store.load(stage)["status"]
            for stage in REMAINING_WINDOW_STAGES
            if store.path(stage).exists()
        }
    except Exception as exc:
        raise StageFailure("ATTEMPT_1_RECEIPT_CONTINUITY_REQUIRED") from exc
    cleanup_payload = cleanup.get("payload", {})
    zero_fields = (
        "alb_count",
        "approved_asr_changes",
        "cpu_asg_instances",
        "cpu_desired",
        "deadline_actions",
        "deployments",
        "endpoint_security_groups",
        "gpu_asg_instances",
        "gpu_desired",
        "ingresses",
        "probe_vpc_endpoints",
        "production_ssm_pointer_changes",
        "window_terraform_resources",
    )
    if (
        cleanup.get("status") != "PASS"
        or not any(status == "REFUSED" for status in observed.values())
        or any(cleanup_payload.get(field) != 0 for field in zero_fields)
        or cleanup_payload.get("persistent_synthetic_secret")
        != "RETAINED_OPERATOR_DENIED"
    ):
        raise StageFailure("ATTEMPT_1_CLEAN_REFUSAL_CONTINUITY_REQUIRED")
    if all(observed.get(stage) == "PASS" for stage in REMAINING_EXECUTION_STAGES):
        raise StageFailure("PASS_TERMINATES_PACKET")


class RealOperations:
    """Execute only the reviewed remaining-proofs operation dispatcher."""

    def __init__(self) -> None:
        self.endpoints_enabled = False

    def before_run(self, context: RunContext) -> None:
        if context.attempt not in {1, 2}:
            raise StageFailure("PACKET_2026_034_ATTEMPT_OUT_OF_RANGE")
        expected = (
            ROOT
            / "platform/evidence/receipts"
            / f"B6-2026-034-A{context.attempt}-LIVE"
        )
        if context.receipts_dir != expected or context.receipts_dir.exists():
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
        _prior_attempt_continuity(context.attempt)
        with tempfile.TemporaryDirectory(
            prefix=f"medzen-b6-034-a{context.attempt}-pre-attempt-cold-"
        ) as temporary:
            output = Path(temporary) / "receipt"
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/b6_remaining_cold_rehearsal.py",
                    "--output-dir",
                    str(output),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise StageFailure("PRE_ATTEMPT_COLD_REHEARSAL_REFUSED")
            fresh = json.loads((output / "cold_rehearsal.json").read_bytes())
        bound = json.loads((ROOT / COLD_PATH).read_bytes())
        if fresh.get("payload") != bound.get("payload"):
            raise StageFailure("PRE_ATTEMPT_COLD_REHEARSAL_DIFFERS")
        file_receipt = json.loads((ROOT / FILE_RECEIPT_PATH).read_bytes())
        if file_receipt.get("stage") != "file_proof" or file_receipt.get("status") != "PASS":
            raise StageFailure("PRESERVED_FILE_PROOF_PASS_REQUIRED")

    def execute(self, stage: str, context: RunContext) -> StageResult:
        with tempfile.NamedTemporaryFile(
            prefix="medzen-b6-034-payload-", delete=False
        ) as stream:
            payload_path = Path(stream.name)
        try:
            completed = subprocess.run(
                [
                    "bash",
                    "scripts/b6_remaining_operations.sh",
                    stage,
                    str(context.kubeconfig),
                    str(context.authorization),
                    context.packet_sha256,
                    str(context.receipts_dir),
                    str(context.token_file),
                    str(context.attempt),
                    str(payload_path),
                ],
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
                    detail = safe_fargate_refusal(payload_path)
                    if detail is not None:
                        diagnosis["probe_refusal"] = detail
                if stage == "alb_ready":
                    detail = safe_alb_refusal(payload_path)
                    if detail is not None:
                        diagnosis["alb_refusal"] = detail
                if stage == "stage0":
                    detail = safe_remaining_stage0_refusal(
                        payload_path, completed.returncode
                    )
                    if detail is not None:
                        diagnosis["stage0_refusal"] = detail
                if stage in SAFE_PROOF_STAGES:
                    detail = safe_proof_refusal(payload_path, completed.returncode)
                    if detail is not None:
                        diagnosis["proof_refusal"] = detail
                if self.endpoints_enabled and stage != "cleanup":
                    post = subprocess.run(
                        [
                            sys.executable,
                            "scripts/b6_remaining_pre_endpoint_images.py",
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
                "scripts/b6_remaining_operations.sh",
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
    result = RemainingRunner(
        RealOperations(), ReceiptStore(context.receipts_dir)
    ).run(context)
    print(json.dumps(result.__dict__, sort_keys=True, separators=(",", ":")))
    return 0 if result.outcome == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
