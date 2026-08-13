#!/usr/bin/env python3
"""Canonical stage runner for the ASR base-model offline pilot successor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import (  # noqa: E402
    ReceiptStore,
    STAGES,
    canonical_json,
    write_exclusive,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan  # noqa: E402
from scripts.asr_external_tool import configure_external_tool_journal, run_external  # noqa: E402


class OperationRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str, *, outcome: str | None = None):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail
        self.outcome = outcome


class Operations(Protocol):
    def deadline_identity_and_acceptance(
        self,
        context: "AttemptContext",
        *,
        dry_run: bool = False,
        caller_arn: str | None = None,
    ) -> dict[str, Any]: ...
    def input_freeze_and_no_phi(self, context: "AttemptContext") -> dict[str, Any]: ...
    def cost_and_zero_state(self, context: "AttemptContext") -> dict[str, Any]: ...
    def image_publication_and_scan(self, context: "AttemptContext") -> dict[str, Any]: ...
    def artifact_stage(self, context: "AttemptContext") -> dict[str, Any]: ...
    def private_endpoint_and_policy_gate(self, context: "AttemptContext") -> dict[str, Any]: ...
    def gpu_and_sampler_gate(self, context: "AttemptContext") -> dict[str, Any]: ...
    def node_local_input_stage(self, context: "AttemptContext") -> dict[str, Any]: ...
    def pilot_rows(self, context: "AttemptContext") -> dict[str, Any]: ...
    def aggregate_report(self, context: "AttemptContext") -> dict[str, Any]: ...
    def cleanup_and_expiry(self, context: "AttemptContext") -> dict[str, Any]: ...


@dataclass
class AttemptContext:
    attempt: int
    bindings: dict[str, Any]
    receipts: ReceiptStore
    workdir: Path
    deadline_seconds: int = 10800
    authorization_path: Path | None = None
    packet_path: Path | None = None
    dry_run_path: Path | None = None
    bindings_sha256: str | None = None
    reviewed_worktree_root: Path | None = None
    local_resource_snapshot: dict[str, Any] | None = None
    local_resource_validation: dict[str, Any] | None = None
    filesystem_events: list[str] = field(default_factory=list)


def validate_external_workdir(root: Path, workdir: Path) -> Path:
    """Require all live/rehearsal side effects to remain outside reviewed Git."""
    reviewed_root = root.resolve()
    candidate = workdir.resolve()
    try:
        candidate.relative_to(reviewed_root)
    except ValueError:
        return candidate
    raise OperationRefusal(
        "EXECUTION_WORKDIR_INSIDE_REVIEWED_WORKTREE",
        "execution workdir must be outside the reviewed worktree",
    )


def build_attempt_context(
    *,
    root: Path,
    workdir: Path,
    attempt: int,
    bindings: dict[str, Any],
    packet_sha256: str,
    authorization_sha256: str,
    authorization_path: Path | None = None,
    packet_path: Path | None = None,
    dry_run_path: Path | None = None,
    bindings_sha256: str | None = None,
    local_resource_snapshot: dict[str, Any] | None = None,
) -> AttemptContext:
    """Canonical, side-effect-free bootstrap shared by live and rehearsal."""
    external = validate_external_workdir(root, workdir)
    return AttemptContext(
        attempt=attempt,
        bindings=bindings,
        receipts=ReceiptStore(
            external / "receipts",
            packet_sha256=packet_sha256,
            authorization_sha256=authorization_sha256,
        ),
        workdir=external,
        authorization_path=authorization_path,
        packet_path=packet_path,
        dry_run_path=dry_run_path,
        bindings_sha256=bindings_sha256,
        reviewed_worktree_root=root.resolve(),
        local_resource_snapshot=local_resource_snapshot,
    )


def validate_clean_reviewed_worktree(root: Path) -> str:
    """Read-only clean-HEAD gate shared by live and cold-rehearsal execution."""
    completed, diagnostic = run_external(
        ["git", "status", "--porcelain=v1"],
        cwd=root,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise OperationRefusal(
            "REVIEWED_WORKTREE_STATUS_UNREADABLE",
            f"reviewed worktree status could not be read: {diagnostic}",
        )
    if completed.stdout:
        raise OperationRefusal(
            "REVIEWED_CLEAN_COMMIT_REQUIRED",
            "execution requires a clean worktree before runtime evidence exists",
        )
    head, diagnostic = run_external(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        timeout=60,
    )
    if head.returncode != 0:
        raise OperationRefusal(
            "REVIEWED_WORKTREE_HEAD_UNREADABLE",
            f"reviewed worktree HEAD could not be read: {diagnostic}",
        )
    return head.stdout.strip()


def _pass(payload: dict[str, Any], expected: str) -> dict[str, Any]:
    if payload.get("status") != expected:
        raise OperationRefusal("STAGE_RESULT_DIFFERS", f"expected {expected}, got {payload.get('status')}")
    return payload


def stage_deadline_identity_and_acceptance(
    ops: Operations,
    context: AttemptContext,
    *,
    dry_run: bool = False,
    caller_arn: str | None = None,
) -> dict[str, Any]:
    if dry_run or caller_arn is not None:
        payload = ops.deadline_identity_and_acceptance(
            context,
            dry_run=dry_run,
            caller_arn=caller_arn,
        )
    else:
        payload = ops.deadline_identity_and_acceptance(context)
    return _pass(payload, "PASS_DEADLINE_IDENTITY_AND_ACCEPTANCE")


def stage_input_freeze_and_no_phi(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.input_freeze_and_no_phi(context), "PASS_INPUT_FREEZE_AND_NO_PHI")


def stage_cost_and_zero_state(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.cost_and_zero_state(context), "PASS_COST_AND_ZERO_STATE")


def stage_image_publication_and_scan(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.image_publication_and_scan(context), "PASS_IMAGE_PUBLICATION_AND_SCAN")


def stage_artifact_stage(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.artifact_stage(context), "PASS_ARTIFACT_STAGE")


def stage_private_endpoint_and_policy_gate(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.private_endpoint_and_policy_gate(context), "PASS_PRIVATE_ENDPOINT_AND_POLICY_GATE")


def stage_gpu_and_sampler_gate(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.gpu_and_sampler_gate(context), "PASS_GPU_AND_SAMPLER_GATE")


def stage_node_local_input_stage(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.node_local_input_stage(context), "PASS_NODE_LOCAL_INPUT_STAGE")


def stage_pilot_rows(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.pilot_rows(context), "PASS_PILOT_ROWS")


def stage_aggregate_report(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    value = ops.aggregate_report(context)
    if value.get("status") not in {"PASS_AGGREGATE_REPORT", "INCOMPLETE_MEASUREMENT"}:
        raise OperationRefusal("AGGREGATE_RESULT_DIFFERS", "aggregate result is unknown")
    return value


def stage_cleanup_and_expiry(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.cleanup_and_expiry(context), "PASS_CLEANUP_AND_EXPIRY")


STAGE_FUNCTIONS = {
    "deadline_identity_and_acceptance": stage_deadline_identity_and_acceptance,
    "input_freeze_and_no_phi": stage_input_freeze_and_no_phi,
    "cost_and_zero_state": stage_cost_and_zero_state,
    "image_publication_and_scan": stage_image_publication_and_scan,
    "artifact_stage": stage_artifact_stage,
    "private_endpoint_and_policy_gate": stage_private_endpoint_and_policy_gate,
    "gpu_and_sampler_gate": stage_gpu_and_sampler_gate,
    "node_local_input_stage": stage_node_local_input_stage,
    "pilot_rows": stage_pilot_rows,
    "aggregate_report": stage_aggregate_report,
    "cleanup_and_expiry": stage_cleanup_and_expiry,
}


OUTCOME_BY_STAGE = {
    "input_freeze_and_no_phi": "BLOCKED_INPUT_FREEZE",
    "image_publication_and_scan": "BLOCKED_IMAGE_SCAN",
    "private_endpoint_and_policy_gate": "BLOCKED_NETWORK_ISOLATION",
}


def _safe_reason(exc: Exception) -> dict[str, str]:
    # The runner can be invoked either with ``python -m`` or by file path. In
    # the latter case Python may load this module once as ``__main__`` and once
    # by package name through the operations module. Attribute-based handling
    # preserves a typed refusal across that module-identity boundary.
    code = getattr(exc, "reason_code", "UNEXPECTED_STAGE_EXCEPTION")
    detail = getattr(exc, "detail", str(exc) or type(exc).__name__)
    if re.fullmatch(r"[A-Z0-9_]{1,96}", code) is None:
        code = "MALFORMED_REASON_CODE"
    detail = " ".join(str(detail).split())[:512]
    return {"reason_code": code, "safe_error_text": detail}


def _refusal_outcome(exc: Exception) -> str | None:
    value = getattr(exc, "outcome", None)
    return value if isinstance(value, str) and value else None


def validate_authorization_payload(
    authorization: dict[str, Any],
    *,
    expected_id: str,
    packet_sha256: str,
    risk_sha256: str,
    attempt: int,
) -> dict[str, Any]:
    attempts = authorization.get("attempts")
    if not isinstance(attempts, dict):
        raise OperationRefusal("AUTHORIZATION_ATTEMPTS_ABSENT", "top-level attempt authorization is absent")
    numbers = attempts.get("authorized_numbers")
    if (
        authorization.get("id") != expected_id
        or authorization.get("status") != "owner-approved"
        or authorization.get("packet", {}).get("sha256") != packet_sha256
        or authorization.get("risk_acceptance", {}).get("sha256") != risk_sha256
        or not isinstance(numbers, list)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in numbers)
        or len(numbers) != len(set(numbers))
        or attempts.get("maximum") != len(numbers)
        or attempts.get("seconds_each") != 10800
        or attempts.get("non_transferable") is not True
        or attempt not in numbers
    ):
        raise OperationRefusal("AUTHORIZATION_BINDING_DIFFERS", "successor owner authorization differs")
    return {
        "status": "PASS_AUTHORIZATION_SCHEMA",
        "attempt": attempt,
        "authorized_numbers": numbers,
        "seconds_each": attempts["seconds_each"],
        "non_transferable": True,
    }


def write_attempt_envelope(context: AttemptContext) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    value = {
        "schema_version": 1,
        "status": "DEADLINE_DECLARED_BEFORE_MUTATION",
        "attempt": context.attempt,
        "declared_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "hard_deadline_utc": (now + timedelta(seconds=context.deadline_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "maximum_seconds": context.deadline_seconds,
        "packet_sha256": context.receipts.packet_sha256,
        "authorization_sha256": context.receipts.authorization_sha256,
    }
    encoded = canonical_json(value)
    write_exclusive(context.workdir / "attempt-envelope.json", encoded)
    return {**value, "sha256": hashlib.sha256(encoded).hexdigest()}


def execute_attempt(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    reviewed_root = context.reviewed_worktree_root or ROOT
    context.workdir = validate_external_workdir(reviewed_root, context.workdir)
    if context.receipts.directory.resolve() != context.workdir / "receipts":
        raise OperationRefusal(
            "RECEIPT_STORE_PATH_DIFFERS",
            "receipt store must be the receipts directory under the external workdir",
        )
    context.filesystem_events.append("external_workdir_validated_before_side_effects")
    if context.reviewed_worktree_root is not None:
        validate_clean_reviewed_worktree(reviewed_root)
        context.filesystem_events.append("reviewed_worktree_clean_before_side_effects")
        if context.attempt >= 13:
            try:
                from scripts.asr_base_model_local_resources import (
                    LocalResourceRefusal,
                    collect_local_resource_snapshot,
                    validate_local_resource_snapshot,
                )

                policy = context.bindings.get("local_resource_policy")
                if not isinstance(policy, dict):
                    raise OperationRefusal(
                        "LOCAL_RESOURCE_POLICY_ABSENT",
                        "a bound local resource policy is required before an attempt envelope",
                    )
                snapshot = context.local_resource_snapshot or collect_local_resource_snapshot(
                    context.workdir
                )
                validation = validate_local_resource_snapshot(policy, snapshot)
                context.filesystem_events.append("pre_envelope_local_resources_passed")
                context.local_resource_validation = validation
            except LocalResourceRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
        context.workdir.mkdir(parents=True, exist_ok=False)
        context.filesystem_events.append("external_workdir_created")
    prior_journal = configure_external_tool_journal(
        context.workdir / "external-tool-diagnostics"
    )
    try:
        return _execute_attempt(ops, context)
    finally:
        configure_external_tool_journal(prior_journal)


def _execute_attempt(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    if context.attempt not in set(range(1, 17)) or context.deadline_seconds != 10800:
        raise OperationRefusal("ATTEMPT_BOUNDARY_DIFFERS", "only attempts 1 through 16 at 10800 seconds are permitted")
    if context.dry_run_path is not None:
        if not context.dry_run_path.is_file():
            raise OperationRefusal(
                "COMMITTED_STAGE_ONE_DRY_RUN_ABSENT",
                "committed deadline dry-run receipt is absent",
            )
        try:
            dry_run = json.loads(context.dry_run_path.read_bytes())
        except Exception as exc:
            raise OperationRefusal(
                "COMMITTED_STAGE_ONE_DRY_RUN_MALFORMED",
                "committed deadline dry-run receipt is malformed",
            ) from exc
        if (
            dry_run.get("status") != "PASS_COMMITTED_DEADLINE_IDENTITY_DRY_RUN"
            or dry_run.get("attempt") != context.attempt
            or dry_run.get("packet_sha256") != context.receipts.packet_sha256
            or dry_run.get("authorization_sha256") != context.receipts.authorization_sha256
            or dry_run.get("bindings_sha256") != context.bindings_sha256
            or dry_run.get("result", {}).get("status") != "PASS_DEADLINE_IDENTITY_AND_ACCEPTANCE"
        ):
            raise OperationRefusal(
                "COMMITTED_STAGE_ONE_DRY_RUN_BINDING_DIFFERS",
                "committed deadline dry-run receipt differs from execution artifacts",
            )
    preflight = context.bindings.get("scout_real_execution_preflight")
    if context.attempt >= 7:
        if not isinstance(preflight, dict):
            raise OperationRefusal(
                "COMMITTED_SCOUT_PREFLIGHT_ABSENT",
                "attempt 7 or later requires the committed exact-image Scout preflight",
            )
        path = ROOT / str(preflight.get("path", ""))
        try:
            value = json.loads(path.read_bytes())
        except Exception as exc:
            raise OperationRefusal(
                "COMMITTED_SCOUT_PREFLIGHT_MALFORMED",
                "committed exact-image Scout preflight is absent or malformed",
            ) from exc
        if (
            hashlib.sha256(path.read_bytes()).hexdigest() != preflight.get("sha256")
            or value.get("status") != "PASS_EXACT_IMAGE_SCOUT_REAL_EXECUTION_PREFLIGHT"
            or value.get("scope", {}).get("aws_calls") != 0
            or value.get("scope", {}).get("aws_mutations") != 0
            or value.get("scope", {}).get("gpu_started") is not False
            or value.get("image", {}).get("oci_index_digest")
            != context.bindings.get("image", {}).get("oci_index_digest")
            or value.get("image", {}).get("linux_amd64_digest")
            != context.bindings.get("image", {}).get("linux_amd64_digest")
            or value.get("scan", {}).get("status")
            != "PASS_DOCKER_SCOUT_ACCEPTED_RISK_GATE"
        ):
            raise OperationRefusal(
                "COMMITTED_SCOUT_PREFLIGHT_BINDING_DIFFERS",
                "committed exact-image Scout preflight differs from attempt bindings",
            )
    if context.attempt >= 5:
        try:
            from scripts.asr_eval_digest_rescan import validate_scout_prerequisites

            validate_scout_prerequisites()
        except Exception as exc:
            if getattr(exc, "reason_code", None) is not None:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            raise
    context.filesystem_events.append("pre_envelope_prerequisites_passed")
    envelope = write_attempt_envelope(context)
    context.filesystem_events.append("attempt_envelope_persisted")
    stage_hashes: dict[str, str] = {}
    failure_stage: str | None = None
    outcome = "PASS_PILOT"
    aggregate_status = None
    try:
        for stage in STAGES[:-1]:
            try:
                payload = STAGE_FUNCTIONS[stage](ops, context)
                status = "INCOMPLETE_MEASUREMENT" if payload.get("status") == "INCOMPLETE_MEASUREMENT" else "PASS"
                receipt = context.receipts.persist(stage, status, payload)
                context.filesystem_events.append(f"stage_receipt_persisted:{stage}:{status}")
                stage_hashes[stage] = receipt["receipt_sha256"]
                if status == "INCOMPLETE_MEASUREMENT":
                    aggregate_status = status
            except Exception as exc:
                failure_stage = stage
                receipt = context.receipts.persist(stage, "REFUSED", _safe_reason(exc), dependencies=())
                context.filesystem_events.append(f"stage_receipt_persisted:{stage}:REFUSED")
                stage_hashes[stage] = receipt["receipt_sha256"]
                outcome = _refusal_outcome(exc) or OUTCOME_BY_STAGE.get(stage, "FAILED_CLOSED_EXECUTION")
                break
    finally:
        try:
            cleanup = stage_cleanup_and_expiry(ops, context)
            receipt = context.receipts.persist("cleanup_and_expiry", "PASS", cleanup, dependencies=())
            context.filesystem_events.append("stage_receipt_persisted:cleanup_and_expiry:PASS")
            stage_hashes["cleanup_and_expiry"] = receipt["receipt_sha256"]
        except Exception as exc:
            failure_stage = failure_stage or "cleanup_and_expiry"
            receipt = context.receipts.persist("cleanup_and_expiry", "REFUSED", _safe_reason(exc), dependencies=())
            context.filesystem_events.append("stage_receipt_persisted:cleanup_and_expiry:REFUSED")
            stage_hashes["cleanup_and_expiry"] = receipt["receipt_sha256"]
            outcome = "FAILED_CLOSED_EXECUTION"
    if outcome == "PASS_PILOT" and aggregate_status == "INCOMPLETE_MEASUREMENT":
        outcome = "INCOMPLETE_MEASUREMENT"
    result = {
        "schema_version": 1,
        "outcome": outcome,
        "attempt": context.attempt,
        "attempt_envelope_sha256": envelope["sha256"],
        "failure_stage": failure_stage,
        "pre_envelope_local_resources": context.local_resource_validation,
        "stage_receipts": stage_hashes,
        "filesystem_side_effect_order": [
            *context.filesystem_events,
            "terminal_result_persisted",
        ],
    }
    write_exclusive(context.workdir / "result.json", canonical_json(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    bindings = json.loads(args.bindings.read_bytes())
    packet_sha = hashlib.sha256(args.packet.read_bytes()).hexdigest()
    auth_sha = hashlib.sha256(args.authorization.read_bytes()).hexdigest()
    plan = exact_plan(bindings, args.attempt)
    validate_plan(plan, bindings, args.attempt)
    from scripts.asr_base_model_pilot_live import LiveOperations

    context = build_attempt_context(
        root=ROOT,
        workdir=args.workdir,
        attempt=args.attempt,
        bindings=bindings,
        packet_sha256=packet_sha,
        authorization_sha256=auth_sha,
        authorization_path=args.authorization,
        packet_path=args.packet,
        dry_run_path=ROOT / bindings["authorization"]["deadline_dry_run_path"],
        bindings_sha256=hashlib.sha256(args.bindings.read_bytes()).hexdigest(),
    )
    result = execute_attempt(LiveOperations(ROOT), context)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["outcome"] in {"PASS_PILOT", "INCOMPLETE_MEASUREMENT"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
