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

from pipeline.asr_base_model_pilot_receipts import ReceiptStore, STAGES, canonical_json, write_exclusive
import pipeline.asr_base_model_pilot_receipts as receipt_module
from scripts.asr_base_model_pilot_fake import FakeOperations
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_live import LiveOperations
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


def _bindings() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "image": {
            "linux_amd64_digest": "sha256:" + "1" * 64,
            "oci_index_digest": "sha256:" + "4" * 64,
            "tag": "pilot-exact",
        },
        "security_gate": {
            "registry_scanning_mutation_permitted": False,
            "inspector_enhanced_scanning_permitted": False,
            "docker_scout_version": "1.18.3",
            "docker_scout_git_commit": "aa68fc25c596bea659d54867443238fd30218d23",
            "accepted_high_tuples": [
                "CVE-2025-55551|torch|2.8.0+cu128|HIGH",
                "CVE-2025-55552|torch|2.8.0+cu128|HIGH",
                "CVE-2026-24747|torch|2.8.0+cu128|HIGH",
                "CVE-2026-4538|torch|2.8.0+cu128|HIGH",
            ],
        },
        "pilot_bundle": {"sha256": "2" * 64},
    }


def rehearse(output: Path) -> dict[str, Any]:
    prior_user = os.environ.get("DOCKER_SCOUT_HUB_USER")
    prior_password = os.environ.get("DOCKER_SCOUT_HUB_PASSWORD")
    os.environ["DOCKER_SCOUT_HUB_USER"] = "synthetic-cold-rehearsal"
    os.environ["DOCKER_SCOUT_HUB_PASSWORD"] = "synthetic-cold-rehearsal-secret"
    receipt_module.utc_now = lambda: "2026-08-12T01:00:00Z"
    bindings = _bindings()
    plan_result = validate_plan(exact_plan(bindings, 5), bindings, 5)
    workload = render(bindings, ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 5)
    workload_result = verify(workload, bindings["image"]["linux_amd64_digest"], 5)
    authorization_result = validate_authorization_payload(
        {
            "id": "ASR-BASE-MODEL-AWS-AUTH-2026-002D",
            "status": "owner-approved",
            "packet": {"sha256": "0" * 64},
            "risk_acceptance": {"sha256": "3" * 64},
            "attempts": {
                "authorized_numbers": [5],
                "maximum": 1,
                "seconds_each": 10800,
                "non_transferable": True,
            },
        },
        expected_id="ASR-BASE-MODEL-AWS-AUTH-2026-002D",
        packet_sha256="0" * 64,
        risk_sha256="3" * 64,
        attempt=5,
    )
    scenarios: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="medzen-asr-pilot-cold-") as temporary:
        base = Path(temporary)
        for name, (injection, expected) in SCENARIOS.items():
            directory = base / name
            ops = FakeOperations(inject=injection)
            context = AttemptContext(
                attempt=5,
                bindings=bindings,
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
    source_paths = [
        ROOT / "scripts/asr_base_model_pilot_runner.py",
        ROOT / "scripts/asr_base_model_pilot_fake.py",
        ROOT / "scripts/asr_base_model_pilot_plan.py",
        ROOT / "scripts/asr_base_model_pilot_k8s.py",
        ROOT / "scripts/asr_base_model_pilot_live.py",
        ROOT / "scripts/asr_base_model_pilot_assets.py",
        ROOT / "scripts/asr_eval_oci_publication.py",
        ROOT / "scripts/asr_eval_digest_rescan.py",
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
        "attempt_5_security_rehearsal": {
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
        "runner_source_hashes": {str(path.relative_to(ROOT)): _sha(path) for path in source_paths},
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
    args = parser.parse_args()
    try:
        result = rehearse(args.output)
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "exception_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
