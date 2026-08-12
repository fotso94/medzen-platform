#!/usr/bin/env python3
"""Cold-rehearse the entire pilot loop against fake AWS and kubectl operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import ReceiptStore, STAGES, canonical_json, write_exclusive
from scripts.asr_base_model_pilot_fake import FakeOperations
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_live import LiveOperations
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan
from scripts.asr_base_model_pilot_runner import AttemptContext, STAGE_FUNCTIONS, execute_attempt


SCENARIOS = {
    "clean_pass": (None, "PASS_PILOT"),
    "isolation_probe_refusal": ("private_endpoint_and_policy_gate", "BLOCKED_NETWORK_ISOLATION"),
    "deadline_refusal": ("deadline_identity_and_acceptance", "FAILED_CLOSED_EXECUTION"),
    "cleanup_refusal": ("cleanup_and_expiry", "FAILED_CLOSED_EXECUTION"),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bindings() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "image": {"linux_amd64_digest": "sha256:" + "1" * 64},
        "pilot_bundle": {"sha256": "2" * 64},
    }


def rehearse(output: Path) -> dict[str, Any]:
    bindings = _bindings()
    plan_result = validate_plan(exact_plan(bindings, 1), bindings, 1)
    workload = render(bindings, ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 1)
    workload_result = verify(workload, bindings["image"]["linux_amd64_digest"], 1)
    scenarios: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="medzen-asr-pilot-cold-") as temporary:
        base = Path(temporary)
        for name, (injection, expected) in SCENARIOS.items():
            directory = base / name
            ops = FakeOperations(inject=injection)
            context = AttemptContext(
                attempt=1,
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
            }
    source_paths = [
        ROOT / "scripts/asr_base_model_pilot_runner.py",
        ROOT / "scripts/asr_base_model_pilot_fake.py",
        ROOT / "scripts/asr_base_model_pilot_plan.py",
        ROOT / "scripts/asr_base_model_pilot_k8s.py",
        ROOT / "scripts/asr_base_model_pilot_live.py",
        ROOT / "scripts/asr_base_model_pilot_assets.py",
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
        "injected_failure_runs": 3,
        "injected_paths": ["isolation_probe", "deadline", "cleanup"],
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
        "kubernetes_workload": workload_result,
        "scenarios": scenarios,
        "runner_source_hashes": {str(path.relative_to(ROOT)): _sha(path) for path in source_paths},
    }
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
