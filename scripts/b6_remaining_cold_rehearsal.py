#!/usr/bin/env python3
"""Deterministic no-AWS rehearsal for packet 2026-032 remaining proofs."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import ReceiptStore, canonical_json, sha256_file
from scripts.b6_6_runner import RunContext, StageFailure, StageResult
from scripts.b6_remaining_bindings import (
    COLD_PATH,
    FILE_RECEIPT_PATH,
    NEW_ORCHESTRATOR_DIGEST,
    REQUIRED_SOURCES,
    SCAN_RESULT_PATH,
)
from scripts.b6_remaining_runner import (
    REMAINING_EXECUTION_STAGES,
    REMAINING_WINDOW_STAGES,
    RemainingRunner,
)

OLD_ORCHESTRATOR_DIGEST = (
    "sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa"
)
GUARDS = {
    "stage0": ["hash_bound_authorization", "fresh_synthetic_credential", "preserved_file_proof_binding", "worker_capacity_zero", "synthetic_deployments_zero", "window_alb_absent"],
    "deadline": ["deadline_first_4500_seconds"],
    "workers_ready": ["bounded_worker_registration"],
    "dra_ready": ["digest_pinned_dra_before_endpoints"],
    "rag_ready": ["digest_pinned_rag_before_endpoints"],
    "asr_ready": ["digest_pinned_loader_and_asr_before_endpoints"],
    "tts_ready": ["digest_pinned_tts_before_endpoints"],
    "llm_ready": ["digest_pinned_llm_before_endpoints"],
    "orchestrator_ready": ["scan_passed_websocket_orchestrator_before_endpoints"],
    "controller_window": ["controller_plan_exact"],
    "controller_ready": ["digest_pinned_controller_before_endpoints"],
    "pre_endpoint_images": ["seven_pods_eight_child_digests_resident"],
    "terraform_window": ["temporary_endpoint_plan_exact"],
    "endpoints_ready": ["private_probe_endpoints_stable"],
    "alb_ready": ["hostname_active_and_stable_healthy_target"],
    "fargate_probe": ["private_readiness_probe"],
    "alb_tag_mutation_warning": ["bounded_nonfatal_tag_mutation_rule"],
    "websocket_proof": ["real_streaming_contract"],
    "cancellation_proof": ["cancel_within_250ms"],
    "failure_drills": ["dependency_refusal_without_pod_recreation"],
    "isolation_proof": ["orchestrator_only_ingress_dependencies_clusterip"],
    "cleanup": ["status_keyed_three_stable_zero_observations"],
}


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> str:
        result = self.value.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.value += timedelta(seconds=1)
        return result


class FakeOperations:
    def __init__(self, fail_stage: str | None) -> None:
        self.fail_stage = fail_stage
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
            context.token_file.write_bytes(bytes(range(32)))
            payload = {
                "cold_rehearsal": True,
                "fresh_synthetic_credential": True,
                "preserved_file_proof": {
                    "status": "PASS",
                    "sha256": "808d160e391998e3f534d8776342e58337ebb4a200ffaab58fcc43e586c60c89",
                    "rerun": False,
                },
            }
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
                "INJECTED_REMAINING_PROOFS_FAILURE",
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
    runner = RemainingRunner(operations, ReceiptStore(directory, clock=Clock()))
    result = runner.run(context)
    receipts = [
        {
            "stage": stage,
            "status": runner.store.load(stage)["status"],
            "sha256": sha256_file(runner.store.path(stage)),
        }
        for stage in REMAINING_WINDOW_STAGES
        if runner.store.path(stage).exists()
    ]
    if runner.store.path("file_proof").exists():
        raise AssertionError("remaining-proofs runner attempted to persist file_proof")
    if fail_stage is None:
        if (
            result.outcome != "PASS"
            or [item["stage"] for item in receipts] != list(REMAINING_WINDOW_STAGES)
            or any(item["status"] != "PASS" for item in receipts)
        ):
            raise AssertionError("remaining-proofs full rehearsal differs")
    else:
        refused = [item["stage"] for item in receipts if item["status"] == "REFUSED"]
        if result.outcome != "REFUSED" or refused != [fail_stage]:
            raise AssertionError(f"injected failure did not refuse exactly {fail_stage}")
        cleanup = runner.store.load("cleanup")
        expected_cleanup = "REFUSED" if fail_stage == "cleanup" else "PASS"
        if cleanup["status"] != expected_cleanup or not operations.zero_state:
            raise AssertionError("injected refusal did not reach exact cleanup")
    invoked = set(operations.guards_invoked)
    if fail_stage is None:
        expected = set(REMAINING_WINDOW_STAGES)
    else:
        expected = set(
            REMAINING_WINDOW_STAGES[: REMAINING_WINDOW_STAGES.index(fail_stage) + 1]
        )
        if fail_stage != "cleanup":
            expected.add("cleanup")
    if invoked != expected:
        raise AssertionError("remaining-proofs guard invocation set differs")
    return {
        "scenario": name,
        "injected_failure_stage": fail_stage,
        "outcome": result.outcome,
        "failure_stage": result.failure_stage,
        "cleanup_complete": operations.zero_state,
        "file_proof_receipt_created": False,
        "guards_invoked": operations.guards_invoked,
        "receipts": receipts,
        "real_aws_calls": operations.real_aws_calls,
        "real_kubectl_calls": operations.real_kubectl_calls,
        "platform_mutations": operations.platform_mutations,
    }


def _immutable_reuse_and_digest_audit() -> dict[str, Any]:
    scan = json.loads((ROOT / SCAN_RESULT_PATH).read_bytes())
    file_receipt = json.loads((ROOT / FILE_RECEIPT_PATH).read_bytes())
    source_paths = (
        "platform/k8s/b6-6/remaining-proofs-window.yaml",
        "scripts/b6_remaining_operations.sh",
        "scripts/b6_remaining_pre_endpoint_images.py",
    )
    for relative in source_paths:
        text = (ROOT / relative).read_text()
        if NEW_ORCHESTRATOR_DIGEST not in text or OLD_ORCHESTRATOR_DIGEST in text:
            raise AssertionError(f"remaining-proofs orchestrator digest drift: {relative}")
    operations = (ROOT / "scripts/b6_remaining_operations.sh").read_text()
    if "stage_file_proof" in operations or "file_proof)" in operations:
        raise AssertionError("remaining dispatcher still exposes file proof")
    if (
        scan.get("outcome") != "PASS_SCAN_ONLY"
        or scan.get("subject", {}).get("child_manifest_digest")
        != NEW_ORCHESTRATOR_DIGEST
        or scan.get("authoritative_scan", {}).get("finding_count") != 0
        or file_receipt.get("stage") != "file_proof"
        or file_receipt.get("status") != "PASS"
        or file_receipt.get("payload", {}).get("http_status") != 200
    ):
        raise AssertionError("immutable predecessor binding differs")
    return {
        "status": "PASS",
        "scan_result_path": SCAN_RESULT_PATH,
        "scan_result_sha256": sha256_file(ROOT / SCAN_RESULT_PATH),
        "scan_outcome": "PASS_SCAN_ONLY",
        "scan_findings": 0,
        "orchestrator_child_manifest_digest": NEW_ORCHESTRATOR_DIGEST,
        "file_proof_path": FILE_RECEIPT_PATH,
        "file_proof_sha256": sha256_file(ROOT / FILE_RECEIPT_PATH),
        "file_proof_status": "PASS",
        "file_proof_rerun": False,
        "digest_projection_count": len(source_paths),
        "old_digest_projection_count": 0,
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
    }


def run(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"cold rehearsal output already exists: {output_dir}")
    reuse = _immutable_reuse_and_digest_audit()
    with tempfile.TemporaryDirectory(prefix="medzen-b6-032-cold-") as temporary:
        root = Path(temporary)
        scenarios = [_scenario(root, "full-pass", None)]
        scenarios.extend(
            _scenario(root, f"fail-{index:02d}-{stage}", stage)
            for index, stage in enumerate(REMAINING_WINDOW_STAGES, start=1)
        )
    results_sha256 = hashlib.sha256(
        canonical_json({"window": scenarios, "immutable_reuse": reuse})
    ).hexdigest()
    hashable_sources = sorted(REQUIRED_SOURCES - {COLD_PATH})
    source_hashes = {
        relative: sha256_file(ROOT / relative) for relative in hashable_sources
    }
    payload = {
        "status": "PASS_COLD_REHEARSAL",
        "packet": "B6-AWS-CHANGE-PACKET-2026-032",
        "full_pass_runs": 1,
        "injected_failure_runs": len(REMAINING_WINDOW_STAGES),
        "enumerated_execution_stages": list(REMAINING_EXECUTION_STAGES),
        "enumerated_receipt_stages": list(REMAINING_WINDOW_STAGES),
        "remaining_live_proofs": [
            "websocket_proof",
            "cancellation_proof",
            "failure_drills",
            "isolation_proof",
        ],
        "preserved_proofs_not_executed": ["file_proof"],
        "file_proof_receipts_created": 0,
        "immutable_reuse_and_digest_audit": reuse,
        "scenario_results_sha256": results_sha256,
        "scenarios": scenarios,
        "runner_source_hashes": source_hashes,
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "aws_mutations": 0,
        "kubernetes_mutations": 0,
    }
    store = ReceiptStore(output_dir, clock=Clock())
    return store.persist("cold_rehearsal", "PASS", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = run(args.output_dir.resolve())
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"REFUSING: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
