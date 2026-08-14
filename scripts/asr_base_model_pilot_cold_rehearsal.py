#!/usr/bin/env python3
"""Cold-rehearse the real ASR pilot composition with boundary fakes only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline.asr_base_model_pilot_receipts as receipt_module
from pipeline.asr_base_model_pilot_receipts import STAGES, canonical_json, write_exclusive
from scripts.asr_base_model_pilot_fake import (
    assert_no_parallel_stage_implementation,
    build_rehearsal_operations,
)
from scripts.asr_base_model_aws_read_fixtures import (
    FixtureCatalog,
    validate_dynamic_paths,
)
from scripts.asr_base_model_pilot_integrity import (
    read_committed_artifact,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_live import LiveOperations
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan
from scripts.asr_base_model_proven_commands import validate_proven_command_bindings
from scripts.asr_base_model_pilot_runner import (
    STAGE_FUNCTIONS,
    AttemptContext,
    OperationRefusal,
    build_attempt_context,
    execute_attempt,
    stage_artifact_stage,
    validate_authorization_payload,
    validate_clean_reviewed_worktree,
)
from scripts.asr_base_model_pilot_staging import (
    StagingRefusal,
    validate_prestage_proof,
    validate_window_budget,
)
from scripts.asr_base_model_boundary_contracts import audit_bounded_helper_calls
from scripts.asr_eval_digest_rescan import validate_security_binding


SCENARIOS = {
    "clean_pass": (None, "PASS_PILOT"),
    "gpu_node_delayed_ready": ("gpu_node_delayed_ready", "PASS_PILOT"),
    "gpu_node_never_ready": ("gpu_node_never_ready", "FAILED_CLOSED_EXECUTION"),
    "dra_not_ready": ("dra_not_ready", "FAILED_CLOSED_EXECUTION"),
    "sampler_driver_library_missing": (
        "sampler_driver_library_missing",
        "FAILED_CLOSED_EXECUTION",
    ),
    "node_staging_unknown_user": (
        "node_staging_unknown_user",
        "FAILED_CLOSED_EXECUTION",
    ),
    "pilot_job_refused": ("pilot_job_refused", "FAILED_CLOSED_EXECUTION"),
    "security_wrong_digest": ("security_wrong_digest", "BLOCKED_IMAGE_SCAN"),
    "security_extra_finding": ("security_extra_finding", "BLOCKED_IMAGE_SCAN"),
    "isolation_probe_refusal": ("private_endpoint_and_policy_gate", "BLOCKED_NETWORK_ISOLATION"),
    "endpoint_policy_missing_version_action": (
        "endpoint_policy_missing_version_action",
        "BLOCKED_NETWORK_ISOLATION",
    ),
    "deadline_refusal": ("deadline_identity_and_acceptance", "FAILED_CLOSED_EXECUTION"),
    "cleanup_refusal": ("cleanup_and_expiry", "FAILED_CLOSED_EXECUTION"),
    "prestage_object_absent": ("prestage_object_absent", "FAILED_CLOSED_EXECUTION"),
}


def _resource_snapshot(bindings: dict[str, Any], *, sufficient: bool) -> dict[str, Any]:
    policy = bindings["local_resource_policy"]
    required = max(
        policy["disk"]["operating_floor_bytes"],
        policy["disk"]["exact_archive_bytes"]
        + policy["disk"]["scanner_scratch_reserve_bytes"]
        + policy["disk"]["evidence_reserve_bytes"]
        + policy["disk"]["safety_margin_bytes"],
    )
    return {
        "schema_version": 1,
        "disk": {
            "measured_path": "<external-workdir-parent>",
            "total_bytes": required * 4,
            "available_bytes": required + 1024 if sufficient else required - 1,
        },
        "memory": {"physical_bytes": policy["minimum_memory_bytes"]},
        "cpu": {"logical_count": policy["minimum_logical_cpus"]},
        "process_limits": {
            "open_files": {"soft": policy["minimum_open_files_soft"], "hard": policy["minimum_open_files_soft"]},
            "processes": {"soft": policy["minimum_processes_soft"], "hard": policy["minimum_processes_soft"]},
        },
        "commands": {name: f"/synthetic/bin/{name}" for name in ("aws", "docker", "git", "kubectl")},
        "environment": {
            "home_present": True,
            "workdir_parent_writable": True,
            "scout_user_present": True,
            "scout_password_present": True,
            "credential_values_recorded": False,
        },
        "docker": {"daemon_reachable": True, "server_version_present": True},
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_receipt_chain(paths: list[Path], workdir: Path) -> str:
    """Hash receipt semantics without temporary-path or wall-clock noise."""

    def normalize(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            if key == "dependencies":
                return {
                    item: "<normalized-dependency-receipt-sha256>"
                    for item in value
                }
            return {item: normalize(body, item) for item, body in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if key == "deadline_utc":
            return "<bounded-deadline-utc>"
        if isinstance(value, str):
            return value.replace(str(workdir), "<external-workdir>")
        return value

    measured = hashlib.sha256()
    for path in paths:
        measured.update(hashlib.sha256(canonical_json(normalize(json.loads(path.read_bytes())))).digest())
    return measured.hexdigest()


def _committed_clean_head(*, output: Path | None = None) -> str:
    """Allow only the committed output receipt to be absent while replacing it."""
    if output is not None:
        relative = output.resolve().relative_to(ROOT.resolve())
        status_lines = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if status_lines == [f" D {relative}"]:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
    return validate_clean_reviewed_worktree(ROOT)


def _wrapper_contract(bindings: dict[str, Any], directory: Path) -> dict[str, Any]:
    operations, state = build_rehearsal_operations(bindings)
    context = AttemptContext(
        attempt=bindings["attempts"]["authorized_numbers"][0],
        bindings=bindings,
        receipts=receipt_module.ReceiptStore(
            directory / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=directory,
    )
    directory.mkdir(parents=True)
    payload = stage_artifact_stage(operations, context)
    if (
        payload.get("status") != "PASS_ARTIFACT_STAGE"
        or payload.get("verification", {}).get("status")
        != "PASS_PRESTAGED_BUNDLE_VERIFY_ONLY"
        or payload.get("artifact_upload_bytes") != 0
        or not state.zero_state()
    ):
        raise AssertionError("artifact wrapper contract differs")
    return {
        "status": "PASS_ARTIFACT_WRAPPER_CONTRACT",
        "outer_status": payload["status"],
        "nested_verification_status": payload["verification"]["status"],
        "artifact_upload_bytes": payload["artifact_upload_bytes"],
    }


def _pure_prestage_injections(proof: dict[str, Any], bundle_sha: str) -> dict[str, Any]:
    results = {}
    in_attempt = json.loads(json.dumps(proof))
    in_attempt["timed_window"]["in_attempt_upload_bytes"] = 1
    try:
        validate_prestage_proof(in_attempt, expected_bundle_sha256=bundle_sha)
    except StagingRefusal as exc:
        results["prestage_in_attempt_upload"] = exc.reason_code
    else:
        raise AssertionError("in-attempt upload injection passed")

    infeasible = json.loads(json.dumps(proof))
    infeasible["timed_window"]["estimated_fast_stage_seconds"] = 10_000
    try:
        validate_window_budget(
            infeasible,
            deadline_seconds=10800,
            expected_bundle_sha256=bundle_sha,
        )
    except StagingRefusal as exc:
        results["uplink_window_infeasible"] = exc.reason_code
    else:
        raise AssertionError("infeasible window injection passed")
    return {
        "status": "PASS_PURE_INPUT_REFUSAL_CHECKS",
        "refusals": results,
        "stage_implementation_replaced": False,
    }


def _scenario_repository(
    base: Path,
    *,
    bindings_path: Path,
    bindings_body: bytes,
    packet_relative: str,
    authorization_relative: str,
    dry_relative: str,
) -> tuple[Path, Path, Path]:
    """Commit exact synthetic governance artifacts for the real stage-one gate."""
    root = base / "reviewed"
    root.mkdir(parents=True)
    commit_environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-08-13T03:00:00Z",
        "GIT_COMMITTER_DATE": "2026-08-13T03:00:00Z",
    }
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "MedZen Rehearsal"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "rehearsal@medzen.invalid"], cwd=root, check=True)
    bindings = json.loads(bindings_body)
    tracked = {
        str(bindings_path.relative_to(ROOT)): bindings_body,
        "platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001.json": (
            ROOT / "platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001.json"
        ).read_bytes(),
        bindings["cost_registry"]["path"]: (
            ROOT / bindings["cost_registry"]["path"]
        ).read_bytes(),
        bindings["local_resource_policy"]["path"]: (
            ROOT / bindings["local_resource_policy"]["path"]
        ).read_bytes(),
        bindings["local_resource_qualification"]["path"]: (
            ROOT / bindings["local_resource_qualification"]["path"]
        ).read_bytes(),
        "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml": (
            ROOT / "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml"
        ).read_bytes(),
        bindings["dra_network_policy"]["path"]: (
            ROOT / bindings["dra_network_policy"]["path"]
        ).read_bytes(),
        "services/asr-eval-runtime/assets/language-conditioning-v1.json": (
            ROOT / "services/asr-eval-runtime/assets/language-conditioning-v1.json"
        ).read_bytes(),
        "scripts/audit_asr_base_model_eval_inputs.py": (
            ROOT / "scripts/audit_asr_base_model_eval_inputs.py"
        ).read_bytes(),
        bindings["aws_read_fixtures"]["path"]: (
            ROOT / bindings["aws_read_fixtures"]["path"]
        ).read_bytes(),
    }
    if "gpu_node_readiness_fixtures" in bindings:
        relative = bindings["gpu_node_readiness_fixtures"]["path"]
        tracked[relative] = (ROOT / relative).read_bytes()
    if "proven_live_node_commands" in bindings:
        sampler = bindings["proven_live_node_commands"]["sampler"]
        tracked[sampler["receipt_path"]] = (
            ROOT / sampler["receipt_path"]
        ).read_bytes()
    fixture_record = json.loads(
        (ROOT / bindings["aws_read_fixtures"]["path"]).read_bytes()
    )
    for capture in fixture_record["captures"]:
        tracked[capture["path"]] = (ROOT / capture["path"]).read_bytes()
    for relative in bindings["executor_modules"]:
        tracked[relative] = (ROOT / relative).read_bytes()
    tracked[packet_relative] = b"synthetic committed rehearsal packet\n"
    for relative, body in tracked.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "reviewed source"],
        cwd=root,
        check=True,
        env=commit_environment,
    )
    reviewed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    authorization_path = root / authorization_relative
    packet_path = root / packet_relative
    dry_path = root / dry_relative
    packet_sha256 = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    authorization = {
        "id": bindings["authorization"]["id"],
        "status": "owner-approved",
        "expires_utc": "2099-01-01T00:00:00Z",
        "packet": {"sha256": packet_sha256},
        "risk_acceptance": {"sha256": bindings["risk_acceptance_sha256"]},
        "attempts": {
            "authorized_numbers": bindings["attempts"]["authorized_numbers"],
            "maximum": 1,
            "seconds_each": 10800,
            "non_transferable": True,
        },
        "reviewed_repository_commit": reviewed,
        "pre_execution_dry_run": {"path": str(dry_path.relative_to(root))},
    }
    authorization_path.parent.mkdir(parents=True, exist_ok=True)
    authorization_path.write_bytes(canonical_json(authorization))
    dry_path.parent.mkdir(parents=True, exist_ok=True)
    dry_path.write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "authorization and dry run"],
        cwd=root,
        check=True,
        env=commit_environment,
    )
    return root, authorization_path, packet_path


def rehearse(output: Path, bindings_path: Path | None = None) -> dict[str, Any]:
    _committed_clean_head(output=output)
    prior_user = os.environ.get("DOCKER_SCOUT_HUB_USER")
    prior_password = os.environ.get("DOCKER_SCOUT_HUB_PASSWORD")
    os.environ["DOCKER_SCOUT_HUB_USER"] = "synthetic-cold-rehearsal"
    os.environ["DOCKER_SCOUT_HUB_PASSWORD"] = "synthetic-cold-rehearsal-secret"
    receipt_module.utc_now = lambda: "2026-08-13T03:00:00Z"
    bindings_path = bindings_path or (
        ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002P.json"
    )
    bindings_body = read_committed_artifact(ROOT, bindings_path)
    bindings = json.loads(bindings_body)
    fixture_catalog = FixtureCatalog(ROOT, bindings["aws_read_fixtures"])
    aws_read_fixture_coverage = fixture_catalog.summary()
    aws_read_dynamic_paths = validate_dynamic_paths(fixture_catalog)
    gpu_fixture_binding = bindings.get("gpu_node_readiness_fixtures")
    if not isinstance(gpu_fixture_binding, dict):
        raise RuntimeError("GPU-node readiness fixture binding is absent")
    gpu_fixture_path = ROOT / gpu_fixture_binding["path"]
    gpu_fixture_body = read_committed_artifact(ROOT, gpu_fixture_path)
    if hashlib.sha256(gpu_fixture_body).hexdigest() != gpu_fixture_binding["sha256"]:
        raise RuntimeError("committed GPU-node readiness fixture hash differs")
    gpu_fixture = json.loads(gpu_fixture_body)
    if gpu_fixture.get("status") != "PASS_READ_ONLY_LIVE_GPU_NODE_TRANSITION_CAPTURE":
        raise RuntimeError("committed GPU-node readiness fixture status differs")
    rehearsal_commit = bindings["executor_source_commit"]
    digest_bindings_path = ROOT / bindings["digest_rescan_bindings"]["path"]
    digest_bindings_body = read_committed_artifact(ROOT, digest_bindings_path)
    if hashlib.sha256(digest_bindings_body).hexdigest() != bindings[
        "digest_rescan_bindings"
    ]["sha256"]:
        raise RuntimeError("committed digest-rescan bindings hash differs")
    if bindings.get("security_gate") != json.loads(digest_bindings_body)["security_gate"]:
        raise RuntimeError("pilot and digest-rescan security gates differ")
    security_gate_validation = validate_security_binding(bindings["security_gate"])
    source_integrity = validate_executor_module_bindings(
        ROOT, bindings.get("executor_modules")
    )
    proven_command_bindings = validate_proven_command_bindings(
        ROOT, bindings.get("proven_live_node_commands")
    )
    boundary_contract_audit = audit_bounded_helper_calls(ROOT)
    real_only = assert_no_parallel_stage_implementation()
    attempt = bindings["attempts"]["authorized_numbers"][0]
    plan_result = validate_plan(exact_plan(bindings, attempt), bindings, attempt)
    workload = render(bindings, ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], attempt)
    workload_result = verify(workload, bindings["image"]["linux_amd64_digest"], attempt)
    authorization_result = validate_authorization_payload(
        {
            "id": bindings["authorization"]["id"],
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
        expected_id=bindings["authorization"]["id"],
        packet_sha256="0" * 64,
        risk_sha256="3" * 64,
        attempt=attempt,
    )
    proof_binding = bindings["artifact_prestage_proof"]
    proof_body = read_committed_artifact(ROOT, ROOT / proof_binding["path"])
    if hashlib.sha256(proof_body).hexdigest() != proof_binding["sha256"]:
        raise RuntimeError("committed pre-stage proof hash differs")
    proof = json.loads(proof_body)

    scenarios: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="medzen-asr-pilot-cold-") as temporary:
        base = Path(temporary)
        wrapper = _wrapper_contract(bindings, base / "wrapper-contract")
        for name, (injection, expected) in SCENARIOS.items():
            directory = base / name
            reviewed, authorization_path, packet_path = _scenario_repository(
                base / f"{name}-repo",
                bindings_path=bindings_path,
                bindings_body=bindings_body,
                packet_relative=bindings["successor_packet"]["path"],
                authorization_relative=bindings["authorization"]["path"],
                dry_relative=bindings["authorization"]["deadline_dry_run_path"],
            )
            scenario_bindings = json.loads(json.dumps(bindings))
            scenario_bindings["authorization"]["path"] = str(
                authorization_path.relative_to(reviewed)
            )
            scenario_bindings["authorization"]["id"] = bindings["authorization"]["id"]
            scenario_bindings["authorization"]["deadline_dry_run_path"] = str(
                (reviewed / bindings["authorization"]["deadline_dry_run_path"]).relative_to(reviewed)
            )
            scenario_bindings["artifact_prestage_proof"]["path"] = "platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001.json"
            operations, boundary = build_rehearsal_operations(
                scenario_bindings, injection=injection, root=reviewed
            )
            packet_hash = hashlib.sha256(packet_path.read_bytes()).hexdigest()
            authorization_hash = hashlib.sha256(authorization_path.read_bytes()).hexdigest()
            context = build_attempt_context(
                root=reviewed,
                workdir=directory,
                attempt=attempt,
                bindings=scenario_bindings,
                packet_sha256=packet_hash,
                authorization_sha256=authorization_hash,
                authorization_path=authorization_path,
                packet_path=packet_path,
                local_resource_snapshot=_resource_snapshot(
                    scenario_bindings, sufficient=True
                ),
            )
            result = execute_attempt(operations, context)
            if result["outcome"] != expected or not boundary.zero_state():
                raise RuntimeError(f"cold rehearsal scenario differs: {name}")
            receipt_files = sorted((directory / "receipts").glob("*.json"))
            failure_receipt = (
                json.loads((directory / f"receipts/{result['failure_stage']}.json").read_bytes())
                if result["failure_stage"] is not None
                else None
            )
            image_receipt = directory / "receipts/image_publication_and_scan.json"
            image_payload = (
                json.loads(image_receipt.read_bytes()).get("payload", {})
                if image_receipt.is_file()
                else {}
            )
            scenarios[name] = {
                "outcome": result["outcome"],
                "failure_stage": result["failure_stage"],
                "failure_reason_code": (
                    failure_receipt["payload"].get("reason_code")
                    if failure_receipt is not None
                    else None
                ),
                "cleanup_status": json.loads(
                    (directory / "receipts/cleanup_and_expiry.json").read_bytes()
                )["status"],
                "receipt_count": len(receipt_files),
                "normalized_receipt_chain_sha256": _normalized_receipt_chain(
                    receipt_files, directory
                ),
                "receipt_chain_normalization": [
                    "external_workdir_path",
                    "bounded_deadline_utc",
                ],
                "zero_state": boundary.zero_state(),
                "aws_boundary_calls": boundary.aws_calls,
                "kubectl_boundary_calls": boundary.kubectl_calls,
                "filesystem_side_effect_order": result["filesystem_side_effect_order"],
                "gpu_node_readiness": {
                    "reads": boundary.gpu_node_reads,
                    "observation_sequence": boundary.gpu_node_observation_sequence,
                },
                "scan_representation": image_payload.get("security_gate", {}).get(
                    "reconstruction"
                ),
                "dra_refusal_diagnostics": (
                    {
                        "persisted_before_cleanup": True,
                        "sha256": _sha(directory / "dra-refusal-diagnostics.json"),
                    }
                    if (directory / "dra-refusal-diagnostics.json").is_file()
                    else None
                ),
                "gpu_sampler_diagnostics": (
                    {
                        "persisted_before_cleanup": True,
                        "sha256": _sha(directory / "gpu-sampler-self-test.json"),
                        "status": json.loads(
                            (directory / "gpu-sampler-self-test.json").read_bytes()
                        )["status"],
                        "reason_code": json.loads(
                            (directory / "gpu-sampler-self-test.json").read_bytes()
                        )["reason_code"],
                    }
                    if (directory / "gpu-sampler-self-test.json").is_file()
                    else None
                ),
                "node_staging_diagnostics": (
                    {
                        "persisted_before_cleanup": True,
                        "sha256": _sha(directory / "node-local-input-ssm-diagnostic.json"),
                        "status": json.loads((directory / "node-local-input-ssm-diagnostic.json").read_bytes())["status"],
                        "stderr_sanitized": json.loads((directory / "node-local-input-ssm-diagnostic.json").read_bytes())["stderr_sanitized"],
                    }
                    if (directory / "node-local-input-ssm-diagnostic.json").is_file()
                    else None
                ),
                "pilot_workload_diagnostics": (
                    {
                        "persisted_before_cleanup": True,
                        "sha256": _sha(directory / "pilot-workload-refusal-diagnostics.json"),
                        "status": json.loads((directory / "pilot-workload-refusal-diagnostics.json").read_bytes())["status"],
                    }
                    if (directory / "pilot-workload-refusal-diagnostics.json").is_file()
                    else None
                ),
            }
        insufficient_directory = base / "local-disk-insufficient"
        reviewed, authorization_path, packet_path = _scenario_repository(
            base / "local-disk-insufficient-repo",
            bindings_path=bindings_path,
            bindings_body=bindings_body,
            packet_relative=bindings["successor_packet"]["path"],
            authorization_relative=bindings["authorization"]["path"],
            dry_relative=bindings["authorization"]["deadline_dry_run_path"],
        )
        scenario_bindings = json.loads(json.dumps(bindings))
        operations, boundary = build_rehearsal_operations(
            scenario_bindings, root=reviewed
        )
        context = build_attempt_context(
            root=reviewed,
            workdir=insufficient_directory,
            attempt=attempt,
            bindings=scenario_bindings,
            packet_sha256=hashlib.sha256(packet_path.read_bytes()).hexdigest(),
            authorization_sha256=hashlib.sha256(authorization_path.read_bytes()).hexdigest(),
            authorization_path=authorization_path,
            packet_path=packet_path,
            local_resource_snapshot=_resource_snapshot(
                scenario_bindings, sufficient=False
            ),
        )
        try:
            execute_attempt(operations, context)
        except OperationRefusal as exc:
            if exc.reason_code != "LOCAL_DISK_CAPACITY_INSUFFICIENT":
                raise
            if insufficient_directory.exists() or boundary.aws_calls or boundary.kubectl_calls:
                raise RuntimeError("disk prerequisite refusal had runtime side effects")
            prerequisite_refusal = {
                "status": "PASS_PRE_ENVELOPE_RESOURCE_REFUSAL_REHEARSAL",
                "reason_code": exc.reason_code,
                "attempt_envelope_created": False,
                "attempt_number_consumed": False,
                "workdir_created": False,
                "aws_boundary_calls": boundary.aws_calls,
                "kubectl_boundary_calls": boundary.kubectl_calls,
            }
        else:
            raise RuntimeError("insufficient local disk passed the pre-envelope gate")
    pure_injections = _pure_prestage_injections(
        proof, bindings["pilot_bundle"]["sha256"]
    )
    rehearsal_source_paths = [
        ROOT / "scripts/asr_base_model_pilot_cold_rehearsal.py",
        ROOT / "scripts/asr_base_model_pilot_fake.py",
        ROOT / "scripts/asr_base_model_pilot_live.py",
        ROOT / "scripts/asr_base_model_aws_read_fixtures.py",
        ROOT / "scripts/asr_base_model_proven_commands.py",
        ROOT / "scripts/asr_base_model_node_staging.py",
        ROOT / "scripts/asr_base_model_pilot_workload.py",
    ]
    receipt = {
        "schema_version": 2,
        "status": "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS",
        "real_aws_calls": 0,
        "real_kubectl_calls": 0,
        "real_stage_implementations": len(STAGES),
        "parallel_fake_stage_implementations": 0,
        "full_pass_runs": sum(
            expected == "PASS_PILOT" for _, expected in SCENARIOS.values()
        ),
        "injected_failure_runs": sum(
            expected != "PASS_PILOT" for _, expected in SCENARIOS.values()
        ) + len(pure_injections["refusals"]),
        "live_stage_injected_paths": [
            name for name, (_, expected) in SCENARIOS.items() if expected != "PASS_PILOT"
        ],
        "live_stage_delayed_success_paths": [
            name for name, (injection, expected) in SCENARIOS.items()
            if injection is not None and expected == "PASS_PILOT"
        ],
        "pure_input_injected_paths": sorted(pure_injections["refusals"]),
        "pure_input_refusal_checks": pure_injections,
        "pre_envelope_resource_gate": {
            "sufficient_capacity": "pre_envelope_local_resources_passed"
            in scenarios["clean_pass"]["filesystem_side_effect_order"],
            "insufficient_capacity": prerequisite_refusal,
            "gate_runs_before_workdir_creation": True,
            "gate_runs_before_attempt_envelope": True,
        },
        "single_representation_security_scan": scenarios["clean_pass"][
            "scan_representation"
        ],
        "artifact_wrapper_contract": wrapper,
        "bindings_source": {
            "path": str(bindings_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(bindings_body).hexdigest(),
            "loaded_from_committed_head": True,
            "fixture_bindings_permitted": False,
        },
        "rehearsal_source_commit": rehearsal_commit,
        "enumerated_stages": list(STAGES),
        "execution_asset_completeness": {
            stage: {
                "runner": f"scripts.asr_base_model_pilot_runner.{STAGE_FUNCTIONS[stage].__name__}",
                "operation": f"scripts.asr_base_model_pilot_live.LiveOperations.{getattr(LiveOperations, stage).__name__}",
                "same_operation_for_live_and_rehearsal": True,
            }
            for stage in STAGES
        },
        "stage_implementation_guard": real_only,
        "executor_module_integrity": source_integrity,
        "bounded_helper_contract_audit": boundary_contract_audit,
        "proven_live_node_command_bindings": proven_command_bindings,
        "executor_module_paths": list(bindings["executor_modules"]),
        "security_gate_validation": security_gate_validation,
        "aws_read_fixture_coverage": aws_read_fixture_coverage,
        "aws_read_dynamic_paths": aws_read_dynamic_paths,
        "gpu_node_readiness_fixtures": {
            "path": gpu_fixture_binding["path"],
            "sha256": gpu_fixture_binding["sha256"],
            "status": gpu_fixture["status"],
            "causal_classification": gpu_fixture["causal_timeline"]["classification"],
            "invented_kubernetes_fields": 0,
        },
        "rehearsal_binding_normalization_permitted": False,
        "exact_plan": plan_result,
        "authorization_schema": authorization_result,
        "fidelity_boundary": {
            "policy": "ACTUAL_LIVE_OPERATIONS_WITH_EXTERNAL_BOUNDARY_FAKES_ONLY",
            "stage_class": "scripts.asr_base_model_pilot_live.LiveOperations",
            "parallel_stage_class": None,
            "fake_boundary": ["AWS client calls replaying hash-bound real response shapes", "kubectl calls", "external Docker Scout response"],
            "aws_response_shape_policy": "EVERY_EXECUTOR_AWS_READ_HAS_COMMITTED_REAL_RESPONSE_FIXTURE_NO_INVENTED_FIELDS",
            "real_local_operations": ["pre-envelope resource validation", "stage composition", "state snapshots", "receipt ordering", "input-freeze audit", "artifact wrapper", "cleanup composition"],
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
        default=ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002P.json",
    )
    args = parser.parse_args()
    try:
        result = rehearse(args.output.resolve(), args.bindings.resolve())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "REFUSED", "exception_class": type(exc).__name__, "detail": str(exc)[:256]},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
