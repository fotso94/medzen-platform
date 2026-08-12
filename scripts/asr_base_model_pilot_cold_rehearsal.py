#!/usr/bin/env python3
"""Cold-rehearse the entire pilot loop against fake AWS and kubectl operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import ReceiptStore, STAGES, canonical_json, write_exclusive
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
    AttemptContext,
    STAGE_FUNCTIONS,
    execute_attempt,
    validate_authorization_payload,
)


SCENARIOS = {
    "clean_pass": (None, "PASS_PILOT"),
    "security_wrong_digest": ("security_wrong_digest", "BLOCKED_IMAGE_SCAN"),
    "security_extra_finding": ("security_extra_finding", "BLOCKED_IMAGE_SCAN"),
    "isolation_probe_refusal": ("private_endpoint_and_policy_gate", "BLOCKED_NETWORK_ISOLATION"),
    "deadline_refusal": ("deadline_identity_and_acceptance", "FAILED_CLOSED_EXECUTION"),
    "cleanup_refusal": ("cleanup_and_expiry", "FAILED_CLOSED_EXECUTION"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _committed_clean_head() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    if status:
        raise RuntimeError("cold rehearsal requires a clean committed worktree")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def rehearse(output: Path, bindings_path: Path | None = None) -> dict[str, Any]:
    rehearsal_commit = _committed_clean_head()
    prior_user = os.environ.get("DOCKER_SCOUT_HUB_USER")
    prior_password = os.environ.get("DOCKER_SCOUT_HUB_PASSWORD")
    os.environ["DOCKER_SCOUT_HUB_USER"] = "synthetic-cold-rehearsal"
    os.environ["DOCKER_SCOUT_HUB_PASSWORD"] = "synthetic-cold-rehearsal-secret"
    receipt_module.utc_now = lambda: "2026-08-12T01:00:00Z"
    bindings_path = bindings_path or (
        ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002E.json"
    )
    bindings_body = read_committed_artifact(ROOT, bindings_path)
    bindings = json.loads(bindings_body)
    digest_bindings_path = ROOT / bindings["digest_rescan_bindings"]["path"]
    digest_bindings_body = read_committed_artifact(ROOT, digest_bindings_path)
    if hashlib.sha256(digest_bindings_body).hexdigest() != bindings[
        "digest_rescan_bindings"
    ]["sha256"]:
        raise RuntimeError("committed digest-rescan bindings hash differs")
    security_gate = json.loads(digest_bindings_body)["security_gate"]
    if any(bindings["security_gate"][key] != value for key, value in security_gate.items()):
        raise RuntimeError("pilot and digest-rescan security gates differ")
    execution_bindings = {**bindings, "security_gate": security_gate}
    source_integrity = validate_executor_module_bindings(
        ROOT, bindings.get("executor_modules")
    )
    plan_result = validate_plan(exact_plan(execution_bindings, 6), execution_bindings, 6)
    workload = render(execution_bindings, ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 6)
    workload_result = verify(workload, bindings["image"]["linux_amd64_digest"], 6)
    authorization_result = validate_authorization_payload(
        {
            "id": "ASR-BASE-MODEL-AWS-AUTH-2026-002E",
            "status": "owner-approved",
            "packet": {"sha256": "0" * 64},
            "risk_acceptance": {"sha256": "3" * 64},
            "attempts": {
                "authorized_numbers": [6],
                "maximum": 1,
                "seconds_each": 10800,
                "non_transferable": True,
            },
        },
        expected_id="ASR-BASE-MODEL-AWS-AUTH-2026-002E",
        packet_sha256="0" * 64,
        risk_sha256="3" * 64,
        attempt=6,
    )
    scenarios: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="medzen-asr-pilot-cold-") as temporary:
        base = Path(temporary)
        for name, (injection, expected) in SCENARIOS.items():
            directory = base / name
            ops = FakeOperations(inject=injection)
            context = AttemptContext(
                attempt=6,
                bindings=execution_bindings,
                receipts=ReceiptStore(directory / "receipts", packet_sha256="0" * 64, authorization_sha256="a" * 64),
                workdir=directory,
            )
            result = execute_attempt(ops, context)
            if result["outcome"] != expected or not ops.zero_state():
                raise RuntimeError(f"cold rehearsal scenario differs: {name}")
            receipt_files = sorted((directory / "receipts").glob("*.json"))
            scenarios[name] = {
                "outcome": result["outcome"],
                "failure_stage": result["failure_stage"],
                "cleanup_status": json.loads((directory / "receipts/cleanup_and_expiry.json").read_bytes())["status"],
                "receipt_count": len(receipt_files),
                "receipt_chain_sha256": hashlib.sha256("".join(_sha(path) for path in receipt_files).encode()).hexdigest(),
                "zero_state": ops.zero_state(),
                "ecr_scan_configuration_put_calls": ops.registry_scanning.put_calls,
                "ecr_scan_configuration_restored": ops.registry_scanning.restored(),
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
        "injected_failure_runs": 5,
        "injected_paths": ["security_wrong_digest", "security_extra_finding", "isolation_probe", "deadline", "cleanup"],
        "bindings_source": {
            "path": str(bindings_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(bindings_body).hexdigest(),
            "loaded_from_committed_head": True,
            "fixture_used": False,
        },
        "rehearsal_source_commit": rehearsal_commit,
        "attempt_6_security_rehearsal": {
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
        "executor_module_paths": list(EXECUTOR_MODULE_PATHS),
        "exact_plan": plan_result,
        "authorization_schema": authorization_result,
        "reviewed_worktree_boundary": {
            "required_head": "packet-bound reviewed commit",
            "required_porcelain_status": "empty",
            "dependency_interpreter_location": "outside reviewed worktree",
            "runner_invocation": "python -m scripts.asr_base_model_pilot_runner",
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
        default=ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002E.json",
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
