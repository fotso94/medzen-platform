#!/usr/bin/env python3
"""Cold-rehearse the entire pilot loop against fake AWS and kubectl operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import STAGES, canonical_json, write_exclusive
import pipeline.asr_base_model_pilot_receipts as receipt_module
from scripts.asr_base_model_pilot_fake import FakeOperations
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_live import LiveOperations
from scripts.asr_base_model_pilot_integrity import (
    EXECUTOR_MODULE_PATHS,
    read_committed_artifact,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan
from scripts.asr_base_model_pilot_runner import (
    STAGE_FUNCTIONS,
    build_attempt_context,
    execute_attempt,
    validate_authorization_payload,
    validate_clean_reviewed_worktree,
)
from scripts.asr_eval_digest_rescan import validate_security_binding


SCENARIOS = {
    "clean_pass": (None, "PASS_PILOT"),
    "security_wrong_digest": ("security_wrong_digest", "BLOCKED_IMAGE_SCAN"),
    "security_extra_finding": ("security_extra_finding", "BLOCKED_IMAGE_SCAN"),
    "isolation_probe_refusal": ("private_endpoint_and_policy_gate", "BLOCKED_NETWORK_ISOLATION"),
    "deadline_refusal": ("deadline_identity_and_acceptance", "FAILED_CLOSED_EXECUTION"),
    "cleanup_refusal": ("cleanup_and_expiry", "FAILED_CLOSED_EXECUTION"),
    "prestage_object_absent": ("prestage_object_absent", "FAILED_CLOSED_EXECUTION"),
    "prestage_in_attempt_upload": ("prestage_in_attempt_upload", "FAILED_CLOSED_EXECUTION"),
    "uplink_window_infeasible": ("uplink_window_infeasible", "FAILED_CLOSED_EXECUTION"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _committed_clean_head() -> str:
    return validate_clean_reviewed_worktree(ROOT)


def rehearse(output: Path, bindings_path: Path | None = None) -> dict[str, Any]:
    rehearsal_commit = _committed_clean_head()
    prior_user = os.environ.get("DOCKER_SCOUT_HUB_USER")
    prior_password = os.environ.get("DOCKER_SCOUT_HUB_PASSWORD")
    os.environ["DOCKER_SCOUT_HUB_USER"] = "synthetic-cold-rehearsal"
    os.environ["DOCKER_SCOUT_HUB_PASSWORD"] = "synthetic-cold-rehearsal-secret"
    receipt_module.utc_now = lambda: "2026-08-12T01:00:00Z"
    bindings_path = bindings_path or (
        ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002H.json"
    )
    bindings_body = read_committed_artifact(ROOT, bindings_path)
    bindings = json.loads(bindings_body)
    if bindings["attempts"]["authorized_numbers"] == [9]:
        prestage_path = ROOT / bindings["artifact_prestage_proof"]["path"]
        prestage_body = read_committed_artifact(ROOT, prestage_path)
        if hashlib.sha256(prestage_body).hexdigest() != bindings["artifact_prestage_proof"]["sha256"]:
            raise RuntimeError("committed pre-stage proof hash differs")
        bindings["rehearsal_artifact_prestage_proof"] = json.loads(prestage_body)
    else:
        for name in (
            "prestage_object_absent",
            "prestage_in_attempt_upload",
            "uplink_window_infeasible",
        ):
            SCENARIOS.pop(name, None)
    digest_bindings_path = ROOT / bindings["digest_rescan_bindings"]["path"]
    digest_bindings_body = read_committed_artifact(ROOT, digest_bindings_path)
    if hashlib.sha256(digest_bindings_body).hexdigest() != bindings[
        "digest_rescan_bindings"
    ]["sha256"]:
        raise RuntimeError("committed digest-rescan bindings hash differs")
    security_gate = json.loads(digest_bindings_body)["security_gate"]
    if bindings.get("security_gate") != security_gate:
        raise RuntimeError("pilot and digest-rescan security gates differ")
    security_gate_validation = validate_security_binding(bindings["security_gate"])
    source_integrity = validate_executor_module_bindings(
        ROOT, bindings.get("executor_modules")
    )
    attempt = bindings["attempts"]["authorized_numbers"][0]
    authorization_id = bindings["authorization"]["id"]
    plan_result = validate_plan(exact_plan(bindings, attempt), bindings, attempt)
    workload = render(bindings, ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], attempt)
    workload_result = verify(workload, bindings["image"]["linux_amd64_digest"], attempt)
    authorization_result = validate_authorization_payload(
        {
            "id": authorization_id,
            "status": "owner-approved",
            "packet": {"sha256": "0" * 64},
            "risk_acceptance": {"sha256": "3" * 64},
            "attempts": {
                "authorized_numbers": [attempt],
                "maximum": 1,
                "seconds_each": 10800,
                "non_transferable": True,
            },
        },
        expected_id=authorization_id,
        packet_sha256="0" * 64,
        risk_sha256="3" * 64,
        attempt=attempt,
    )
    scenarios: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="medzen-asr-pilot-cold-") as temporary:
        base = Path(temporary)
        for name, (injection, expected) in SCENARIOS.items():
            directory = base / name
            ops = FakeOperations(inject=injection)
            context = build_attempt_context(
                root=ROOT,
                workdir=directory,
                attempt=attempt,
                bindings=bindings,
                packet_sha256="0" * 64,
                authorization_sha256="a" * 64,
            )
            result = execute_attempt(ops, context)
            if result["outcome"] != expected or not ops.zero_state():
                raise RuntimeError(f"cold rehearsal scenario differs: {name}")
            receipt_files = sorted((directory / "receipts").glob("*.json"))
            failure_receipt = (
                json.loads((directory / f"receipts/{result['failure_stage']}.json").read_bytes())
                if result["failure_stage"] is not None
                else None
            )
            scenarios[name] = {
                "outcome": result["outcome"],
                "failure_stage": result["failure_stage"],
                "failure_reason_code": (
                    failure_receipt["payload"].get("reason_code")
                    if failure_receipt is not None
                    else None
                ),
                "cleanup_status": json.loads((directory / "receipts/cleanup_and_expiry.json").read_bytes())["status"],
                "receipt_count": len(receipt_files),
                "receipt_chain_sha256": hashlib.sha256("".join(_sha(path) for path in receipt_files).encode()).hexdigest(),
                "zero_state": ops.zero_state(),
                "ecr_scan_configuration_put_calls": ops.registry_scanning.put_calls,
                "ecr_scan_configuration_restored": ops.registry_scanning.restored(),
                "filesystem_side_effect_order": result["filesystem_side_effect_order"],
                "external_workdir_classification": "TEMPORARY_EXTERNAL_TO_REVIEWED_WORKTREE",
                "external_to_reviewed_worktree": True,
                "receipt_store_relative_path": "receipts",
            }
    rehearsal_source_paths = [
        ROOT / "scripts/asr_base_model_pilot_cold_rehearsal.py",
        ROOT / "scripts/asr_base_model_pilot_fake.py",
        ROOT / "services/asr-eval-runtime/medzen_asr_eval/pilot.py",
        ROOT / "services/asr-eval-runtime/medzen_asr_eval/network_probe.py",
    ]
    receipt = {
        "schema_version": 1,
        "status": "PASS_COLD_REHEARSAL",
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "aws_mutations": 0,
        "kubernetes_mutations": 0,
        "full_pass_runs": 1,
        "injected_failure_runs": len(SCENARIOS) - 1,
        "injected_paths": [name for name in SCENARIOS if name != "clean_pass"],
        "bindings_source": {
            "path": str(bindings_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(bindings_body).hexdigest(),
            "loaded_from_committed_head": True,
            "fixture_used": False,
        },
        "rehearsal_source_commit": rehearsal_commit,
        f"attempt_{attempt}_security_rehearsal": {
            "aligned_pass": True,
            "wrong_digest_refuses": True,
            "extra_finding_refuses": True,
            "existing_exact_image_upload_skipped": True,
            "registry_scanning_mutations": 0,
        },
        "registry_scanning_boundary": {
            "status": "PASS_NO_REGISTRY_SCANNING_MUTATION",
            "inspector_enhanced_scanning_adopted": False,
            "maximum_put_calls_per_scenario": max(
                value["ecr_scan_configuration_put_calls"] for value in scenarios.values()
            ),
            "ecr_basic_role": "SUPPLEMENTARY_OS_GATE",
            "docker_scout_role": "DIGEST_VERIFIED_PYTHON_PACKAGE_GATE",
        },
        "enumerated_stages": list(STAGES),
        "execution_asset_completeness": {
            stage: {
                "runner": f"scripts.asr_base_model_pilot_runner.{STAGE_FUNCTIONS[stage].__name__}",
                "real_operation": f"scripts.asr_base_model_pilot_live.LiveOperations.{getattr(LiveOperations, stage).__name__}",
                "fake_operation": f"scripts.asr_base_model_pilot_fake.FakeOperations.{getattr(FakeOperations, stage).__name__}",
            }
            for stage in STAGES
        },
        "executor_module_integrity": source_integrity,
        "executor_module_paths": list(bindings["executor_modules"]),
        "security_gate_validation": security_gate_validation,
        "rehearsal_binding_normalization_permitted": False,
        "exact_plan": plan_result,
        "authorization_schema": authorization_result,
        "reviewed_worktree_boundary": {
            "required_head": "packet-bound reviewed commit",
            "required_porcelain_status": "empty",
            "dependency_interpreter_location": "outside reviewed worktree",
            "runner_invocation": "python -m scripts.asr_base_model_pilot_runner",
            "workdir_location": "outside reviewed worktree",
            "receipt_commit_timing": "only after terminal run",
        },
        "fidelity_boundary": {
            "policy": "EVERYTHING_EXCEPT_PAID_EXTERNAL_CALLS",
            "shared_context_builder": "scripts.asr_base_model_pilot_runner.build_attempt_context",
            "shared_execution_runner": "scripts.asr_base_model_pilot_runner.execute_attempt",
            "shared_receipt_store": "pipeline.asr_base_model_pilot_receipts.ReceiptStore",
            "shared_filesystem_side_effect_ordering": True,
            "fake_boundary": ["AWS calls", "kubectl calls"],
            "filesystem_side_effects_faked": False,
        },
        "kubernetes_workload": workload_result,
        "scenarios": scenarios,
        "rehearsal_source_hashes": {
            str(path.relative_to(ROOT)): _sha(path) for path in rehearsal_source_paths
        },
    }
    if prior_user is None:
        os.environ.pop("DOCKER_SCOUT_HUB_USER", None)
    else:
        os.environ["DOCKER_SCOUT_HUB_USER"] = prior_user
    if prior_password is None:
        os.environ.pop("DOCKER_SCOUT_HUB_PASSWORD", None)
    else:
        os.environ["DOCKER_SCOUT_HUB_PASSWORD"] = prior_password
    write_exclusive(output, canonical_json(receipt))
    return {**receipt, "sha256": _sha(output)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--bindings",
        type=Path,
        default=ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002H.json",
    )
    args = parser.parse_args()
    try:
        result = rehearse(args.output.resolve(), args.bindings.resolve())
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "exception_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
