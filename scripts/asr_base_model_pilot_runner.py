#!/usr/bin/env python3
"""Canonical stage runner for the ASR base-model offline pilot successor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
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


class OperationRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str, *, outcome: str | None = None):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail
        self.outcome = outcome


class Operations(Protocol):
    def deadline_identity_and_acceptance(self, context: "AttemptContext") -> dict[str, Any]: ...
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


def _pass(payload: dict[str, Any], expected: str) -> dict[str, Any]:
    if payload.get("status") != expected:
        raise OperationRefusal("STAGE_RESULT_DIFFERS", f"expected {expected}, got {payload.get('status')}")
    return payload


def stage_deadline_identity_and_acceptance(ops: Operations, context: AttemptContext) -> dict[str, Any]:
    return _pass(ops.deadline_identity_and_acceptance(context), "PASS_DEADLINE_IDENTITY_AND_ACCEPTANCE")


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
    detail = getattr(exc, "detail", type(exc).__name__)
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
    if context.attempt not in {1, 2} or context.deadline_seconds != 10800:
        raise OperationRefusal("ATTEMPT_BOUNDARY_DIFFERS", "only attempts 1/2 at 10800 seconds are permitted")
    context.workdir.mkdir(parents=True, exist_ok=True)
    envelope = write_attempt_envelope(context)
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
                stage_hashes[stage] = receipt["receipt_sha256"]
                if status == "INCOMPLETE_MEASUREMENT":
                    aggregate_status = status
            except Exception as exc:
                failure_stage = stage
                receipt = context.receipts.persist(stage, "REFUSED", _safe_reason(exc), dependencies=())
                stage_hashes[stage] = receipt["receipt_sha256"]
                outcome = _refusal_outcome(exc) or OUTCOME_BY_STAGE.get(stage, "FAILED_CLOSED_EXECUTION")
                break
    finally:
        try:
            cleanup = stage_cleanup_and_expiry(ops, context)
            receipt = context.receipts.persist("cleanup_and_expiry", "PASS", cleanup, dependencies=())
            stage_hashes["cleanup_and_expiry"] = receipt["receipt_sha256"]
        except Exception as exc:
            failure_stage = failure_stage or "cleanup_and_expiry"
            receipt = context.receipts.persist("cleanup_and_expiry", "REFUSED", _safe_reason(exc), dependencies=())
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
        "stage_receipts": stage_hashes,
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

    context = AttemptContext(
        attempt=args.attempt,
        bindings=bindings,
        receipts=ReceiptStore(args.workdir / "receipts", packet_sha256=packet_sha, authorization_sha256=auth_sha),
        workdir=args.workdir,
        authorization_path=args.authorization,
        packet_path=args.packet,
    )
    result = execute_attempt(LiveOperations(ROOT), context)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["outcome"] in {"PASS_PILOT", "INCOMPLETE_MEASUREMENT"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
