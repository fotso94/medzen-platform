#!/usr/bin/env python3
"""Cold-rehearse the real ASR pilot composition with boundary fakes only."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import sys
import tempfile
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVAL_RUNTIME_PACKAGE = ROOT / "services" / "asr-eval-runtime"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(EVAL_RUNTIME_PACKAGE) not in sys.path:
    sys.path.insert(0, str(EVAL_RUNTIME_PACKAGE))

import pipeline.asr_base_model_pilot_receipts as receipt_module
from medzen_asr_eval.harness import EvaluationRefusal
from medzen_asr_eval.network_probe import probe_network
from pipeline.asr_base_model_pilot_receipts import STAGES, canonical_json, write_exclusive
from scripts.asr_base_model_endpoint_policy import (
    EndpointPolicyRefusal,
    build_call_inventory,
    derive_policy,
    validate_observed_s3_calls,
    validate_policy_coverage,
)
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
from scripts.asr_base_model_pilot_live import PRIVATE_PULL_REPOSITORIES
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
from scripts.asr_base_model_async_observations import audit_async_observation_sites
from scripts.asr_base_model_pod_lifecycle import audit_waiter_and_finalizer_sites
from scripts.asr_eval_digest_rescan import validate_security_binding


SCENARIOS = {
    "clean_pass": (None, "PASS_PILOT"),
    "aggregate_chunk_integrity": (
        "aggregate_chunk_integrity",
        "FAILED_CLOSED_EXECUTION",
    ),
    "volume_device_delayed_ready": (
        "volume_device_delayed_ready",
        "PASS_PILOT",
    ),
    "volume_attachment_never_attached": (
        "volume_attachment_never_attached",
        "FAILED_CLOSED_EXECUTION",
    ),
    "volume_device_never_present": (
        "volume_device_never_present",
        "FAILED_CLOSED_EXECUTION",
    ),
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
    "pilot_large_preamble_terminal_tail": (
        "pilot_large_preamble_terminal_tail",
        "FAILED_CLOSED_EXECUTION",
    ),
    "network_receipt_delayed": ("network_receipt_delayed", "PASS_PILOT"),
    "network_receipt_timeout": (
        "network_receipt_timeout",
        "BLOCKED_NETWORK_ISOLATION",
    ),
    "network_receipt_pod_terminal": (
        "network_receipt_pod_terminal",
        "BLOCKED_NETWORK_ISOLATION",
    ),
    "dns_resolver_unreachable": (
        "dns_resolver_unreachable",
        "BLOCKED_NETWORK_ISOLATION",
    ),
    "dns_resolved_ip_outside_allowlist": (
        "dns_resolved_ip_outside_allowlist",
        "BLOCKED_NETWORK_ISOLATION",
    ),
    "dns_primary_delete_timeout": (
        "dns_primary_delete_timeout",
        "BLOCKED_NETWORK_ISOLATION",
    ),
    "image_prepull_stall": (
        "image_prepull_stall",
        "BLOCKED_NETWORK_ISOLATION",
    ),
    "image_stream_reset_then_success": (
        "image_stream_reset_then_success",
        "PASS_PILOT",
    ),
    "image_stream_persistent_reset": (
        "image_stream_persistent_reset",
        "BLOCKED_IMAGE_SCAN",
    ),
    "security_wrong_digest": ("security_wrong_digest", "BLOCKED_IMAGE_SCAN"),
    "security_extra_finding": ("security_extra_finding", "BLOCKED_IMAGE_SCAN"),
    "isolation_probe_refusal": ("private_endpoint_and_policy_gate", "BLOCKED_NETWORK_ISOLATION"),
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
            "scout_authentication": {
                "status": "PASS_SCOUT_AUTHENTICATION_HANDOFF",
                "mode": "ENVIRONMENT_PAIR",
                "credentials_present": True,
                "credentials_persisted": False,
                "credential_values_recorded": False,
            },
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


def _validate_waiter_fidelity(scenarios: dict[str, Any]) -> dict[str, Any]:
    """Prove the rehearsal crosses a non-terminal state at every live waiter family."""

    clean = scenarios["clean_pass"]
    lifecycle = clean["stage_pod_lifecycle"]
    expected_prefixes = {
        "gpu_node_readiness": (
            clean["gpu_node_readiness"]["observation_sequence"],
            ["CAPTURED_ATTEMPT_11_EMPTY", "CAPTURED_ATTEMPT_11_READY"],
        ),
        "private_endpoint_availability": (
            lifecycle["endpoint_availability_observation_sequence"],
            ["PENDING", "AVAILABLE"],
        ),
        "volume_attachment": (
            lifecycle["volume_attachment_observation_sequence"],
            ["absent", "attaching", "attached", "attached"],
        ),
        "volume_device_appearance": (
            lifecycle["volume_device_observation_sequence"],
            ["ABSENT", "PRESENT"],
        ),
        "dra_readiness": (
            lifecycle["dra_observation_sequence"],
            ["NOT_READY", "READY"],
        ),
        "image_prepull_terminal": (
            lifecycle["terminal_observation_sequences"].get(
                "asr-eval-image-prepull", []
            ),
            ["Pending", "Succeeded"],
        ),
        "image_inventory_stability": (
            lifecycle["node_image_inventory_sequence"],
            ["ABSENT", "PRESENT", "PRESENT"],
        ),
        "dns_control_terminal": (
            lifecycle["terminal_observation_sequences"].get(
                "asr-eval-dns-control", []
            ),
            ["Pending", "Succeeded"],
        ),
        "inbound_control_terminal": (
            lifecycle["terminal_observation_sequences"].get(
                "asr-eval-inbound-control", []
            ),
            ["Pending", "Succeeded"],
        ),
        "stage_pod_stable_absence": (
            lifecycle["absence_observation_sequences"].get(
                "asr-eval-image-prepull", []
            ),
            ["PRESENT", "ABSENT", "ABSENT"],
        ),
        "pilot_pod_discovery": (
            lifecycle["pilot_discovery_observation_sequence"],
            ["ABSENT", "PRESENT"],
        ),
        "pilot_network_receipts": (
            clean["pilot_receipt_readiness"]["observation_sequence"],
            ["ABSENT", "READY", "READY"],
        ),
        "pilot_job_completion": (
            lifecycle["pilot_job_observation_sequence"],
            ["ACTIVE", "SUCCEEDED"],
        ),
        "aggregate_ssm_completion": (
            lifecycle["ssm_observation_sequence"],
            ["InProgress", "Success"],
        ),
        "cleanup_endpoint_absence": (
            lifecycle["endpoint_deletion_observation_sequence"],
            ["PRESENT_DELETING", "ABSENT"],
        ),
    }
    verified: dict[str, Any] = {}
    for site, (observed, expected_prefix) in expected_prefixes.items():
        if observed[: len(expected_prefix)] != expected_prefix:
            raise AssertionError(
                f"waiter fidelity differs for {site}: "
                f"observed={observed!r} expected_prefix={expected_prefix!r}"
            )
        verified[site] = {
            "status": "PASS_NONTERMINAL_BEFORE_TERMINAL",
            "observation_sequence": observed,
        }

    primary_cleanup = scenarios["dns_primary_delete_timeout"]
    if (
        primary_cleanup["failure_reason_code"]
        != "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST"
        or primary_cleanup["secondary_cleanup_diagnostic"] is None
    ):
        raise AssertionError(
            "primary DNS refusal was masked by the injected delete timeout"
        )
    return {
        "status": "PASS_ALL_BOUNDED_WAITER_FAKES_EXERCISE_NONTERMINAL_STATE",
        "site_count": len(verified),
        "sites": verified,
        "primary_exception_preservation": {
            "status": "PASS_PRIMARY_REFUSAL_RETAINED",
            "scenario": "dns_primary_delete_timeout",
            "reason_code": primary_cleanup["failure_reason_code"],
            "secondary_cleanup_diagnostic": primary_cleanup[
                "secondary_cleanup_diagnostic"
            ],
        },
    }


def _endpoint_policy_injections(bindings: dict[str, Any]) -> dict[str, Any]:
    """Exercise the shared call-inventory policy gate without altering the fake."""

    downloads = bindings.get("recorded_boundary_fixtures", {}).get(
        "prestage_downloads"
    )
    if isinstance(downloads, dict):
        # Suite packets: exercise the policy gate over the shard's own
        # recorded bundle and model bindings.
        bundle_fixture = next(
            value for key, value in downloads.items()
            if key.endswith("pilot-bundle.json")
        )
        binding_fixture = next(
            value for key, value in downloads.items()
            if key.endswith("model-bindings.json")
        )
        pilot_bundle = json.loads((ROOT / bundle_fixture["path"]).read_bytes())
        model_bindings = json.loads((ROOT / binding_fixture["path"]).read_bytes())
    else:
        pilot_bundle = json.loads(
            (ROOT / "tests/fixtures/asr_base_model_pilot/pilot-bundle-2026-001.json").read_bytes()
        )
        model_bindings = json.loads(
            (ROOT / "tests/fixtures/asr_base_model_pilot/model-bindings-2026-001.json").read_bytes()
        )
    inventory = build_call_inventory(
        bundle_sha256=bindings["pilot_bundle"]["sha256"],
        pilot_bundle=pilot_bundle,
        model_bindings=model_bindings,
        account="558069890522",
        region="eu-central-1",
        ecr_repositories=PRIVATE_PULL_REPOSITORIES,
    )
    s3_policy = derive_policy(inventory, "s3")
    coverage = validate_policy_coverage(inventory, s3_policy, "s3")
    refusals: dict[str, str] = {}

    missing_version = json.loads(json.dumps(s3_policy))
    missing_version["Statement"] = [
        row
        for row in missing_version["Statement"]
        if row.get("Action") != ["s3:GetObjectVersion"]
    ]
    try:
        validate_policy_coverage(inventory, missing_version, "s3")
    except EndpointPolicyRefusal as exc:
        refusals["missing_get_object_version"] = exc.reason_code
    else:
        raise AssertionError("missing s3:GetObjectVersion passed policy coverage")

    drifted = json.loads(json.dumps(inventory))
    versioned = next(
        row
        for row in drifted["calls"]
        if row["service"] == "s3"
        and row["parameters"].get("version_id_present") is True
    )
    versioned["parameters"]["version_id_present"] = False
    try:
        derive_policy(drifted, "s3")
    except EndpointPolicyRefusal as exc:
        refusals["version_flag_action_drift"] = exc.reason_code
    else:
        raise AssertionError("version flag/action drift passed policy derivation")

    observed = [
        {
            "operation": row["parameters"]["operation"],
            "bucket": row["parameters"]["bucket"],
            "key": row["parameters"]["key"],
            "version_id_present": row["parameters"]["version_id_present"],
        }
        for row in inventory["calls"]
        if row["service"] == "s3"
    ]
    observed[0]["version_id_present"] = not observed[0]["version_id_present"]
    try:
        validate_observed_s3_calls(inventory, observed)
    except EndpointPolicyRefusal as exc:
        refusals["observed_request_inventory_drift"] = exc.reason_code
    else:
        raise AssertionError("observed request/inventory drift passed")

    return {
        "status": "PASS_ENDPOINT_POLICY_STATIC_REHEARSAL",
        "inventory_sha256": inventory["inventory_sha256"],
        "inventory_call_count": len(inventory["calls"]),
        "s3_coverage": coverage,
        "required_actions": coverage["required_actions"],
        "other_version_variant_actions": [],
        "refusals": refusals,
        "stage_implementation_replaced": False,
    }


class _ProbeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _network_probe_rehearsal(base: Path) -> dict[str, Any]:
    """Exercise policy convergence and refusal semantics with no network."""
    base.mkdir(parents=True)
    binding = base / "network-binding.json"
    binding.write_bytes(canonical_json({
        "schema_version": 1,
        "classification": "OFFLINE_EVALUATION_ONLY",
        "allowed_tcp_443_hosts": [
            "api.ecr.eu-central-1.amazonaws.com",
            "repository.dkr.ecr.eu-central-1.amazonaws.com",
            "bucket.s3.eu-central-1.amazonaws.com",
        ],
    }))
    addresses = {
        "api.ecr.eu-central-1.amazonaws.com": ["10.0.1.10", "10.0.2.10"],
        "repository.dkr.ecr.eu-central-1.amazonaws.com": ["10.0.1.11"],
        "bucket.s3.eu-central-1.amazonaws.com": ["10.0.1.12"],
        "dl.fbaipublicfiles.com": ["198.51.100.10"],
        "example.com": ["198.51.100.11"],
        "169.254.169.254": ["169.254.169.254"],
    }

    def resolver(host: str, port: int) -> list[str]:
        del port
        return addresses[host]

    immediate_clock = _ProbeClock()

    def immediate_connector(ip: str, port: int, timeout: float) -> None:
        del port, timeout
        if ip.startswith("198.51.100.") or ip == "169.254.169.254":
            raise OSError(errno.ECONNREFUSED, "refused")

    immediate_path = base / "policy-already-converged.json"
    immediate = probe_network(
        binding,
        immediate_path,
        resolver=resolver,
        connector=immediate_connector,
        sleeper=immediate_clock.sleep,
        monotonic=immediate_clock.monotonic,
    )
    if (
        immediate["status"] != "PASS_NETWORK_ISOLATION_PRE_TORCH"
        or len(immediate["positive_and_negative_proofs"]["positive_convergence"])
        != 1
    ):
        raise RuntimeError("already-converged rehearsal did not pass immediately")

    delayed_clock = _ProbeClock()
    delayed_calls = 0

    def delayed_connector(ip: str, port: int, timeout: float) -> None:
        nonlocal delayed_calls
        del port, timeout
        if ip in {"10.0.1.10", "10.0.2.10"}:
            delayed_calls += 1
            if delayed_calls <= 2:
                raise OSError(errno.ECONNREFUSED, "policy not programmed")
        if ip.startswith("198.51.100.") or ip == "169.254.169.254":
            raise OSError(errno.ECONNREFUSED, "refused")

    delayed_path = base / "policy-delay-then-pass.json"
    delayed = probe_network(
        binding,
        delayed_path,
        resolver=resolver,
        connector=delayed_connector,
        sleeper=delayed_clock.sleep,
        monotonic=delayed_clock.monotonic,
    )
    if (
        delayed["status"] != "PASS_NETWORK_ISOLATION_PRE_TORCH"
        or len(delayed["positive_and_negative_proofs"]["positive_convergence"]) != 2
    ):
        raise RuntimeError("policy-delay rehearsal did not converge as expected")

    timeout_clock = _ProbeClock()

    def timeout_connector(ip: str, port: int, timeout: float) -> None:
        del ip, port, timeout
        raise OSError(errno.ETIMEDOUT, "policy did not converge")

    timeout_path = base / "policy-never-converges.json"
    try:
        probe_network(
            binding,
            timeout_path,
            resolver=resolver,
            connector=timeout_connector,
            sleeper=timeout_clock.sleep,
            monotonic=timeout_clock.monotonic,
        )
    except EvaluationRefusal as exc:
        timeout = json.loads(timeout_path.read_bytes())
        if (
            timeout.get("reason_code") != "POSITIVE_NETWORK_CONVERGENCE_TIMEOUT"
            or "POSITIVE_NETWORK_CONVERGENCE_TIMEOUT" not in str(exc)
        ):
            raise RuntimeError("never-converges rehearsal refused differently") from exc
    else:
        raise RuntimeError("never-converges rehearsal passed")

    negative_clock = _ProbeClock()

    def negative_connector(ip: str, port: int, timeout: float) -> None:
        del port, timeout
        if ip in {"198.51.100.11", "169.254.169.254"}:
            raise OSError(errno.ECONNREFUSED, "refused")

    negative_path = base / "post-convergence-negative-failure.json"
    try:
        probe_network(
            binding,
            negative_path,
            resolver=resolver,
            connector=negative_connector,
            sleeper=negative_clock.sleep,
            monotonic=negative_clock.monotonic,
        )
    except EvaluationRefusal as exc:
        negative = json.loads(negative_path.read_bytes())
        if (
            negative.get("reason_code") != "PROHIBITED_NETWORK_DESTINATION_ACCEPTED"
            or "meta_public_download" not in str(exc)
        ):
            raise RuntimeError("negative-check rehearsal refused differently") from exc
    else:
        raise RuntimeError("negative-check rehearsal passed")

    return {
        "status": "PASS_NETWORK_PROBE_CONVERGENCE_REHEARSAL",
        "real_network_calls": 0,
        "scenarios": {
            "policy_already_converged_pass": {
                "status": immediate["status"],
                "convergence_attempts": len(
                    immediate["positive_and_negative_proofs"]["positive_convergence"]
                ),
                "receipt_sha256": _sha(immediate_path),
            },
            "policy_propagation_delay_then_pass": {
                "status": delayed["status"],
                "convergence_attempts": len(
                    delayed["positive_and_negative_proofs"]["positive_convergence"]
                ),
                "receipt_sha256": _sha(delayed_path),
            },
            "never_converges_timeout": {
                "status": timeout["status"],
                "reason_code": timeout["reason_code"],
                "elapsed_seconds": timeout_clock.value,
                "receipt_sha256": _sha(timeout_path),
            },
            "post_convergence_negative_check_failure": {
                "status": negative["status"],
                "reason_code": negative["reason_code"],
                "allowed_battery_completed": len(negative["telemetry"]["allowed"]),
                "receipt_sha256": _sha(negative_path),
            },
        },
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
        bindings["artifact_prestage_proof"]["path"]: (
            ROOT / bindings["artifact_prestage_proof"]["path"]
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
    if "gpu_storage_policy" in bindings:
        for evidence_name in (
            "capacity_qualification",
            "storage_apply_evidence",
            "live_fixture",
        ):
            relative = bindings["gpu_storage_policy"][evidence_name]["path"]
            tracked[relative] = (ROOT / relative).read_bytes()
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
    existing_image_fixture = bindings.get("ecr_existing_image_fixture")
    if isinstance(existing_image_fixture, dict):
        for key in ("capture_path", "path"):
            relative = existing_image_fixture[key]
            tracked[relative] = (ROOT / relative).read_bytes()
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
        ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002S.json"
    )
    bindings_body = read_committed_artifact(ROOT, bindings_path)
    bindings = json.loads(bindings_body)
    fixture_catalog = FixtureCatalog(ROOT, bindings["aws_read_fixtures"])
    aws_read_fixture_coverage = fixture_catalog.summary()
    aws_read_dynamic_paths = validate_dynamic_paths(fixture_catalog)
    existing_image_fixture = bindings.get("ecr_existing_image_fixture")
    if not isinstance(existing_image_fixture, dict):
        raise RuntimeError("existing-image ECR fixture binding is absent")
    for path_key, hash_key in (
        ("capture_path", "capture_sha256"),
        ("path", "sha256"),
    ):
        path = ROOT / existing_image_fixture[path_key]
        body = read_committed_artifact(ROOT, path)
        if hashlib.sha256(body).hexdigest() != existing_image_fixture[hash_key]:
            raise RuntimeError("existing-image ECR fixture binding differs")
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
        network_probe_rehearsal = _network_probe_rehearsal(
            base / "network-probe-rehearsal"
        )
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
            endpoint_receipt = directory / "receipts/private_endpoint_and_policy_gate.json"
            endpoint_payload = (
                json.loads(endpoint_receipt.read_bytes()).get("payload", {})
                if endpoint_receipt.is_file()
                else {}
            )
            pilot_receipt = directory / "receipts/pilot_rows.json"
            pilot_payload = (
                json.loads(pilot_receipt.read_bytes()).get("payload", {})
                if pilot_receipt.is_file()
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
                "failure_safe_error_text": (
                    failure_receipt["payload"].get("safe_error_text")
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
                "pilot_receipt_readiness": {
                    "reads": boundary.pilot_receipt_reads,
                    "observation_sequence": boundary.pilot_receipt_observation_sequence,
                    "pod_reads": boundary.pilot_pod_reads,
                },
                "stage_pod_lifecycle": {
                    "terminal_observation_sequences": boundary.pod_terminal_observation_sequence,
                    "absence_observation_sequences": boundary.pod_absence_observation_sequence,
                    "node_image_inventory_sequence": boundary.node_image_inventory_sequence,
                    "pilot_job_reads": boundary.pilot_job_reads,
                    "pilot_job_observation_sequence": boundary.pilot_job_observation_sequence,
                    "pilot_discovery_observation_sequence": boundary.pilot_discovery_observation_sequence,
                    "endpoint_availability_observation_sequence": boundary.endpoint_availability_observation_sequence,
                    "endpoint_deletion_observation_sequence": boundary.endpoint_deletion_observation_sequence,
                    "dra_observation_sequence": boundary.dra_observation_sequence,
                    "ssm_observation_sequence": boundary.ssm_observation_sequence,
                    "volume_attachment_observation_sequence": boundary.volume_attachment_observation_sequence,
                    "volume_device_observation_sequence": boundary.volume_device_observation_sequence,
                },
                "secondary_cleanup_diagnostic": (
                    {
                        "path": "asr-eval-dns-control-secondary-cleanup-diagnostic.json",
                        "sha256": _sha(
                            directory
                            / "asr-eval-dns-control-secondary-cleanup-diagnostic.json"
                        ),
                        "status": json.loads(
                            (
                                directory
                                / "asr-eval-dns-control-secondary-cleanup-diagnostic.json"
                            ).read_bytes()
                        )["status"],
                    }
                    if (
                        directory
                        / "asr-eval-dns-control-secondary-cleanup-diagnostic.json"
                    ).is_file()
                    else None
                ),
                "pod_dns": {
                    "dns_control": (
                        {
                            "dnsPolicy": boundary.dns_control_spec.get("dnsPolicy"),
                            "dnsConfig": boundary.dns_control_spec.get("dnsConfig"),
                        }
                        if boundary.dns_control_spec is not None
                        else None
                    ),
                    "inbound_control": (
                        {
                            "dnsPolicy": boundary.inbound_control_spec.get("dnsPolicy"),
                            "dnsConfig": boundary.inbound_control_spec.get("dnsConfig"),
                        }
                        if boundary.inbound_control_spec is not None
                        else None
                    ),
                },
                "scan_representation": image_payload.get("security_gate", {}).get(
                    "reconstruction"
                ),
                "read_retry_audit": image_payload.get("read_retry_audit"),
                "endpoint_policy_coverage": endpoint_payload.get(
                    "endpoint_policy_coverage"
                ),
                "endpoint_call_inventory_sha256": endpoint_payload.get(
                    "endpoint_call_inventory_sha256"
                ),
                "pre_envelope_gpu_storage": result.get(
                    "pre_envelope_gpu_storage"
                ),
                "successful_runtime_resource_telemetry": pilot_payload.get(
                    "job_completion", {}
                ).get("runtime_resource_telemetry"),
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
                        "network_probe_receipt_status": json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["network_probe_receipt"]["status"],
                        "network_probe_reason_code": json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["network_probe_receipt"].get("reason_code"),
                        "network_policy_agent_status": json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["network_policy_agent"]["status"],
                        "diagnostic_window_policy": json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["diagnostic_window_policy"],
                        "log_head_contains_terminal_error": "synthetic terminal model-load failure" in json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["logs"].get("head_sanitized", ""),
                        "log_tail_contains_terminal_error": "synthetic terminal model-load failure" in json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["logs"].get("tail_sanitized", ""),
                        "container_termination": json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["container_termination"],
                        "phase_journal": json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["phase_journal"],
                        "runtime_resource_telemetry": json.loads(
                            (directory / "pilot-workload-refusal-diagnostics.json").read_bytes()
                        )["runtime_resource_telemetry"],
                    }
                    if (directory / "pilot-workload-refusal-diagnostics.json").is_file()
                    else None
                ),
            }
        large_tail = scenarios["pilot_large_preamble_terminal_tail"][
            "pilot_workload_diagnostics"
        ]
        if (
            large_tail is None
            or large_tail["diagnostic_window_policy"]
            != "SANITIZED_HEAD_AND_TAIL_4096_BYTES_EACH"
            or large_tail["log_head_contains_terminal_error"] is not False
            or large_tail["log_tail_contains_terminal_error"] is not True
            or large_tail["container_termination"].get("exit_code") != 86
            or large_tail["container_termination"].get("reason") != "Error"
            or large_tail["container_termination"].get("signal") != 0
            or large_tail["container_termination"].get("oom_killed") is not False
            or large_tail["phase_journal"].get("last_event", {}).get("phase")
            != "PILOT_EXCEPTION"
            or large_tail["phase_journal"].get("last_event", {}).get(
                "exception_class"
            )
            != "RuntimeError"
            or large_tail["runtime_resource_telemetry"].get(
                "pass_sample_count"
            )
            < 2
        ):
            raise RuntimeError(
                "large workload preamble evicted terminal or resource diagnostics"
            )
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

        gpu_insufficient_directory = base / "gpu-storage-insufficient"
        reviewed, authorization_path, packet_path = _scenario_repository(
            base / "gpu-storage-insufficient-repo",
            bindings_path=bindings_path,
            bindings_body=bindings_body,
            packet_relative=bindings["successor_packet"]["path"],
            authorization_relative=bindings["authorization"]["path"],
            dry_relative=bindings["authorization"]["deadline_dry_run_path"],
        )
        scenario_bindings = json.loads(json.dumps(bindings))
        operations, boundary = build_rehearsal_operations(
            scenario_bindings,
            injection="gpu_storage_below_floor",
            root=reviewed,
        )
        context = build_attempt_context(
            root=reviewed,
            workdir=gpu_insufficient_directory,
            attempt=attempt,
            bindings=scenario_bindings,
            packet_sha256=hashlib.sha256(packet_path.read_bytes()).hexdigest(),
            authorization_sha256=hashlib.sha256(
                authorization_path.read_bytes()
            ).hexdigest(),
            authorization_path=authorization_path,
            packet_path=packet_path,
            local_resource_snapshot=_resource_snapshot(
                scenario_bindings, sufficient=True
            ),
        )
        try:
            execute_attempt(operations, context)
        except OperationRefusal as exc:
            if exc.reason_code != "GPU_ROOT_VOLUME_BELOW_OPERATIONAL_FLOOR":
                raise
            if (
                gpu_insufficient_directory.exists()
                or boundary.aws_calls != 1
                or boundary.aws_mutations
                or boundary.kubectl_calls
            ):
                raise RuntimeError(
                    "GPU-storage prerequisite refusal had runtime side effects"
                )
            gpu_storage_refusal = {
                "status": "PASS_PRE_ENVELOPE_GPU_STORAGE_REFUSAL_REHEARSAL",
                "reason_code": exc.reason_code,
                "attempt_envelope_created": False,
                "attempt_number_consumed": False,
                "workdir_created": False,
                "aws_boundary_calls": boundary.aws_calls,
                "aws_mutations": boundary.aws_mutations,
                "kubectl_boundary_calls": boundary.kubectl_calls,
            }
        else:
            raise RuntimeError(
                "insufficient GPU storage passed the pre-envelope gate"
            )
    pure_injections = _pure_prestage_injections(
        proof, bindings["pilot_bundle"]["sha256"]
    )
    endpoint_policy_rehearsal = _endpoint_policy_injections(bindings)
    async_observation_audit = audit_async_observation_sites(ROOT)
    waiter_finalizer_audit = audit_waiter_and_finalizer_sites(ROOT)
    waiter_fidelity = _validate_waiter_fidelity(scenarios)
    rehearsal_source_paths = [
        ROOT / "scripts/asr_base_model_pilot_cold_rehearsal.py",
        ROOT / "scripts/asr_base_model_pilot_fake.py",
        ROOT / "scripts/asr_base_model_pilot_live.py",
        ROOT / "scripts/asr_base_model_aws_read_fixtures.py",
        ROOT / "scripts/asr_base_model_proven_commands.py",
        ROOT / "scripts/asr_base_model_node_staging.py",
        ROOT / "scripts/asr_base_model_pilot_workload.py",
        ROOT / "scripts/asr_idempotent_read_retry.py",
        ROOT / "scripts/asr_base_model_gpu_storage.py",
        ROOT / "scripts/asr_base_model_async_observations.py",
        ROOT / "scripts/asr_base_model_pod_lifecycle.py",
        ROOT / "services/asr-eval-runtime/medzen_asr_eval/network_probe.py",
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
        ) + len(pure_injections["refusals"]) + 2,
        "live_stage_injected_paths": [
            name for name, (_, expected) in SCENARIOS.items() if expected != "PASS_PILOT"
        ],
        "live_stage_delayed_success_paths": [
            name for name, (injection, expected) in SCENARIOS.items()
            if injection is not None and expected == "PASS_PILOT"
        ],
        "pure_input_injected_paths": sorted(pure_injections["refusals"]),
        "pure_input_refusal_checks": pure_injections,
        "endpoint_policy_inventory": {
            "static_rehearsal": endpoint_policy_rehearsal,
            "live_composition_coverage": scenarios["clean_pass"][
                "endpoint_policy_coverage"
            ],
            "live_composition_inventory_sha256": scenarios["clean_pass"][
                "endpoint_call_inventory_sha256"
            ],
            "hand_written_action_lists_permitted": False,
        },
        "pre_envelope_resource_gate": {
            "sufficient_capacity": "pre_envelope_local_resources_passed"
            in scenarios["clean_pass"]["filesystem_side_effect_order"],
            "insufficient_capacity": prerequisite_refusal,
            "gate_runs_before_workdir_creation": True,
            "gate_runs_before_attempt_envelope": True,
        },
        "pre_envelope_gpu_storage_gate": {
            "sufficient_capacity": scenarios["clean_pass"][
                "pre_envelope_gpu_storage"
            ],
            "insufficient_capacity": gpu_storage_refusal,
            "gate_runs_before_workdir_creation": True,
            "gate_runs_before_attempt_envelope": True,
            "aws_read_calls": 1,
            "aws_mutations": 0,
        },
        "single_representation_security_scan": scenarios["clean_pass"][
            "scan_representation"
        ],
        "artifact_wrapper_contract": wrapper,
        "network_probe_convergence": network_probe_rehearsal,
        "pod_dns_alignment": {
            "status": "PASS_REAL_POD_SPEC_DNS_ALIGNMENT_REHEARSAL",
            "resolver": "172.31.0.2",
            "clean_pass_dns_control": scenarios["clean_pass"]["pod_dns"][
                "dns_control"
            ],
            "clean_pass_inbound_control": scenarios["clean_pass"]["pod_dns"][
                "inbound_control"
            ],
            "resolver_unreachable_reason_code": scenarios[
                "dns_resolver_unreachable"
            ]["failure_reason_code"],
            "outside_allowlist_reason_code": scenarios[
                "dns_resolved_ip_outside_allowlist"
            ]["failure_reason_code"],
        },
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
        "async_observation_audit": async_observation_audit,
        "waiter_finalizer_audit": waiter_finalizer_audit,
        "bounded_waiter_rehearsal_fidelity": waiter_fidelity,
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
        default=ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002X.json",
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
