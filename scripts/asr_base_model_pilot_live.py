"""Real, fail-closed operations for the authorized offline ASR pilot.

This module is deliberately import-safe: importing it makes no AWS or
Kubernetes call. Every mutation is reachable only through one of the exact
stage methods consumed by ``asr_base_model_pilot_runner``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


from pipeline.asr_base_model_pilot_receipts import canonical_json, write_exclusive
from scripts.asr_external_tool import (
    ExternalToolTimeout,
    run_external,
    sanitize_bytes,
    sanitize_head_tail,
)
from scripts.asr_idempotent_read_retry import (
    IdempotentReadRetrier,
    RetryPolicy,
    TransientReadFault,
    TransientReadRetryExhausted,
    classify_external_read_failure,
    invoke_transport_read,
)
from scripts.asr_base_model_boundary_contracts import (
    DRA_WAIT_TIMEOUT_SECONDS,
    invoke_dra_waiter,
    validate_boundary_parameters,
)
from scripts.asr_base_model_async_observations import (
    AsyncObservationRefusal,
    PILOT_RECEIPT_POLL_INTERVAL_SECONDS,
    PILOT_RECEIPT_STABLE_OBSERVATIONS,
    PILOT_RECEIPT_TIMEOUT_SECONDS,
    VOLUME_ATTACHMENT_POLL_INTERVAL_SECONDS,
    VOLUME_ATTACHMENT_STABLE_OBSERVATIONS,
    VOLUME_ATTACHMENT_TIMEOUT_SECONDS,
    VOLUME_DEVICE_TIMEOUT,
    network_receipt_observation_command,
    observe_volume_attachment,
    parse_network_receipt_observation,
    pilot_pod_terminal_observation,
    volume_mount_command_template,
    volume_mount_commands,
)
from scripts.asr_base_model_pilot_assets import (
    AssetRefusal,
    ObjectStore,
    select_pilot_rows,
    select_suite_rows,
    sha256_file,
    stage_assets,
)
from scripts.asr_base_model_ecr_scanning import (
    canonical_configuration,
    merge_scan_on_push_filter,
    validate_configuration,
)
from scripts.asr_base_model_endpoint_policy import (
    EndpointPolicyRefusal,
    build_call_inventory,
    derive_policy,
    validate_observed_s3_calls,
    validate_policy_coverage,
)
from scripts.asr_base_model_gpu_storage import (
    GpuStorageRefusal,
    validate_gpu_storage_prerequisite,
)
from scripts.asr_eval_digest_rescan import (
    DigestRescanRefusal,
    scan_exact_ecr_child,
    validate_security_binding,
)
from scripts.asr_eval_oci_publication import (  # noqa: E402
    OciPublicationRefusal,
    publish_exact_image,
)
from scripts.asr_base_model_pilot_k8s import render as render_k8s
from scripts.asr_base_model_pilot_dns import (
    DnsAlignmentRefusal,
    VPC_DNS_RESOLVER,
    pod_dns_fields,
    validate_dns_resolution_receipt,
    validate_pod_dns_fields,
    workload_egress_allowlist,
)
from scripts.asr_base_model_pod_lifecycle import (
    POD_ABSENCE_STABLE_OBSERVATIONS,
    POD_DELETE_TIMEOUT_SECONDS,
    POD_POLL_INTERVAL_SECONDS,
    POD_PULL_STALL_SECONDS,
    POD_TERMINAL_TIMEOUT_SECONDS,
    PodLifecycleRefusal,
    exact_image_in_node_inventory,
    observe_named_pod_list,
    observe_pod,
)
from scripts.asr_base_model_pilot_integrity import (
    PilotIntegrityRefusal,
    read_committed_artifact,
    validate_executor_module_bindings,
    validate_governance_commit_boundary,
)
from scripts.asr_base_model_proven_commands import (
    B6A_PROVEN_NVIDIA_SMI_ARGV,
    ProvenCommandRefusal,
    canonical_argv_sha256,
    sampler_shell_command,
    validate_proven_command_bindings,
)
from scripts.asr_base_model_pilot_staging import (
    StagingRefusal,
    validate_prestage_proof,
    validate_window_budget,
    verify_prestaged_bundle,
)
from scripts.asr_base_model_pilot_plan import (
    ACCOUNT,
    CLUSTER,
    LEGACY_GPU_ASG,
    NAMESPACE,
    NODE_SG,
    PROFILE,
    REGION,
    VPC,
    exact_plan,
    validate_plan,
)
from scripts.asr_base_model_node_staging import (
    STAGING_PRESIGNED_URL_SECONDS,
    STAGING_SSM_TIMEOUT_SECONDS,
    audit_staging_commands,
    concatenate_files,
    download_file,
    extract_archive,
    install_directory,
    numeric_identity_command,
    root_command,
    staging_prelude,
    verify_sha256,
    verify_size,
    write_base64,
)
from scripts.asr_base_model_pilot_runner import (
    AttemptContext,
    OperationRefusal,
    validate_authorization_payload,
)
from scripts.asr_base_model_pilot_workload import bound_attempt_window


CALLER = f"arn:aws:iam::{ACCOUNT}:user/s.fotso"
BUCKET = "medzen-speech"
GPU_NODEGROUP = "gpu"
CPU_NODEGROUP = "cpu"
ECR_REPOSITORY = "medzen-asr-eval-runtime"
# SSM GetCommandInvocation truncates StandardOutputContent at 24,000
# characters; 15,000 raw bytes encode to 20,000 base64 characters, keeping
# every chunk safely under the cap (attempt-27 refusal).
SSM_READBACK_RAW_CHUNK_BYTES = 15000
AGGREGATE_READBACK_MAXIMUM_BYTES = 33_554_432
DRA_MANIFEST = Path("platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml")
DRA_NETWORK_POLICY = Path("platform/k8s/asr-eval/nvidia-dra-api-egress.yaml")
EXPECTED_HIGHS = {
    ("CVE-2026-24747", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2026-4538", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2025-55552", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2025-55551", "torch", "2.8.0+cu128", "HIGH"),
}
PRIVATE_PULL_REPOSITORIES = (
    (ACCOUNT, ECR_REPOSITORY),
    (ACCOUNT, "medzen-nvidia-dra"),
    ("602401143452", "amazon-k8s-cni-init"),
    ("602401143452", "amazon-k8s-cni"),
    ("602401143452", "amazon/aws-network-policy-agent"),
    ("602401143452", "eks/eks-pod-identity-agent"),
    ("602401143452", "eks/kube-proxy"),
)
GPU_NODE_READY_POLL_INTERVAL_SECONDS = 10
GPU_NODE_READY_TIMEOUT_SECONDS = 600
GPU_NODE_READY_STABLE_OBSERVATIONS = 2
IMAGE_SCAN_READ_RETRY_HARD_CAP_SECONDS = 7200
S3_READ_RETRY_HARD_CAP_SECONDS = 3600
RUNTIME_TELEMETRY_MARKER = "MEDZEN_RUNTIME_TELEMETRY_V1"
PHASE_JOURNAL_PRESENT = "MEDZEN_PHASE_JOURNAL_PRESENT"
PHASE_JOURNAL_ABSENT = "MEDZEN_PHASE_JOURNAL_ABSENT"
PHASE_JOURNAL_TAIL = "MEDZEN_PHASE_JOURNAL_TAIL"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], *, cwd: Path | None = None, stdin: bytes | None = None,
         timeout: int = 900, check: bool = True,
         journal_path: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        completed, diagnostic = run_external(
            command, cwd=cwd, input=stdin, timeout=timeout, journal_path=journal_path
        )
    except ExternalToolTimeout as exc:
        raise OperationRefusal(
            "BOUNDED_COMMAND_TIMEOUT",
            f"{Path(command[0]).name} timed out: {canonical_json(exc.diagnostic).decode().strip()}",
        ) from exc
    if check and completed.returncode != 0:
        raise OperationRefusal(
            "BOUNDED_COMMAND_REFUSED",
            f"{Path(command[0]).name} refused: {canonical_json(diagnostic).decode().strip()}",
        )
    return completed


def _json_command(command: list[str], *, cwd: Path | None = None, timeout: int = 900) -> dict[str, Any]:
    completed = _run(command, cwd=cwd, timeout=timeout)
    try:
        value = json.loads(completed.stdout)
    except Exception as exc:
        raise OperationRefusal("COMMAND_RESPONSE_MALFORMED", f"{Path(command[0]).name} returned non-JSON") from exc
    if not isinstance(value, dict):
        raise OperationRefusal("COMMAND_RESPONSE_MALFORMED", "command response is not an object")
    return value


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_container_termination(
    pod: Any,
    *,
    container_name: str = "offline-evaluator",
) -> dict[str, Any]:
    """Extract the stable Kubernetes termination fields without shape guesses."""
    status = pod.get("status") if isinstance(pod, dict) else None
    statuses = status.get("containerStatuses") if isinstance(status, dict) else None
    if not isinstance(statuses, list):
        return {
            "status": "UNAVAILABLE_CONTAINER_STATUSES_ABSENT",
            "container_name": container_name,
            "exit_code": None,
            "reason": None,
            "signal": None,
            "oom_killed": None,
            "started_at": None,
            "finished_at": None,
        }
    matches = [
        item
        for item in statuses
        if isinstance(item, dict) and item.get("name") == container_name
    ]
    if len(matches) != 1:
        return {
            "status": "UNAVAILABLE_CONTAINER_STATUS_AMBIGUOUS",
            "container_name": container_name,
            "matching_statuses": len(matches),
            "exit_code": None,
            "reason": None,
            "signal": None,
            "oom_killed": None,
            "started_at": None,
            "finished_at": None,
        }
    item = matches[0]
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    terminated = state.get("terminated")
    if not isinstance(terminated, dict):
        current_state = next(
            (name for name in ("waiting", "running") if isinstance(state.get(name), dict)),
            "unknown",
        )
        current = state.get(current_state) if isinstance(state.get(current_state), dict) else {}
        return {
            "status": "NOT_TERMINATED",
            "container_name": container_name,
            "current_state": current_state,
            "current_reason": current.get("reason"),
            "restart_count": item.get("restartCount"),
            "ready": item.get("ready"),
            "exit_code": None,
            "reason": None,
            "signal": None,
            "oom_killed": False,
            "started_at": None,
            "finished_at": None,
        }
    reason = terminated.get("reason")
    return {
        "status": "TERMINATED",
        "container_name": container_name,
        "restart_count": item.get("restartCount"),
        "ready": item.get("ready"),
        "exit_code": terminated.get("exitCode"),
        "reason": reason,
        "signal": terminated.get("signal"),
        "oom_killed": reason == "OOMKilled",
        "started_at": terminated.get("startedAt"),
        "finished_at": terminated.get("finishedAt"),
        "message_sanitized": sanitize_bytes(terminated.get("message")),
    }


class S3CreateOnlyStore(ObjectStore):
    """Legacy attempt-8 store retained only for historical diagnosis tests.

    Live timed attempts no longer construct this store. All large transfers
    occur through the pre-staging module before authorization.
    """

    def __init__(self, s3: Any, kms_key_arn: str):
        self.s3 = s3
        self.kms_key_arn = kms_key_arn

    def download(self, bucket: str, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            self.s3.download_fileobj(bucket, key, stream)

    def upload_create_only(self, source: Path, bucket: str, key: str, sha256: str) -> str:
        digest, size = sha256_file(source)
        if digest != sha256 or size >= 5 * 1024 * 1024 * 1024:
            raise AssetRefusal("conditional object size or digest differs")
        with source.open("rb") as stream:
            response = self.s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=stream,
                ContentLength=size,
                IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=self.kms_key_arn,
                Metadata={"sha256": digest, "classification": "offline-evaluation-only"},
            )
        readback = self.s3.get_object(Bucket=bucket, Key=key, VersionId=response["VersionId"])
        measured = hashlib.sha256()
        measured_bytes = 0
        for block in iter(lambda: readback["Body"].read(8 * 1024 * 1024), b""):
            measured.update(block)
            measured_bytes += len(block)
        if measured.hexdigest() != digest or measured_bytes != size:
            raise AssetRefusal("conditional object readback differs")
        return response["VersionId"]


class LiveOperations:
    """The only stage implementation used by live execution and rehearsal.

    A cold rehearsal may inject deterministic clients at the external-call
    boundary.  It may not replace or override any stage method.  This makes
    stage composition, state persistence, cleanup ordering and wrapper status
    checks identical to a live run.
    """

    def __init__(
        self,
        root: Path,
        *,
        session: Any | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[bytes]] = _run,
        kubectl_runner: Callable[..., dict[str, Any] | bytes] | None = None,
        ssm_runner: Callable[..., dict[str, Any]] | None = None,
        digest_scanner: Callable[..., dict[str, Any]] = scan_exact_ecr_child,
        oci_publisher: Callable[..., dict[str, Any]] = publish_exact_image,
        dra_waiter: Callable[..., dict[str, Any]] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.root = root
        if session is None:
            try:
                import boto3
            except Exception as exc:
                raise OperationRefusal("BOTO3_ABSENT", "the reviewed AWS SDK is unavailable") from exc
            session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        self.session = session
        self._command_runner = command_runner
        self._kubectl_runner = kubectl_runner
        self._ssm_runner = ssm_runner
        self._digest_scanner = digest_scanner
        self._oci_publisher = oci_publisher
        self._dra_waiter = dra_waiter
        self._sleeper = sleeper
        self._monotonic = monotonic
        self.sts = self.session.client("sts")
        self.eks = self.session.client("eks")
        self.ec2 = self.session.client("ec2")
        self.ecr = self.session.client("ecr")
        self.s3 = self.session.client("s3")
        self.asg = self.session.client("autoscaling")
        self.ssm = self.session.client("ssm")

    def _idempotent_read(
        self,
        operation: str,
        label: str,
        action: Callable[[], Any],
        *,
        hard_cap_seconds: float,
    ) -> tuple[Any, dict[str, Any]]:
        """Retry only a boundary-typed transport failure on an approved read."""

        retrier = IdempotentReadRetrier(
            RetryPolicy(hard_cap_seconds=hard_cap_seconds),
            sleeper=self._sleeper,
            monotonic=self._monotonic,
        )

        def typed_action() -> Any:
            return invoke_transport_read(operation, action)

        try:
            return retrier.run(operation, label, typed_action)
        except TransientReadRetryExhausted as exc:
            raise OperationRefusal(
                exc.reason_code,
                "typed transient read retry exhausted: "
                + canonical_json(exc.audit).decode().strip(),
            ) from exc

    def _idempotent_read_composition(
        self,
        operations: tuple[str, ...],
        label: str,
        action: Callable[[], Any],
        *,
        hard_cap_seconds: float,
    ) -> tuple[Any, dict[str, Any]]:
        retrier = IdempotentReadRetrier(
            RetryPolicy(hard_cap_seconds=hard_cap_seconds),
            sleeper=self._sleeper,
            monotonic=self._monotonic,
        )
        try:
            return retrier.run_composed(operations, label, action)
        except TransientReadRetryExhausted as exc:
            raise OperationRefusal(
                exc.reason_code,
                "typed transient read retry exhausted: "
                + canonical_json(exc.audit).decode().strip(),
            ) from exc

    def _idempotent_read_command(
        self,
        operation: str,
        label: str,
        command: list[str],
        *,
        hard_cap_seconds: float = S3_READ_RETRY_HARD_CAP_SECONDS,
    ) -> tuple[subprocess.CompletedProcess[bytes], dict[str, Any]]:
        def action() -> subprocess.CompletedProcess[bytes]:
            try:
                completed = self._command(command, timeout=1800, check=False)
            except OperationRefusal as exc:
                if exc.reason_code == "BOUNDED_COMMAND_TIMEOUT":
                    raise TransientReadFault(operation, "TIMEOUT") from exc
                raise
            transient = classify_external_read_failure(
                operation,
                returncode=completed.returncode,
                stdout=completed.stdout or b"",
                stderr=completed.stderr or b"",
            )
            if transient is not None:
                raise transient
            if completed.returncode != 0:
                raise OperationRefusal(
                    "IDEMPOTENT_READ_COMMAND_REFUSED",
                    f"{Path(command[0]).name} read command refused without a typed transient transport fault",
                )
            return completed

        return self._idempotent_read(
            operation,
            label,
            action,
            hard_cap_seconds=hard_cap_seconds,
        )

    def _command(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        stdin: bytes | None = None,
        timeout: int = 900,
        check: bool = True,
        journal_path: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        validate_boundary_parameters("external_command", timeout=timeout)
        return self._command_runner(
            command,
            cwd=cwd,
            stdin=stdin,
            timeout=timeout,
            check=check,
            journal_path=journal_path,
        )

    def _json_external_command(
        self, command: list[str], *, cwd: Path | None = None, timeout: int = 900
    ) -> dict[str, Any]:
        completed = self._command(command, cwd=cwd, timeout=timeout)
        try:
            value = json.loads(completed.stdout)
        except Exception as exc:
            raise OperationRefusal(
                "COMMAND_RESPONSE_MALFORMED",
                f"{Path(command[0]).name} returned non-JSON",
            ) from exc
        if not isinstance(value, dict):
            raise OperationRefusal(
                "COMMAND_RESPONSE_MALFORMED", "command response is not an object"
            )
        return value

    def _state(self, context: AttemptContext) -> dict[str, Any]:
        directory = context.workdir / "state"
        snapshots = sorted(directory.glob("*.json"))
        if not snapshots:
            return {
                "deadline_action": None,
                "reservation": False,
                "ecr_repository_created": False,
                "scan_configuration_before": None,
                "artifact_prefix": None,
                "endpoint_ids": [],
                "endpoint_security_group": None,
                "cni_addon_before": None,
                "cni_daemonset_env_before": None,
                "cni_changed": False,
                "namespace": False,
                "gpu_scaled": False,
                "volume_id": None,
                "instance_id": None,
                "node_name": None,
                "staging_path": None,
                "dra_installed": False,
                "dra_pod": None,
            }
        return json.loads(snapshots[-1].read_bytes())

    def _save_state(self, context: AttemptContext, state: dict[str, Any]) -> None:
        directory = context.workdir / "state"
        sequence = len(list(directory.glob("*.json"))) + 1 if directory.exists() else 1
        write_exclusive(directory / f"{sequence:04d}.json", canonical_json(state))

    def _aws(self, *args: str, timeout: int = 900) -> dict[str, Any]:
        return self._json_external_command(["aws", "--profile", PROFILE, "--region", REGION, *args, "--output", "json"], timeout=timeout)

    def _kubectl(self, context: AttemptContext, *args: str, stdin: bytes | None = None,
                 timeout: int = 900, json_output: bool = False) -> dict[str, Any] | bytes:
        validate_boundary_parameters("kubectl", timeout=timeout)
        if self._kubectl_runner is not None:
            return self._kubectl_runner(
                context,
                *args,
                stdin=stdin,
                timeout=timeout,
                json_output=json_output,
            )
        kubeconfig = context.workdir / "kubeconfig"
        command = ["kubectl", "--kubeconfig", str(kubeconfig), *args]
        if json_output:
            return self._json_external_command(command + ["-o", "json"], timeout=timeout)
        return self._command(command, stdin=stdin, timeout=timeout).stdout

    def _wait_stage_pod_terminal(
        self,
        context: AttemptContext,
        *,
        pod_name: str,
        purpose: str,
        timeout_seconds: int = POD_TERMINAL_TIMEOUT_SECONDS,
        poll_interval_seconds: int = POD_POLL_INTERVAL_SECONDS,
        stall_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Poll one stage-local Pod and retain every safe phase transition."""

        validate_boundary_parameters(
            "stage_pod_terminal",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        if stall_seconds is not None and (
            stall_seconds <= poll_interval_seconds or stall_seconds >= timeout_seconds
        ):
            raise OperationRefusal(
                "STAGE_POD_STALL_BOUNDARY_DIFFERS",
                "stage-Pod stall bound must be shorter than its terminal bound",
            )
        started = self._monotonic()
        deadline = started + timeout_seconds
        last_progress_at = started
        last_progress_sha256: str | None = None
        observations = 0
        phase_sequence: list[str] = []
        progress_changes = 0
        while self._monotonic() < deadline:
            value = self._kubectl(
                context,
                "get",
                "pod",
                pod_name,
                "-n",
                NAMESPACE,
                timeout=30,
                json_output=True,
            )
            try:
                observed = observe_pod(value)
            except PodLifecycleRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            observations += 1
            phase_sequence.append(observed["phase"])
            if observed["progress_sha256"] != last_progress_sha256:
                last_progress_sha256 = observed["progress_sha256"]
                last_progress_at = self._monotonic()
                progress_changes += 1
            if observed["terminal"]:
                return {
                    "status": "PASS_STAGE_POD_TERMINAL_OBSERVATION",
                    "purpose": purpose,
                    "pod_name": pod_name,
                    "phase": observed["phase"],
                    "reason": observed["reason"],
                    "containers": observed["containers"],
                    "observations": observations,
                    "phase_sequence": phase_sequence,
                    "progress_changes": progress_changes,
                    "sleep_branch_executed": observations > 1,
                    "elapsed_seconds": round(self._monotonic() - started, 3),
                    "timeout_seconds": timeout_seconds,
                    "poll_interval_seconds": poll_interval_seconds,
                    "stall_seconds": stall_seconds,
                }
            waiting_reasons = {
                item.get("state", {}).get("reason")
                for item in observed["containers"]
                if item.get("state", {}).get("kind") == "waiting"
            }
            fatal_waiting = waiting_reasons & {
                "ErrImagePull",
                "ImagePullBackOff",
                "InvalidImageName",
                "CreateContainerConfigError",
            }
            if fatal_waiting:
                raise OperationRefusal(
                    "STAGE_POD_TERMINAL_REFUSED",
                    f"{purpose} pod entered a fatal waiting state: {sorted(fatal_waiting)}",
                    outcome="BLOCKED_NETWORK_ISOLATION",
                )
            if (
                stall_seconds is not None
                and self._monotonic() - last_progress_at >= stall_seconds
            ):
                raise OperationRefusal(
                    "IMAGE_PREPULL_PROGRESS_STALLED",
                    f"{purpose} pod showed no bounded status progress for {stall_seconds} seconds; phase={observed['phase']}",
                    outcome="BLOCKED_NETWORK_ISOLATION",
                )
            remaining = deadline - self._monotonic()
            if remaining <= poll_interval_seconds:
                break
            self._sleeper(poll_interval_seconds)
        raise OperationRefusal(
            "STAGE_POD_TERMINAL_TIMEOUT",
            f"{purpose} pod did not become terminal inside {timeout_seconds} seconds after {observations} observations",
            outcome="BLOCKED_NETWORK_ISOLATION",
        )

    def _wait_stage_pod_absent(
        self,
        context: AttemptContext,
        *,
        pod_name: str,
        timeout_seconds: int = POD_DELETE_TIMEOUT_SECONDS,
        poll_interval_seconds: int = 5,
        required_observations: int = POD_ABSENCE_STABLE_OBSERVATIONS,
    ) -> dict[str, Any]:
        """Require stable absence after a non-blocking stage-Pod delete."""

        validate_boundary_parameters(
            "stage_pod_absence",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            required_observations=required_observations,
        )
        started = self._monotonic()
        deadline = started + timeout_seconds
        observations = 0
        consecutive_absent = 0
        present_observations = 0
        sequence: list[str] = []
        while self._monotonic() < deadline:
            value = self._kubectl(
                context,
                "get",
                "pods",
                "-n",
                NAMESPACE,
                f"--field-selector=metadata.name={pod_name}",
                timeout=30,
                json_output=True,
            )
            try:
                observed = observe_named_pod_list(value, pod_name)
            except PodLifecycleRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            observations += 1
            if observed["present"]:
                present_observations += 1
                consecutive_absent = 0
                sequence.append("PRESENT")
            else:
                consecutive_absent += 1
                sequence.append("ABSENT")
                if consecutive_absent >= required_observations:
                    return {
                        "status": "PASS_STABLE_STAGE_POD_ABSENCE",
                        "pod_name": pod_name,
                        "observations": observations,
                        "present_observations": present_observations,
                        "stable_absence_observations": consecutive_absent,
                        "required_stable_absence_observations": required_observations,
                        "observation_sequence": sequence,
                    }
            remaining = deadline - self._monotonic()
            if remaining <= poll_interval_seconds:
                break
            self._sleeper(poll_interval_seconds)
        raise OperationRefusal(
            "STAGE_POD_DELETE_TIMEOUT",
            f"{pod_name} did not reach {required_observations} stable absence observations inside {timeout_seconds} seconds",
        )

    def _delete_stage_pod(
        self, context: AttemptContext, *, pod_name: str, purpose: str
    ) -> dict[str, Any]:
        """Best-effort non-blocking delete whose result can never mask a primary."""

        try:
            self._kubectl(
                context,
                "delete",
                f"pod/{pod_name}",
                "-n",
                NAMESPACE,
                "--ignore-not-found=true",
                "--wait=false",
                timeout=60,
            )
            absence = self._wait_stage_pod_absent(context, pod_name=pod_name)
            return {
                "status": "PASS_NONBLOCKING_DELETE_AND_STABLE_ABSENCE",
                "purpose": purpose,
                "delete_waited_server_side": False,
                "absence": absence,
            }
        except Exception as exc:
            return {
                "status": "SECONDARY_STAGE_POD_CLEANUP_REFUSED",
                "purpose": purpose,
                "exception_class": type(exc).__name__,
                "reason_code": getattr(exc, "reason_code", None),
                "safe_error_text": sanitize_bytes(str(exc))[:512],
            }

    def _retain_secondary_pod_cleanup(
        self,
        context: AttemptContext,
        *,
        pod_name: str,
        purpose: str,
        primary: Exception,
        cleanup: dict[str, Any],
    ) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "status": "PRIMARY_EXCEPTION_RETAINED_WITH_SECONDARY_CLEANUP_DIAGNOSTIC",
            "purpose": purpose,
            "pod_name": pod_name,
            "primary_exception_class": type(primary).__name__,
            "primary_reason_code": getattr(primary, "reason_code", None),
            "primary_safe_error_text": sanitize_bytes(str(primary))[:512],
            "cleanup": cleanup,
        }
        path = context.workdir / f"{pod_name}-secondary-cleanup-diagnostic.json"
        write_exclusive(path, canonical_json(value))
        return {**value, "sha256": _sha(path)}

    def _update_kubeconfig(self, context: AttemptContext) -> None:
        self._command([
            "aws", "--profile", PROFILE, "--region", REGION, "eks", "update-kubeconfig",
            "--name", CLUSTER, "--kubeconfig", str(context.workdir / "kubeconfig"), "--alias", "medzen-asr-eval",
        ])

    def _nodegroup(self, name: str) -> dict[str, Any]:
        return self.eks.describe_nodegroup(clusterName=CLUSTER, nodegroupName=name)["nodegroup"]

    @staticmethod
    def _gpu_asg(context: AttemptContext) -> str:
        if context.attempt >= 20:
            value = context.bindings.get("aws", {}).get("gpu_asg_name")
            if not isinstance(value, str) or not value:
                raise OperationRefusal(
                    "GPU_ASG_BINDING_ABSENT", "current GPU ASG binding is absent"
                )
            return value
        return LEGACY_GPU_ASG

    def gpu_storage_prerequisite(self, context: AttemptContext) -> dict[str, Any]:
        try:
            return validate_gpu_storage_prerequisite(
                self.root,
                context.bindings.get("gpu_storage_policy"),
                self._nodegroup(GPU_NODEGROUP),
                expected_image=context.bindings.get("image", {}),
            )
        except GpuStorageRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc

    def _wait_nodegroup(self, desired: int, timeout_seconds: int = 1200) -> dict[str, Any]:
        validate_boundary_parameters(
            "nodegroup", desired=desired, timeout_seconds=timeout_seconds
        )
        stop = time.monotonic() + timeout_seconds
        stable = 0
        last: dict[str, Any] = {}
        while time.monotonic() < stop:
            nodegroup = self._nodegroup(GPU_NODEGROUP)
            scaling = nodegroup["scalingConfig"]
            resources = nodegroup.get("resources", {}).get("autoScalingGroups", [])
            instances = []
            if resources:
                group = self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[resources[0]["name"]])["AutoScalingGroups"]
                instances = group[0].get("Instances", []) if group else []
            observed = {
                "status": nodegroup["status"],
                "desired": scaling["desiredSize"],
                "instances": len(instances),
                "instance_ids": sorted(item["InstanceId"] for item in instances),
            }
            if observed == last and observed["status"] == "ACTIVE" and observed["desired"] == desired and len(instances) == desired:
                stable += 1
            else:
                stable = 1
                last = observed
            if stable >= 3:
                return observed
            self._sleeper(10)
        raise OperationRefusal("GPU_NODEGROUP_STABILITY_TIMEOUT", "GPU nodegroup did not reach three stable observations")

    @staticmethod
    def _gpu_node_observation(value: dict[str, Any]) -> dict[str, Any]:
        items = value.get("items")
        if not isinstance(items, list):
            raise OperationRefusal(
                "GPU_NODE_RESPONSE_MALFORMED",
                "the Kubernetes labeled-node response has no items list",
            )
        names: list[str] = []
        ready_names: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            status = item.get("status")
            if not isinstance(metadata, dict) or not isinstance(status, dict):
                continue
            name = metadata.get("name")
            labels = metadata.get("labels")
            conditions = status.get("conditions")
            if not isinstance(name, str) or not name:
                continue
            names.append(name)
            if (
                isinstance(labels, dict)
                and labels.get("workload") == "gpu"
                and isinstance(conditions, list)
                and any(
                    isinstance(condition, dict)
                    and condition.get("type") == "Ready"
                    and condition.get("status") == "True"
                    for condition in conditions
                )
            ):
                ready_names.append(name)
        return {
            "labeled_node_count": len(items),
            "node_names": sorted(names),
            "ready_node_names": sorted(ready_names),
        }

    def _wait_gpu_node_ready(
        self,
        context: AttemptContext,
        *,
        timeout_seconds: int = GPU_NODE_READY_TIMEOUT_SECONDS,
        poll_interval_seconds: int = GPU_NODE_READY_POLL_INTERVAL_SECONDS,
        required_observations: int = GPU_NODE_READY_STABLE_OBSERVATIONS,
    ) -> dict[str, Any]:
        validate_boundary_parameters(
            "gpu_node_readiness",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            required_observations=required_observations,
        )
        deadline = self._monotonic() + timeout_seconds
        observations = 0
        consecutive = 0
        stable_name: str | None = None
        last = {
            "labeled_node_count": 0,
            "node_names": [],
            "ready_node_names": [],
        }
        while self._monotonic() < deadline:
            value = self._kubectl(
                context,
                "get",
                "nodes",
                "-l",
                "workload=gpu",
                json_output=True,
            )
            if not isinstance(value, dict):
                raise OperationRefusal(
                    "GPU_NODE_RESPONSE_MALFORMED",
                    "the Kubernetes labeled-node response is not an object",
                )
            last = self._gpu_node_observation(value)
            observations += 1
            ready_names = last["ready_node_names"]
            if last["labeled_node_count"] == 1 and len(ready_names) == 1:
                observed_name = ready_names[0]
                if observed_name == stable_name:
                    consecutive += 1
                else:
                    stable_name = observed_name
                    consecutive = 1
                if consecutive >= required_observations:
                    return {
                        "status": "PASS_STABLE_GPU_NODE_READINESS",
                        "node_name": observed_name,
                        "observations": observations,
                        "consecutive_ready_observations": consecutive,
                        "required_consecutive_ready_observations": required_observations,
                        "poll_interval_seconds": poll_interval_seconds,
                        "timeout_seconds": timeout_seconds,
                    }
            else:
                stable_name = None
                consecutive = 0
            remaining = deadline - self._monotonic()
            if remaining <= poll_interval_seconds:
                break
            self._sleeper(poll_interval_seconds)
        raise OperationRefusal(
            "GPU_NODE_READY_TIMEOUT",
            f"GPU node did not reach {required_observations} consecutive labeled Ready observations "
            f"within {timeout_seconds} seconds after {observations} reads; "
            f"labeled_nodes={last['labeled_node_count']} ready_nodes={len(last['ready_node_names'])}",
        )

    def _wait_volume_attachment(
        self,
        *,
        volume_id: str,
        instance_id: str,
        timeout_seconds: int = VOLUME_ATTACHMENT_TIMEOUT_SECONDS,
        poll_interval_seconds: int = VOLUME_ATTACHMENT_POLL_INTERVAL_SECONDS,
        required_observations: int = VOLUME_ATTACHMENT_STABLE_OBSERVATIONS,
    ) -> dict[str, Any]:
        """Require stable EC2 attachment before observing the guest device."""

        validate_boundary_parameters(
            "volume_attachment",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            required_observations=required_observations,
        )
        deadline = self._monotonic() + timeout_seconds
        observations = 0
        consecutive = 0
        sequence: list[str] = []
        last_state = "UNOBSERVED"
        while self._monotonic() < deadline:
            value = self.ec2.describe_volumes(VolumeIds=[volume_id])
            try:
                observed = observe_volume_attachment(
                    value, volume_id=volume_id, instance_id=instance_id
                )
            except AsyncObservationRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            observations += 1
            last_state = observed["attachment_state"]
            sequence.append(last_state)
            if observed["status"] == "READY":
                consecutive += 1
                if consecutive >= required_observations:
                    return {
                        "status": "PASS_STABLE_VOLUME_ATTACHMENT",
                        "volume_id": volume_id,
                        "instance_id": instance_id,
                        "observations": observations,
                        "stable_observations": consecutive,
                        "required_stable_observations": required_observations,
                        "observation_sequence": sequence,
                        "poll_interval_seconds": poll_interval_seconds,
                        "timeout_seconds": timeout_seconds,
                    }
            else:
                consecutive = 0
            remaining = deadline - self._monotonic()
            if remaining <= poll_interval_seconds:
                break
            self._sleeper(poll_interval_seconds)
        raise OperationRefusal(
            "VOLUME_ATTACHMENT_TIMEOUT",
            f"volume {volume_id} did not reach {required_observations} stable attached "
            f"observations within {timeout_seconds} seconds after {observations} reads; "
            f"last_state={last_state}",
        )

    def _ssm(
        self,
        instance_id: str,
        commands: list[str],
        *,
        timeout_seconds: int = 900,
        diagnostic_path: Path | None = None,
        pre_model_safe_output: bool = False,
    ) -> dict[str, Any]:
        validate_boundary_parameters("ssm", timeout_seconds=timeout_seconds)
        if self._ssm_runner is not None:
            value = self._ssm_runner(
                instance_id, commands, timeout_seconds=timeout_seconds
            )
            terminal = {
                "command_id": value["command_id"],
                "status": value["status"],
                "response_code": value.get("response_code", 0),
                "stdout": value.get("stdout", ""),
                "stderr": value.get("stderr", ""),
            }
        else:
            response = self.ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                TimeoutSeconds=timeout_seconds,
                Parameters={"commands": commands},
            )
            command_id = response["Command"]["CommandId"]
            stop = time.monotonic() + timeout_seconds + 60
            terminal = None
            while time.monotonic() < stop:
                try:
                    observed = self.ssm.get_command_invocation(
                        CommandId=command_id, InstanceId=instance_id
                    )
                except self.ssm.exceptions.InvocationDoesNotExist:
                    self._sleeper(2)
                    continue
                status = observed["Status"]
                if status in {"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"}:
                    terminal = {
                        "command_id": command_id,
                        "status": status,
                        "response_code": observed.get("ResponseCode"),
                        "stdout": observed.get("StandardOutputContent", ""),
                        "stderr": observed.get("StandardErrorContent", ""),
                    }
                    break
                self._sleeper(2)
            if terminal is None:
                raise OperationRefusal(
                    "SSM_COMMAND_TIMEOUT", f"SSM command {command_id} exceeded its bound"
                )
        stdout = terminal["stdout"]
        stderr = terminal["stderr"]
        diagnostic = {
            "schema_version": 1,
            "classification": (
                "PRE_MODEL_PRE_AUDIO_SAFE_DIAGNOSTICS"
                if pre_model_safe_output
                else "HASH_ONLY_POST_MODEL_DIAGNOSTICS"
            ),
            "command_id": terminal["command_id"],
            "status": terminal["status"],
            "response_code": terminal["response_code"],
            "command_count": len(commands),
            "command_values_recorded": False,
            "stdout_bytes": len(stdout.encode()),
            "stderr_bytes": len(stderr.encode()),
            "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
            "stdout_sanitized": sanitize_bytes(stdout) if pre_model_safe_output else None,
            "stderr_sanitized": sanitize_bytes(stderr) if pre_model_safe_output else None,
            "credentials_or_presigned_query_values_recorded": False,
        }
        if diagnostic_path is not None:
            write_exclusive(diagnostic_path, canonical_json(diagnostic))
        if terminal["status"] != "Success":
            detail = f"SSM command {terminal['command_id']} ended {terminal['status']}"
            if pre_model_safe_output and diagnostic["stderr_sanitized"]:
                detail += f": {diagnostic['stderr_sanitized'][:256]}"
            if diagnostic_path is not None:
                detail += f"; diagnostic_sha256={_sha(diagnostic_path)}"
            raise OperationRefusal("SSM_COMMAND_REFUSED", detail)
        return {
            "command_id": terminal["command_id"],
            "status": terminal["status"],
            "response_code": terminal["response_code"],
            "stdout_sha256": diagnostic["stdout_sha256"],
            "stderr_sha256": diagnostic["stderr_sha256"],
            "stdout": stdout,
            "diagnostic_sha256": _sha(diagnostic_path) if diagnostic_path else None,
        }

    def _dra_readiness(
        self,
        waiter: Callable[..., dict[str, Any]],
        *,
        kubeconfig: Path,
        timeout_seconds: int = DRA_WAIT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Single validated entry point shared by real and injected waiters."""
        return invoke_dra_waiter(
            waiter,
            kubeconfig=kubeconfig,
            timeout_seconds=timeout_seconds,
        )

    def _capture_dra_refusal_diagnostics(
        self,
        context: AttemptContext,
        *,
        readiness_error: Exception,
    ) -> dict[str, Any]:
        """Persist bounded pre-model DRA diagnostics before cleanup.

        This stage runs before any evaluation Pod, model, audio, transcript or
        prediction exists in Kubernetes. Policy v2 therefore permits the safe,
        bounded status, event and log text retained here. Every query is best
        effort: one unavailable diagnostic source must not prevent the other
        sources, or the terminal refusal receipt, from being written.
        """

        def query_json(*args: str) -> dict[str, Any]:
            try:
                value = self._kubectl(
                    context, *args, timeout=30, json_output=True
                )
                if isinstance(value, dict):
                    return {"status": "CAPTURED", "response": value}
                return {"status": "MALFORMED", "safe_error_text": "response is not an object"}
            except Exception as exc:  # Diagnostics must remain best effort.
                return {
                    "status": "UNAVAILABLE",
                    "safe_error_text": sanitize_bytes(str(exc)),
                }

        def describe(*args: str) -> dict[str, Any]:
            try:
                raw = self._kubectl(context, "describe", *args, timeout=30)
                body = raw if isinstance(raw, bytes) else canonical_json(raw)
                return {
                    "status": "CAPTURED",
                    "raw_bytes": len(body),
                    "raw_sha256": hashlib.sha256(body).hexdigest(),
                    "sanitized_text": sanitize_bytes(body),
                }
            except Exception as exc:
                return {
                    "status": "UNAVAILABLE",
                    "safe_error_text": sanitize_bytes(str(exc)),
                }

        def safe_conditions(values: Any) -> list[dict[str, Any]]:
            if not isinstance(values, list):
                return []
            return [{
                "type": item.get("type"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "message": sanitize_bytes(item.get("message")),
                "last_probe_time": item.get("lastProbeTime"),
                "last_transition_time": item.get("lastTransitionTime"),
            } for item in values[:16] if isinstance(item, dict)]

        def safe_container_statuses(values: Any) -> list[dict[str, Any]]:
            if not isinstance(values, list):
                return []
            result: list[dict[str, Any]] = []
            for item in values[:16]:
                if not isinstance(item, dict):
                    continue
                states: dict[str, Any] = {}
                for state_name in ("waiting", "running", "terminated"):
                    state = item.get("state", {}).get(state_name)
                    if not isinstance(state, dict):
                        continue
                    states[state_name] = {
                        key: sanitize_bytes(value) if key == "message" else value
                        for key, value in state.items()
                        if key in {
                            "reason", "message", "exitCode", "signal",
                            "startedAt", "finishedAt", "containerID",
                        }
                    }
                result.append({
                    "name": item.get("name"),
                    "ready": item.get("ready"),
                    "started": item.get("started"),
                    "restart_count": item.get("restartCount"),
                    "image": item.get("image"),
                    "image_id": item.get("imageID"),
                    "state": states,
                })
            return result

        daemonset_raw = query_json(
            "get", "daemonset", "dra-driver-nvidia-gpu-kubelet-plugin",
            "-n", "nvidia-dra-driver",
        )
        pods_raw = query_json(
            "get", "pods", "-n", "nvidia-dra-driver",
            "-l", "dra-driver-nvidia-gpu-component=kubelet-plugin",
        )
        events_raw = query_json(
            "get", "events", "-n", "nvidia-dra-driver",
            "--sort-by=.metadata.creationTimestamp",
        )
        device_class_raw = query_json("get", "deviceclass", "gpu.nvidia.com")
        resource_slices_raw = query_json("get", "resourceslices")

        daemonset: dict[str, Any] = {}
        if daemonset_raw["status"] == "CAPTURED":
            value = daemonset_raw["response"]
            status = value.get("status", {}) if isinstance(value, dict) else {}
            daemonset = {
                "status": "CAPTURED",
                "generation": value.get("metadata", {}).get("generation"),
                "observed_generation": status.get("observedGeneration"),
                "desired": status.get("desiredNumberScheduled"),
                "current": status.get("currentNumberScheduled"),
                "ready": status.get("numberReady"),
                "available": status.get("numberAvailable"),
                "unavailable": status.get("numberUnavailable"),
                "conditions": safe_conditions(status.get("conditions")),
            }
        else:
            daemonset = daemonset_raw

        pods: list[dict[str, Any]] = []
        pod_items = (
            pods_raw.get("response", {}).get("items", [])
            if pods_raw["status"] == "CAPTURED"
            else []
        )
        logs: list[dict[str, Any]] = []
        for pod in pod_items[:4]:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            spec = pod.get("spec", {})
            pod_name = metadata.get("name")
            pods.append({
                "name": pod_name,
                "uid": metadata.get("uid"),
                "deletion_timestamp": metadata.get("deletionTimestamp"),
                "node_name": spec.get("nodeName"),
                "phase": status.get("phase"),
                "reason": status.get("reason"),
                "message": sanitize_bytes(status.get("message")),
                "conditions": safe_conditions(status.get("conditions")),
                "init_container_statuses": safe_container_statuses(
                    status.get("initContainerStatuses")
                ),
                "container_statuses": safe_container_statuses(
                    status.get("containerStatuses")
                ),
            })
            container_names = [
                item.get("name")
                for family in ("initContainers", "containers")
                for item in spec.get(family, [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ]
            for container_name in container_names[:8]:
                try:
                    raw = self._kubectl(
                        context, "logs", "-n", "nvidia-dra-driver", str(pod_name),
                        "-c", container_name, "--tail=200", "--timestamps=true",
                        timeout=30,
                    )
                    body = raw if isinstance(raw, bytes) else canonical_json(raw)
                    logs.append({
                        "pod": pod_name,
                        "container": container_name,
                        "status": "CAPTURED",
                        "raw_bytes": len(body),
                        "raw_sha256": hashlib.sha256(body).hexdigest(),
                        "sanitized_text": sanitize_bytes(body),
                    })
                except Exception as exc:  # Preserve other diagnostics.
                    logs.append({
                        "pod": pod_name,
                        "container": container_name,
                        "status": "UNAVAILABLE",
                        "safe_error_text": sanitize_bytes(str(exc)),
                    })

        events: list[dict[str, Any]] = []
        if events_raw["status"] == "CAPTURED":
            for item in events_raw.get("response", {}).get("items", [])[-100:]:
                involved = item.get("involvedObject", {})
                events.append({
                    "type": item.get("type"),
                    "reason": item.get("reason"),
                    "count": item.get("count"),
                    "first_timestamp": item.get("firstTimestamp"),
                    "last_timestamp": item.get("lastTimestamp"),
                    "event_time": item.get("eventTime"),
                    "object_kind": involved.get("kind"),
                    "object_name": involved.get("name"),
                    "message": sanitize_bytes(item.get("message") or item.get("note")),
                })

        diagnostic = {
            "schema_version": 1,
            "status": "CAPTURED_BEFORE_DRA_CLEANUP",
            "classification": "PRE_MODEL_PRE_AUDIO_SAFE_DIAGNOSTICS",
            "readiness_error_class": type(readiness_error).__name__,
            "readiness_error_text": sanitize_bytes(str(readiness_error)),
            "daemonset": daemonset,
            "daemonset_describe": describe(
                "daemonset/dra-driver-nvidia-gpu-kubelet-plugin",
                "-n", "nvidia-dra-driver",
            ),
            "pods_query_status": pods_raw["status"],
            "pods": pods,
            "events_query_status": events_raw["status"],
            "events": events,
            "device_class_query_status": device_class_raw["status"],
            "device_class": (
                {
                    "name": device_class_raw["response"].get("metadata", {}).get("name")
                }
                if device_class_raw["status"] == "CAPTURED"
                else device_class_raw
            ),
            "resource_slices_query_status": resource_slices_raw["status"],
            "resource_slices": (
                [{
                    "name": item.get("metadata", {}).get("name"),
                    "driver": item.get("spec", {}).get("driver"),
                    "node_name": item.get("spec", {}).get("nodeName"),
                    "device_count": len(item.get("spec", {}).get("devices", [])),
                } for item in resource_slices_raw["response"].get("items", [])[:16]]
                if resource_slices_raw["status"] == "CAPTURED"
                else resource_slices_raw
            ),
            "logs": logs,
            "bounds": {
                "command_timeout_seconds": 30,
                "maximum_pods": 4,
                "maximum_containers_per_pod": 8,
                "maximum_events": 100,
                "maximum_log_lines_per_container": 200,
                "maximum_sanitized_text_bytes_per_field": 4096,
            },
            "contains_model_audio_transcript_prediction_credentials_or_phi": False,
        }
        path = context.workdir / "dra-refusal-diagnostics.json"
        write_exclusive(path, canonical_json(diagnostic))
        return {
            "path": str(path),
            "sha256": _sha(path),
            "status": diagnostic["status"],
            "pod_count": len(pods),
            "event_count": len(events),
            "log_capture_count": len(logs),
        }

    def deadline_identity_and_acceptance(
        self,
        context: AttemptContext,
        *,
        dry_run: bool = False,
        caller_arn: str | None = None,
    ) -> dict[str, Any]:
        if context.authorization_path is None or context.packet_path is None:
            raise OperationRefusal("AUTHORIZATION_PATH_ABSENT", "authorization and packet paths are required")
        bindings = context.bindings
        observed_caller = caller_arn
        if observed_caller is None and not dry_run:
            observed_caller = self.sts.get_caller_identity()["Arn"]
        if self.session.region_name != REGION or observed_caller != CALLER:
            raise OperationRefusal("AWS_IDENTITY_DIFFERS", "AWS account, principal or region differs")
        try:
            authorization = json.loads(context.authorization_path.read_bytes())
        except Exception as exc:
            raise OperationRefusal("AUTHORIZATION_MALFORMED", "successor authorization is unreadable") from exc
        expected_auth = bindings["authorization"]
        validate_authorization_payload(
            authorization,
            expected_id=expected_auth["id"],
            packet_sha256=context.receipts.packet_sha256,
            risk_sha256=bindings["risk_acceptance_sha256"],
            attempt=context.attempt,
            expected_seconds_each=bound_attempt_window(bindings)["seconds_each"],
        )
        try:
            source_integrity = validate_executor_module_bindings(
                self.root,
                bindings.get("executor_modules"),
            )
        except PilotIntegrityRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        if context.attempt >= 16:
            try:
                proven_commands = validate_proven_command_bindings(
                    self.root,
                    bindings.get("proven_live_node_commands"),
                )
            except ProvenCommandRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
        else:
            proven_commands = {
                "status": "NOT_APPLICABLE_HISTORICAL_ATTEMPT",
                "attempt": context.attempt,
            }
        dra_policy_binding = bindings.get("dra_network_policy")
        if not isinstance(dra_policy_binding, dict):
            raise OperationRefusal(
                "DRA_NETWORK_POLICY_BINDING_ABSENT",
                "the evaluation-only DRA network-policy binding is absent",
            )
        dra_policy_path = self.root / str(dra_policy_binding.get("path", ""))
        try:
            dra_policy_body = read_committed_artifact(self.root, dra_policy_path)
        except PilotIntegrityRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        if (
            dra_policy_path.relative_to(self.root) != DRA_NETWORK_POLICY
            or hashlib.sha256(dra_policy_body).hexdigest()
            != dra_policy_binding.get("sha256")
            or dra_policy_binding.get("kubernetes_api_service_ip") != "10.100.0.1"
            or dra_policy_binding.get("allowed_protocol") != "TCP"
            or dra_policy_binding.get("allowed_port") != 443
            or dra_policy_binding.get("other_egress_permitted") is not False
        ):
            raise OperationRefusal(
                "DRA_NETWORK_POLICY_BINDING_DIFFERS",
                "the committed DRA API-egress policy differs from its reviewed binding",
            )
        expires = datetime.fromisoformat(authorization["expires_utc"].replace("Z", "+00:00"))
        if _utc() >= expires:
            raise OperationRefusal("RISK_ACCEPTANCE_EXPIRED", "offline evaluation acceptance has expired")
        if _sha(context.packet_path) != context.receipts.packet_sha256 or _sha(context.authorization_path) != context.receipts.authorization_sha256:
            raise OperationRefusal("REVIEWED_FILE_HASH_DIFFERS", "packet or authorization changed after binding")
        if context.attempt >= 9:
            prestage_binding = bindings.get("artifact_prestage_proof")
            if not isinstance(prestage_binding, dict):
                raise OperationRefusal(
                    "COMMITTED_PRESTAGE_PROOF_ABSENT",
                    "attempt 9 or later requires the committed complete-bundle pre-stage proof",
                )
            prestage_path = self.root / str(prestage_binding.get("path", ""))
            try:
                prestage_body = read_committed_artifact(self.root, prestage_path)
                if hashlib.sha256(prestage_body).hexdigest() != prestage_binding.get("sha256"):
                    raise StagingRefusal("PRESTAGE_PROOF_HASH_DIFFERS", "pre-stage proof hash differs")
                prestage = json.loads(prestage_body)
                structure = validate_prestage_proof(
                    prestage,
                    expected_bundle_sha256=bindings["pilot_bundle"]["sha256"],
                )
                budget = validate_window_budget(
                    prestage,
                    deadline_seconds=context.deadline_seconds,
                    expected_bundle_sha256=bindings["pilot_bundle"]["sha256"],
                )
            except (PilotIntegrityRefusal, StagingRefusal) as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            except Exception as exc:
                raise OperationRefusal(
                    "COMMITTED_PRESTAGE_PROOF_MALFORMED",
                    "committed complete-bundle pre-stage proof is malformed",
                ) from exc
        else:
            structure = {"status": "NOT_APPLICABLE_HISTORICAL_ATTEMPT"}
            budget = {"status": "NOT_APPLICABLE_HISTORICAL_ATTEMPT"}
        try:
            lineage = validate_governance_commit_boundary(
                self.root,
                reviewed_commit=authorization["reviewed_repository_commit"],
                authorization_path=context.authorization_path,
                deadline_dry_run_path=self.root / authorization["pre_execution_dry_run"]["path"],
            )
        except PilotIntegrityRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        common = {
            "status": "PASS_DEADLINE_IDENTITY_AND_ACCEPTANCE",
            "caller": CALLER,
            "attempt": context.attempt,
            "dry_run": dry_run,
            "source_integrity": source_integrity,
            "proven_live_node_commands": proven_commands,
            "dra_network_policy": {
                "status": "PASS_COMMITTED_DRA_API_EGRESS_BINDING",
                "path": str(DRA_NETWORK_POLICY),
                "sha256": hashlib.sha256(dra_policy_body).hexdigest(),
                "kubernetes_api_service_ip": "10.100.0.1",
                "allowed_protocol": "TCP",
                "allowed_port": 443,
                "other_egress_permitted": False,
            },
            "reviewed_commit_lineage": lineage,
            "artifact_prestage": structure,
            "window_budget": budget,
        }
        if dry_run:
            return {
                **common,
                "aws_calls": 0,
                "aws_mutations": 0,
                "scheduled_action_created": False,
            }
        state = self._state(context)
        action = f"medzen-asr-eval-a{context.attempt}-deadline"
        deadline = _utc() + timedelta(seconds=context.deadline_seconds)
        gpu_asg = self._gpu_asg(context)
        self.asg.put_scheduled_update_group_action(
            AutoScalingGroupName=gpu_asg,
            ScheduledActionName=action,
            StartTime=deadline,
            DesiredCapacity=0,
        )
        state["deadline_action"] = action
        self._save_state(context, state)
        readback = self.asg.describe_scheduled_actions(AutoScalingGroupName=gpu_asg, ScheduledActionNames=[action])["ScheduledUpdateGroupActions"]
        if len(readback) != 1 or readback[0].get("DesiredCapacity") != 0:
            raise OperationRefusal("DEADLINE_READBACK_DIFFERS", "deadline scheduled action is not exact")
        return {**common, "deadline_utc": deadline.isoformat()}

    def input_freeze_and_no_phi(self, context: AttemptContext) -> dict[str, Any]:
        binding = context.bindings["input_freeze"]
        manifest_root = context.workdir / "manifests"
        manifest_root.mkdir(parents=True, exist_ok=False)
        _, sync_retry = self._idempotent_read_command(
            "S3_READ",
            "input-freeze-manifest-sync",
            [
                "aws", "--profile", PROFILE, "--region", REGION, "s3", "sync",
                "s3://medzen-speech/eval/", str(manifest_root), "--exclude", "*", "--include", "*/asr/*/manifest*.jsonl",
            ],
            hard_cap_seconds=S3_READ_RETRY_HARD_CAP_SECONDS,
        )
        outputs = []
        for run in (1, 2):
            completed = self._command([
                sys.executable, "scripts/audit_asr_base_model_eval_inputs.py",
                "--manifest-root", str(manifest_root),
                "--data-commit", binding["data_commit"],
                "--source-inventory-sha256", binding["source_inventory_sha256"],
                "--correction-record-sha256", binding["correction_record_sha256"],
                "--correction-addendum-sha256", binding["correction_addendum_sha256"],
                "--recorded-utc", binding["recorded_utc"],
            ], cwd=self.root)
            path = context.workdir / f"input-freeze-{run}.json"
            write_exclusive(path, completed.stdout)
            outputs.append(completed.stdout)
        if outputs[0] != outputs[1] or hashlib.sha256(outputs[0]).hexdigest() != binding["canonical_sha256"]:
            raise OperationRefusal("INPUT_FREEZE_REPRODUCTION_DIFFERS", "two input-freeze runs are not the packet-bound PASS")
        audit = json.loads(outputs[0])
        if audit.get("status") != "PASS_INPUT_FREEZE" or audit["inventory"]["rows"] != 24230:
            raise OperationRefusal("INPUT_FREEZE_NOT_PASS", "evaluation freeze is not PASS")
        suite = binding.get("suite_selection")
        if suite is not None:
            selection = select_suite_rows(manifest_root, suite["units"])
            if (
                selection["public_row_list_sha256"] != suite["row_list_sha256"]
                or len(selection["rows"]) != suite["expected_rows"]
            ):
                raise OperationRefusal(
                    "SUITE_ROW_LIST_DIFFERS",
                    "deterministic suite shard row list differs",
                )
        else:
            selection = select_pilot_rows(manifest_root)
            if selection["public_row_list_sha256"] != binding["pilot_row_list_sha256"] or len(selection["rows"]) > 540:
                raise OperationRefusal("PILOT_ROW_LIST_DIFFERS", "deterministic pilot row list differs")
        write_exclusive(context.workdir / "pilot-selection.json", canonical_json(selection))
        return {"status": "PASS_INPUT_FREEZE_AND_NO_PHI", "runs": 2, "byte_identical": True, "rows": audit["inventory"]["rows"], "pilot_rows": len(selection["rows"]), "pilot_row_list_sha256": selection["public_row_list_sha256"], "phi": False, "s3_read_retry": sync_retry}

    def cost_and_zero_state(self, context: AttemptContext) -> dict[str, Any]:
        for name in (CPU_NODEGROUP, GPU_NODEGROUP):
            group = self._nodegroup(name)
            if group["status"] != "ACTIVE" or group["scalingConfig"]["desiredSize"] != 0 or group.get("health", {}).get("issues"):
                raise OperationRefusal("NODEGROUP_ZERO_STATE_DIFFERS", f"{name} nodegroup is not healthy at desired zero")
        self._update_kubeconfig(context)
        namespaces = self._kubectl(context, "get", "namespaces", json_output=True)
        names = {item["metadata"]["name"] for item in namespaces.get("items", [])}
        if NAMESPACE in names or "nvidia-dra-driver" in names:
            raise OperationRefusal("KUBERNETES_ZERO_STATE_DIFFERS", "evaluation or DRA namespace already exists")
        endpoints = self.ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [VPC]}, {"Name": "tag:MedZenPurpose", "Values": ["asr-base-model-eval"]}])["VpcEndpoints"]
        if endpoints:
            raise OperationRefusal("TEMPORARY_ENDPOINT_RESIDUALS", "evaluation VPC endpoints already exist")
        validate_plan(exact_plan(context.bindings, context.attempt), context.bindings, context.attempt)
        cost = json.loads((self.root / context.bindings["cost_registry"]["path"]).read_bytes())
        if _sha(self.root / context.bindings["cost_registry"]["path"]) != context.bindings["cost_registry"]["sha256"]:
            raise OperationRefusal("COST_REGISTRY_HASH_DIFFERS", "cost registry differs")
        summary = cost["guardrail_summary"]
        headroom = summary["guardrail_headroom_after_reservations_usd"]
        if float(headroom) < 10:
            raise OperationRefusal("COST_HEADROOM_INSUFFICIENT", "less than $10 headroom remains")
        state = self._state(context)
        state["reservation"] = True
        self._save_state(context, state)
        return {"status": "PASS_COST_AND_ZERO_STATE", "reservation_usd": 10.0, "headroom_before_usd": headroom, "cpu": 0, "gpu": 0, "temporary_endpoints": 0}

    def _image_scan(
        self,
        repository: str,
        digest: str,
        *,
        expected_highs: set[tuple[str, str, str, str]] | None = None,
    ) -> dict[str, Any]:
        expected = EXPECTED_HIGHS if expected_highs is None else expected_highs
        stop = time.monotonic() + 1800
        while time.monotonic() < stop:
            try:
                response = self.ecr.describe_image_scan_findings(repositoryName=repository, imageId={"imageDigest": digest})
            except Exception:
                self._sleeper(10)
                continue
            if response.get("imageScanStatus", {}).get("status") == "COMPLETE":
                findings = response.get("imageScanFindings", {}).get("enhancedFindings") or response.get("imageScanFindings", {}).get("findings", [])
                normalized = set()
                for finding in findings:
                    details = finding.get("packageVulnerabilityDetails", {})
                    package = (details.get("vulnerablePackages") or [{}])[0]
                    attributes = {
                        item.get("key"): item.get("value")
                        for item in finding.get("attributes", [])
                        if isinstance(item, dict)
                    }
                    normalized.add((
                        finding.get("name") or details.get("vulnerabilityId"),
                        package.get("name") or attributes.get("package_name"),
                        package.get("version") or attributes.get("package_version"),
                        finding.get("severity"),
                    ))
                critical = {value for value in normalized if value[3] == "CRITICAL"}
                high = {value for value in normalized if value[3] == "HIGH"}
                if critical or high != expected:
                    raise OperationRefusal("AUTHORITATIVE_SCAN_FINDINGS_DIFFER", "authoritative critical/high tuple set differs")
                return {"status": "COMPLETE", "critical": 0, "high": len(high), "high_tuples": sorted(high)}
            self._sleeper(10)
        raise OperationRefusal("AUTHORITATIVE_SCAN_TIMEOUT", "ECR scan did not complete")

    def _wait_registry_scanning_configuration(
        self, expected: dict[str, Any], *, timeout_seconds: int = 120
    ) -> dict[str, Any]:
        validate_boundary_parameters(
            "registry_scan_configuration", timeout_seconds=timeout_seconds
        )
        expected_canonical = canonical_configuration(expected)
        stop = time.monotonic() + timeout_seconds
        stable = 0
        observed: dict[str, Any] = {}
        while time.monotonic() < stop:
            observed = self.ecr.get_registry_scanning_configuration()[
                "scanningConfiguration"
            ]
            if canonical_configuration(observed) == expected_canonical:
                stable += 1
                if stable == 2:
                    return observed
            else:
                stable = 0
            self._sleeper(2)
        raise OperationRefusal(
            "ECR_SCAN_CONFIGURATION_STABILITY_TIMEOUT",
            "ECR scanning configuration did not reach two stable exact observations",
        )

    def _existing_exact_image(self, image: dict[str, Any]) -> dict[str, Any]:
        response = self.ecr.batch_get_image(
            repositoryName=ECR_REPOSITORY,
            imageIds=[{"imageTag": image["tag"]}],
            acceptedMediaTypes=["application/vnd.oci.image.index.v1+json"],
        )
        index = response.get("images", [])
        if len(index) != 1 or index[0]["imageId"]["imageDigest"] != image["oci_index_digest"]:
            raise OperationRefusal("IMMUTABLE_IMAGE_TAG_OCCUPIED", "evaluation tag is absent or bound to a different image")
        raw = index[0].get("imageManifest", "").encode()
        if hashlib.sha256(raw).hexdigest() != image["oci_index_digest"].removeprefix("sha256:"):
            raise OperationRefusal("ECR_INDEX_BYTES_DIFFER", "ECR index bytes differ from the bound digest")
        manifest = json.loads(raw)
        children = [
            item for item in manifest["manifests"]
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == "amd64"
        ]
        if len(children) != 1 or children[0]["digest"] != image["linux_amd64_digest"]:
            raise OperationRefusal("ECR_CHILD_DIGEST_DIFFERS", "ECR child differs from the bound linux/amd64 digest")
        return {"status": "PASS_EXACT_IMAGE_ALREADY_PRESENT", "index": index[0], "child": children[0]}

    def _digest_verified_security_gate(
        self, context: AttemptContext, image: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Run the packet-bound Scout gate against bytes pulled back from ECR."""
        gate_binding = validate_security_binding(
            context.bindings.get("security_gate", {})
        )

        def scan_read() -> dict[str, Any]:
            with tempfile.TemporaryDirectory(
                prefix="digest-rescan-", dir=context.workdir
            ) as temporary:
                value = self._digest_scanner(
                    self.ecr,
                    ECR_REPOSITORY,
                    image,
                    Path(temporary),
                )
                retained_sarif = context.workdir / "docker-scout-ecr-rescan.sarif.json"
                source_sarif = Path(temporary) / "docker-scout.sarif.json"
                retained_sarif.unlink(missing_ok=True)
                write_exclusive(retained_sarif, source_sarif.read_bytes())
                value["docker_scout"]["sarif_path"] = str(retained_sarif)
                value["docker_scout"].pop("scanned_oci_layout", None)
                return value

        try:
            scan, scan_retry = self._idempotent_read_composition(
                ("ECR_PULL_BACK", "SCOUT_DATABASE_READ"),
                "exact-child-and-scout-read",
                scan_read,
                hard_cap_seconds=IMAGE_SCAN_READ_RETRY_HARD_CAP_SECONDS,
            )
        except DigestRescanRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        return gate_binding, scan, scan_retry

    def image_publication_and_scan(self, context: AttemptContext) -> dict[str, Any]:
        image = context.bindings["image"]
        publication_required = image.get("publication_required", False)
        if not isinstance(publication_required, bool):
            raise OperationRefusal(
                "IMAGE_PUBLICATION_REQUIREMENT_MALFORMED",
                "image publication requirement must be boolean",
            )
        state = self._state(context)
        try:
            repository_response, repository_retry = self._idempotent_read(
                "ECR_PULL_BACK",
                "evaluation-repository-read",
                lambda: self.ecr.describe_repositories(
                    repositoryNames=[ECR_REPOSITORY]
                ),
                hard_cap_seconds=IMAGE_SCAN_READ_RETRY_HARD_CAP_SECONDS,
            )
            repository = repository_response["repositories"][0]
        except self.ecr.exceptions.RepositoryNotFoundException:
            raise OperationRefusal(
                "ECR_EVALUATION_REPOSITORY_ABSENT",
                "the packet-2026-002A evaluation repository does not exist",
            )
        if repository["imageTagMutability"] != "IMMUTABLE" or repository["encryptionConfiguration"]["encryptionType"] != "KMS":
            raise OperationRefusal("ECR_REPOSITORY_BOUNDARY_DIFFERS", "evaluation repository is not immutable and KMS-encrypted")
        if context.attempt >= 5 and not publication_required:
            exact, identity_retry = self._idempotent_read(
                "ECR_PULL_BACK",
                "exact-image-identity-read",
                lambda: self._existing_exact_image(image),
                hard_cap_seconds=IMAGE_SCAN_READ_RETRY_HARD_CAP_SECONDS,
            )
            gate_binding, scan, scan_retry = self._digest_verified_security_gate(
                context, image
            )
            return {
                "status": "PASS_IMAGE_PUBLICATION_AND_SCAN",
                "repository": ECR_REPOSITORY,
                "oci_index_digest": image["oci_index_digest"],
                "linux_amd64_digest": exact["child"]["digest"],
                "publication": {"status": "SKIPPED_EXISTING_EXACT_IMAGE", "aws_image_mutations": 0},
                "security_gate_binding": gate_binding,
                "security_gate": scan,
                "read_retry_audit": {
                    "repository": repository_retry,
                    "identity": identity_retry,
                    "scan": scan_retry,
                },
            }
        local = self._command(["docker", "image", "inspect", image["local_tag"], "--format", "{{json .Config.Labels}}"])
        labels = json.loads(local.stdout)
        if labels.get("org.opencontainers.image.revision") != image["source_commit"] or labels.get("io.medzen.classification") != "offline-evaluation-only":
            raise OperationRefusal("LOCAL_IMAGE_LABELS_DIFFER", "local image provenance labels differ")
        registry = self.ecr.get_registry_scanning_configuration()["scanningConfiguration"]
        state["scan_configuration_before"] = registry
        self._save_state(context, state)
        try:
            updated, changed = merge_scan_on_push_filter(registry, ECR_REPOSITORY)
        except ValueError as exc:
            raise OperationRefusal(
                "ECR_SCAN_CONFIGURATION_AMBIGUOUS", str(exc)
            ) from exc
        if changed:
            self.ecr.put_registry_scanning_configuration(
                scanType=updated["scanType"], rules=updated["rules"]
            )
            self._wait_registry_scanning_configuration(updated)
        existing = self.ecr.batch_get_image(repositoryName=ECR_REPOSITORY, imageIds=[{"imageTag": image["tag"]}], acceptedMediaTypes=["application/vnd.oci.image.index.v1+json"])
        publication: dict[str, Any]
        if existing.get("images"):
            if len(existing["images"]) != 1 or existing["images"][0]["imageId"]["imageDigest"] != image["oci_index_digest"]:
                raise OperationRefusal("IMMUTABLE_IMAGE_TAG_OCCUPIED", "evaluation tag exists with a different image")
            publication = {
                "status": "PASS_EXACT_IMAGE_ALREADY_PRESENT",
                "oci_index_digest": image["oci_index_digest"],
                "uploaded_blob_count": 0,
                "reused_existing_exact_tag": True,
            }
        else:
            try:
                publication = self._oci_publisher(
                    self.ecr,
                    ECR_REPOSITORY,
                    image,
                    work_parent=context.workdir,
                )
            except OciPublicationRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
        response = self.ecr.batch_get_image(repositoryName=ECR_REPOSITORY, imageIds=[{"imageTag": image["tag"]}], acceptedMediaTypes=["application/vnd.oci.image.index.v1+json"])
        index = response.get("images", [])
        if len(index) != 1 or index[0]["imageId"]["imageDigest"] != image["oci_index_digest"]:
            raise OperationRefusal("ECR_INDEX_DIGEST_DIFFERS", "pushed OCI index differs")
        manifest = json.loads(index[0]["imageManifest"])
        children = [item for item in manifest["manifests"] if item.get("platform", {}).get("os") == "linux" and item.get("platform", {}).get("architecture") == "amd64"]
        if len(children) != 1 or children[0]["digest"] != image["linux_amd64_digest"]:
            raise OperationRefusal("ECR_CHILD_DIGEST_DIFFERS", "scan subject differs from the bound linux/amd64 child")
        ecr_basic = self._image_scan(
            ECR_REPOSITORY, children[0]["digest"], expected_highs=set()
        )
        ecr_basic["status"] = "PASS_ECR_BASIC_OS_GATE"
        gate_binding, scan, scan_retry = self._digest_verified_security_gate(
            context, image
        )
        return {
            "status": "PASS_IMAGE_PUBLICATION_AND_SCAN",
            "repository": ECR_REPOSITORY,
            "oci_index_digest": image["oci_index_digest"],
            "linux_amd64_digest": children[0]["digest"],
            "publication": publication,
            "ecr_basic": ecr_basic,
            "security_gate_binding": gate_binding,
            "security_gate": scan,
            "read_retry_audit": {"repository": repository_retry, "scan": scan_retry},
        }

    def artifact_stage(self, context: AttemptContext) -> dict[str, Any]:
        expected = context.bindings["pilot_bundle"]
        proof_binding = context.bindings["artifact_prestage_proof"]
        proof_path = self.root / proof_binding["path"]
        bundle_path = context.workdir / "pilot-bundle.json"
        try:
            proof_body = read_committed_artifact(self.root, proof_path)
            if hashlib.sha256(proof_body).hexdigest() != proof_binding["sha256"]:
                raise StagingRefusal("PRESTAGE_PROOF_HASH_DIFFERS", "pre-stage proof hash differs")
            proof = json.loads(proof_body)
            def verify_bundle_read() -> dict[str, Any]:
                bundle_path.unlink(missing_ok=True)
                return verify_prestaged_bundle(
                    self.s3,
                    proof,
                    expected_bundle_sha256=expected["sha256"],
                    destination=bundle_path,
                )

            verification, bundle_retry = self._idempotent_read(
                "S3_READ",
                "pre-staged-bundle-verify",
                verify_bundle_read,
                hard_cap_seconds=S3_READ_RETRY_HARD_CAP_SECONDS,
            )
            bundle = json.loads(bundle_path.read_bytes())
            model_bindings = [
                item
                for item in bundle.get("objects", [])
                if item.get("key", "").endswith("/model-bindings.json")
            ]
            if len(model_bindings) != 1:
                raise StagingRefusal(
                    "MODEL_BINDINGS_OBJECT_AMBIGUOUS",
                    "the verified pilot bundle must bind exactly one model-bindings object",
                )
            model_binding = model_bindings[0]
            model_binding_path = context.workdir / "asset-staging/model-bindings.json"
            model_binding_path.parent.mkdir(parents=True, exist_ok=True)
            model_binding_partial = model_binding_path.with_suffix(".json.partial")

            def download_model_binding() -> None:
                model_binding_partial.unlink(missing_ok=True)
                with model_binding_partial.open("xb") as stream:
                    self.s3.download_fileobj(
                        BUCKET,
                        model_binding["key"],
                        stream,
                        ExtraArgs={"VersionId": model_binding["version_id"]},
                    )

            _, model_binding_retry = self._idempotent_read(
                "S3_READ",
                "model-bindings-versioned-download",
                download_model_binding,
                hard_cap_seconds=S3_READ_RETRY_HARD_CAP_SECONDS,
            )
            model_binding_partial.replace(model_binding_path)
            if (
                model_binding_path.stat().st_size != model_binding["bytes"]
                or _sha(model_binding_path) != model_binding["sha256"]
            ):
                raise StagingRefusal(
                    "MODEL_BINDINGS_OBJECT_DIFFERS",
                    "downloaded model bindings differ from the verified bundle",
                )
        except (PilotIntegrityRefusal, StagingRefusal) as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        state = self._state(context)
        state["artifact_prefix"] = proof["prefix"].removeprefix(f"s3://{BUCKET}/")
        self._save_state(context, state)
        return {
            "status": "PASS_ARTIFACT_STAGE",
            "mode": "VERIFY_ONLY_PRESTAGED_BUNDLE",
            "prefix": proof["prefix"],
            "bundle_sha256": expected["sha256"],
            "prestage_proof_sha256": proof_binding["sha256"],
            "create_only": True,
            "hashes_verified": True,
            "verification": verification,
            "s3_read_retry": {
                "bundle": bundle_retry,
                "model_bindings": model_binding_retry,
            },
            "local_model_bindings": {
                "key": model_binding["key"],
                "version_id": model_binding["version_id"],
                "sha256": model_binding["sha256"],
                "bytes": model_binding["bytes"],
            },
            "artifact_upload_bytes": verification["artifact_upload_bytes"],
            "aws_mutations": verification["aws_mutations"],
        }

    def _endpoint_call_inventory(self, context: AttemptContext) -> dict[str, Any]:
        try:
            return build_call_inventory(
                bundle_sha256=context.bindings["pilot_bundle"]["sha256"],
                pilot_bundle=json.loads(
                    (context.workdir / "pilot-bundle.json").read_bytes()
                ),
                model_bindings=json.loads(
                    (context.workdir / "asset-staging/model-bindings.json").read_bytes()
                ),
                account=ACCOUNT,
                region=REGION,
                ecr_repositories=PRIVATE_PULL_REPOSITORIES,
            )
        except EndpointPolicyRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc

    def private_endpoint_and_policy_gate(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        aws = context.bindings["aws"]
        inventory = self._endpoint_call_inventory(context)
        try:
            ecr_policy = derive_policy(inventory, "ecr")
            s3_policy = derive_policy(inventory, "s3")
            ecr_coverage = validate_policy_coverage(inventory, ecr_policy, "ecr")
            s3_coverage = validate_policy_coverage(inventory, s3_policy, "s3")
        except EndpointPolicyRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        inventory_record = {
            **inventory,
            "policy_coverage": {"ecr": ecr_coverage, "s3": s3_coverage},
            "policy_scope": {
                "pilot_prefix": inventory["pilot_prefix"],
                "whisper_prefix": inventory["whisper_prefix"],
                "broader_s3_prefix_permitted": False,
            },
        }
        inventory_path = context.workdir / "endpoint-call-inventory.json"
        write_exclusive(inventory_path, canonical_json(inventory_record))
        sg = self.ec2.create_security_group(GroupName=f"medzen-asr-eval-vpce-a{context.attempt}", Description="MedZen ASR offline evaluation endpoint TLS", VpcId=VPC, TagSpecifications=[{"ResourceType": "security-group", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["GroupId"]
        state["endpoint_security_group"] = sg
        self._save_state(context, state)
        self.ec2.revoke_security_group_egress(GroupId=sg, IpPermissions=[{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        self.ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "UserIdGroupPairs": [{"GroupId": NODE_SG}]}])
        endpoint_ids = []
        for service in ("ecr.api", "ecr.dkr"):
            value = self.ec2.create_vpc_endpoint(VpcEndpointType="Interface", VpcId=VPC, ServiceName=f"com.amazonaws.{REGION}.{service}", SubnetIds=aws["private_subnet_ids"], SecurityGroupIds=[sg], PrivateDnsEnabled=True, PolicyDocument=json.dumps(ecr_policy, sort_keys=True), TagSpecifications=[{"ResourceType": "vpc-endpoint", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["VpcEndpoint"]
            endpoint_ids.append(value["VpcEndpointId"])
            state["endpoint_ids"] = list(endpoint_ids)
            self._save_state(context, state)
        s3_endpoint = self.ec2.create_vpc_endpoint(VpcEndpointType="Gateway", VpcId=VPC, ServiceName=f"com.amazonaws.{REGION}.s3", RouteTableIds=aws["private_route_table_ids"], PolicyDocument=json.dumps(s3_policy, sort_keys=True), TagSpecifications=[{"ResourceType": "vpc-endpoint", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["VpcEndpoint"]
        endpoint_ids.append(s3_endpoint["VpcEndpointId"])
        state["endpoint_ids"] = endpoint_ids
        addon = self.eks.describe_addon(clusterName=CLUSTER, addonName="vpc-cni")["addon"]
        state["cni_addon_before"] = addon.get("configurationValues")
        state["cni_changed"] = True
        self._save_state(context, state)
        config = json.loads(addon.get("configurationValues") or "{}")
        config["enableNetworkPolicy"] = "true"
        self.eks.update_addon(clusterName=CLUSTER, addonName="vpc-cni", configurationValues=json.dumps(config, sort_keys=True), resolveConflicts="PRESERVE")
        self._update_kubeconfig(context)
        env = self._kubectl(context, "get", "daemonset/aws-node", "-n", "kube-system", json_output=True)
        containers = env["spec"]["template"]["spec"]["containers"]
        aws_node = next(item for item in containers if item["name"] == "aws-node")
        state["cni_daemonset_env_before"] = aws_node.get("env", [])
        self._save_state(context, state)
        self._kubectl(context, "set", "env", "daemonset/aws-node", "-n", "kube-system", "NETWORK_POLICY_ENFORCING_MODE=strict")
        stop = time.monotonic() + 900
        while time.monotonic() < stop:
            described = self.ec2.describe_vpc_endpoints(VpcEndpointIds=endpoint_ids)["VpcEndpoints"]
            if len(described) == 3 and all(item["State"] == "available" for item in described):
                break
            self._sleeper(10)
        else:
            raise OperationRefusal("PRIVATE_ENDPOINT_AVAILABILITY_TIMEOUT", "private endpoints did not become available")
        interfaces = [item for item in described if item["VpcEndpointType"] == "Interface"]
        eni_ids = [eni for item in interfaces for eni in item["NetworkInterfaceIds"]]
        enis = self.ec2.describe_network_interfaces(NetworkInterfaceIds=eni_ids)["NetworkInterfaces"]
        endpoint_ips = sorted(item["PrivateIpAddress"] for item in enis)
        prefix_lists = self.ec2.describe_prefix_lists(
            Filters=[
                {
                    "Name": "prefix-list-name",
                    "Values": [f"com.amazonaws.{REGION}.s3"],
                }
            ]
        )["PrefixLists"]
        if len(prefix_lists) != 1:
            raise OperationRefusal(
                "S3_PREFIX_LIST_AMBIGUOUS",
                "the regional S3 managed prefix list is absent or ambiguous",
            )
        prefix_list_id = prefix_lists[0].get("PrefixListId")
        if not isinstance(prefix_list_id, str) or not prefix_list_id:
            raise OperationRefusal(
                "S3_PREFIX_LIST_ID_ABSENT",
                "the regional S3 managed prefix-list response has no identifier",
            )
        prefix_entries = self.ec2.get_managed_prefix_list_entries(PrefixListId=prefix_list_id)["Entries"]
        s3_cidrs = sorted(item["Cidr"] for item in prefix_entries)
        workload = render_k8s(context.bindings, endpoint_ips, s3_cidrs, context.attempt)
        write_exclusive(context.workdir / "workload.yaml", workload.encode())
        network_binding = {
            "schema_version": 1,
            "classification": "OFFLINE_EVALUATION_ONLY",
            "allowed_tcp_443_hosts": [
                f"api.ecr.{REGION}.amazonaws.com",
                f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com",
                f"{BUCKET}.s3.{REGION}.amazonaws.com",
            ],
        }
        write_exclusive(context.workdir / "network-binding.json", canonical_json(network_binding))
        return {"status": "PASS_PRIVATE_ENDPOINT_AND_POLICY_GATE", "endpoint_ids": endpoint_ids, "endpoint_ips": endpoint_ips, "s3_prefix_list_id": prefix_list_id, "s3_cidrs": s3_cidrs, "cni_mode": "strict", "workload_sha256": _sha(context.workdir / "workload.yaml"), "endpoint_call_inventory_sha256": _sha(inventory_path), "endpoint_policy_coverage": {"ecr": ecr_coverage, "s3": s3_coverage}, "pod_dns": {"dns_policy": "None", "nameservers": [VPC_DNS_RESOLVER], "cluster_dns_dependency": False}, "resolve_as_pod_consistency_gate_pending": True, "empirical_pre_torch_probe_pending": True}

    def gpu_and_sampler_gate(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        self.eks.update_nodegroup_config(clusterName=CLUSTER, nodegroupName=GPU_NODEGROUP, scalingConfig={"minSize": 0, "maxSize": 1, "desiredSize": 1})
        state["gpu_scaled"] = True
        self._save_state(context, state)
        stable = self._wait_nodegroup(desired=1)
        instance_id = stable["instance_ids"][0]
        state["instance_id"] = instance_id
        self._save_state(context, state)
        self._update_kubeconfig(context)
        node_readiness = self._wait_gpu_node_ready(context)
        node_name = node_readiness["node_name"]
        state["node_name"] = node_name
        self._save_state(context, state)
        instance = self.ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        volume = self.ec2.create_volume(AvailabilityZone=instance["Placement"]["AvailabilityZone"], Size=60, VolumeType="gp3", Encrypted=True, KmsKeyId=context.bindings["aws"]["ebs_kms_key_arn"], TagSpecifications=[{"ResourceType": "volume", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["VolumeId"]
        state["volume_id"] = volume
        self._save_state(context, state)
        waiter = self.ec2.get_waiter("volume_available")
        waiter.wait(VolumeIds=[volume], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        self.ec2.attach_volume(VolumeId=volume, InstanceId=instance_id, Device="/dev/sdf")
        attachment_readiness = self._wait_volume_attachment(
            volume_id=volume, instance_id=instance_id
        )
        mount_template = volume_mount_command_template()
        try:
            mount_commands = volume_mount_commands(volume)
        except AsyncObservationRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        mount_template_sha256 = hashlib.sha256(
            canonical_json(mount_template)
        ).hexdigest()
        mount_parameter_sha256 = hashlib.sha256(
            canonical_json({"volume_id": volume})
        ).hexdigest()
        mount_commands_sha256 = hashlib.sha256(
            canonical_json(mount_commands)
        ).hexdigest()
        write_exclusive(
            context.workdir / "gpu-node-mount-command-binding.json",
            canonical_json({
                "schema_version": 1,
                "status": "BOUND_TEMPLATE_AND_PARAMETER_FOR_LIVE_PROVENANCE",
                "command_count": len(mount_commands),
                "command_template_sha256": mount_template_sha256,
                "volume_parameter_sha256": mount_parameter_sha256,
                "rendered_command_bundle_sha256": mount_commands_sha256,
                "template_is_volume_independent": True,
                "contains_credentials_phi_audio_reference_or_prediction": False,
                "ec2_attachment_readiness": attachment_readiness,
                "guest_device_timeout_marker": VOLUME_DEVICE_TIMEOUT,
            }),
        )
        self._ssm(instance_id, mount_commands)
        # Mark the namespace as a possible cleanup target before its create
        # call. A timeout after server-side creation can therefore never leak
        # it merely because the client did not receive the success response.
        state["dra_installed"] = True
        self._save_state(context, state)
        existing_namespaces = self._kubectl(
            context, "get", "namespaces", json_output=True
        )
        if any(
            item.get("metadata", {}).get("name") == "nvidia-dra-driver"
            for item in existing_namespaces.get("items", [])
        ):
            raise OperationRefusal(
                "DRA_NAMESPACE_PREEXISTED",
                "the temporary DRA namespace existed before this attempt",
            )
        dra_policy = (self.root / DRA_NETWORK_POLICY).read_bytes()
        self._kubectl(context, "apply", "-f", "-", stdin=dra_policy)
        dra = (self.root / DRA_MANIFEST).read_bytes()
        self._kubectl(context, "apply", "-f", "-", stdin=dra)
        if self._dra_waiter is None:
            from scripts.run_b6a_003c_c_proof import wait_for_stable_dra
            waiter = wait_for_stable_dra
        else:
            waiter = self._dra_waiter
        try:
            readiness = self._dra_readiness(
                waiter,
                kubeconfig=context.workdir / "kubeconfig",
                timeout_seconds=DRA_WAIT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            diagnostic = self._capture_dra_refusal_diagnostics(
                context, readiness_error=exc
            )
            raise OperationRefusal(
                "DRA_STABLE_READINESS_TIMEOUT",
                "DRA stable readiness refused: "
                f"{sanitize_bytes(str(exc))[:256]}; bounded diagnostics persisted "
                f"before cleanup sha256={diagnostic['sha256']}",
            ) from exc
        dra_pod = self._kubectl(context, "get", "pods", "-n", "nvidia-dra-driver", "-l", "dra-driver-nvidia-gpu-component=kubelet-plugin", json_output=True)["items"][0]["metadata"]["name"]
        state = self._state(context)
        state["dra_pod"] = dra_pod
        self._save_state(context, state)
        sample_command = [
            "kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "exec", "-n", "nvidia-dra-driver", dra_pod, "-c", "gpus", "--",
            "/busybox/sh", "-c", sampler_shell_command(),
        ]
        sample = self._command(sample_command, timeout=180, check=False)
        stdout = sample.stdout or b""
        stderr = sample.stderr or b""
        combined = (stdout + b"\n" + stderr).decode(errors="replace")
        parsed: list[tuple[int, int, int]] = []
        malformed_lines = 0
        for raw_line in stdout.decode(errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 3 or any(re.fullmatch(r"[0-9]+", field) is None for field in fields):
                malformed_lines += 1
                continue
            parsed.append(tuple(int(field) for field in fields))
        diagnostic = {
            "schema_version": 1,
            "classification": "PRE_MODEL_PRE_AUDIO_SAFE_DIAGNOSTICS",
            "command_transport": "kubectl_exec_dra_gpus_container",
            "proven_inner_argv": list(B6A_PROVEN_NVIDIA_SMI_ARGV),
            "proven_inner_argv_sha256": canonical_argv_sha256(
                B6A_PROVEN_NVIDIA_SMI_ARGV
            ),
            "returncode": sample.returncode,
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "stdout_sanitized": sanitize_bytes(stdout),
            "stderr_sanitized": sanitize_bytes(stderr),
            "numeric_sample_count": len(parsed),
            "malformed_nonempty_line_count": malformed_lines,
            "contains_model_audio_transcript_prediction_credentials_or_phi": False,
        }
        if "libnvidia-ml.so" in combined:
            reason_code = "GPU_SAMPLER_DRIVER_LIBRARY_NOT_FOUND"
        elif sample.returncode != 0:
            reason_code = "GPU_SAMPLER_COMMAND_REFUSED"
        elif malformed_lines:
            reason_code = "GPU_SAMPLER_NON_NUMERIC_SAMPLE"
        elif len(parsed) != 120:
            reason_code = "GPU_SAMPLER_INCOMPLETE_SAMPLE_SET"
        elif any(index != 0 or used < 0 or total <= 0 or used > total for index, used, total in parsed):
            reason_code = "GPU_SAMPLER_INVALID_SAMPLE"
        elif len({total for _, _, total in parsed}) != 1:
            reason_code = "GPU_SAMPLER_TOTAL_MEMORY_CHANGED"
        else:
            reason_code = None
        diagnostic["status"] = "PASS_120_NUMERIC_SAMPLES" if reason_code is None else "REFUSED"
        diagnostic["reason_code"] = reason_code
        diagnostic_path = context.workdir / "gpu-sampler-self-test.json"
        write_exclusive(diagnostic_path, canonical_json(diagnostic))
        if reason_code is not None:
            raise OperationRefusal(
                reason_code,
                "the receipt-bound B6A sampler invocation refused; bounded typed diagnostics persisted "
                f"sha256={_sha(diagnostic_path)}",
            )
        used_samples = [used for _, used, _ in parsed]
        return {
            "status": "PASS_GPU_AND_SAMPLER_GATE",
            "gpu_node": node_name,
            "node_readiness": node_readiness,
            "instance_id": instance_id,
            "volume_id": volume,
            "volume_gib": 60,
            "volume_attachment_readiness": attachment_readiness,
            "dra": readiness,
            "sampler_binding_status": "PASS_BYTE_IDENTICAL_HISTORICAL_ARGV",
            "sampler_inner_argv_sha256": canonical_argv_sha256(
                B6A_PROVEN_NVIDIA_SMI_ARGV
            ),
            "sampler_diagnostic_sha256": _sha(diagnostic_path),
            "node_mount_command_bundle_sha256": mount_commands_sha256,
            "node_mount_command_template_sha256": mount_template_sha256,
            "node_mount_volume_parameter_sha256": mount_parameter_sha256,
            "samples": len(parsed),
            "baseline_mib": min(used_samples),
            "peak_mib": max(used_samples),
            "total_mib": parsed[0][2],
        }

    def node_local_input_stage(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        instance_id = state.get("instance_id")
        if not instance_id:
            raise OperationRefusal("GPU_INSTANCE_ID_ABSENT", "node-local staging requires the exact GPU instance")
        bundle = json.loads((context.workdir / "pilot-bundle.json").read_bytes())
        prefix = f"research/asr-base-model/pilot/{context.bindings['pilot_bundle']['sha256']}/"
        commands, base = staging_prelude(context.attempt)
        node_objects: list[dict[str, Any]] = []
        observed_s3_calls: list[dict[str, Any]] = []
        for item in bundle["objects"]:
            key = item["key"]
            if key.endswith(("runtime-rows.json", "model-bindings.json")):
                relative = key.removeprefix(prefix)
            elif "/bundles/" in key:
                relative = "parts/" + key.removeprefix(prefix + "bundles/")
            else:
                continue
            url = self.s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": key, "VersionId": item["version_id"]}, ExpiresIn=STAGING_PRESIGNED_URL_SECONDS)
            observed_s3_calls.append({"operation": "GetObject", "bucket": BUCKET, "key": key, "version_id_present": True})
            concrete_destination = f"{base}/input/{relative}"
            parent = str(Path(concrete_destination).parent)
            commands.extend([
                install_directory(parent),
                download_file(url, concrete_destination),
                verify_sha256(concrete_destination, item["sha256"]),
                verify_size(concrete_destination, item["bytes"]),
            ])
            node_objects.append({"key": key, "sha256": item["sha256"], "bytes": item["bytes"]})
        for name, assembly in bundle["assemblies"].items():
            parts = [f"{base}/input/parts/{item['key'].removeprefix(prefix + 'bundles/')}" for item in assembly["parts"]]
            destination = f"{base}/input/{assembly['destination']}"
            commands.extend([
                install_directory(str(Path(destination).parent)),
                concatenate_files(parts, destination),
                verify_sha256(destination, assembly["sha256"]),
                verify_size(destination, assembly["bytes"]),
            ])
            if assembly.get("archive"):
                commands.extend([
                    extract_archive(destination, f"{base}/input"),
                    f"/usr/bin/test \"$(/usr/bin/find {shlex.quote(base + '/input/audio')} -type f | /usr/bin/wc -l)\" = {assembly['files']}",
                    root_command("/usr/bin/sudo", "/usr/bin/rm", "-f", "--", destination),
                ])
        whisper_prefix = "b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/"
        model_bindings = json.loads((context.workdir / "asset-staging/model-bindings.json").read_bytes())
        for relative, item in sorted(model_bindings["whisper_files"].items()):
            url = self.s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": whisper_prefix + relative}, ExpiresIn=STAGING_PRESIGNED_URL_SECONDS)
            observed_s3_calls.append({"operation": "GetObject", "bucket": BUCKET, "key": whisper_prefix + relative, "version_id_present": False})
            destination = f"{base}/input/models/whisper-large-v3-ct2/{relative}"
            commands.extend([
                install_directory(str(Path(destination).parent)),
                download_file(url, destination),
                verify_sha256(destination, item["sha256"]),
            ])
        network = base64.b64encode((context.workdir / "network-binding.json").read_bytes()).decode()
        commands.extend([
            write_base64(network, f"{base}/input/network-binding.json"),
            root_command("/usr/bin/sudo", "/usr/bin/find", f"{base}/input", "-type", "d", "-exec", "/usr/bin/chmod", "0555", "{}", "+"),
            root_command("/usr/bin/sudo", "/usr/bin/find", f"{base}/input", "-type", "f", "-exec", "/usr/bin/chmod", "0444", "{}", "+"),
        ])
        inventory = self._endpoint_call_inventory(context)
        try:
            observed_call_coverage = validate_observed_s3_calls(
                inventory, observed_s3_calls
            )
        except EndpointPolicyRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        command_audit = audit_staging_commands(commands)
        command_bundle_sha256 = hashlib.sha256(canonical_json(commands)).hexdigest()
        write_exclusive(
            context.workdir / "node-local-input-command-binding.json",
            canonical_json({
                "schema_version": 1,
                "status": "BOUND_FOR_FIRST_LIVE_PROOF_NOT_HISTORICALLY_PROVEN",
                "command_count": len(commands),
                "command_bundle_sha256": command_bundle_sha256,
                "command_audit": command_audit,
                "endpoint_inventory_sha256": inventory["inventory_sha256"],
                "observed_s3_call_coverage": observed_call_coverage,
                "presigned_url_values_recorded": False,
                "contains_credentials_phi_audio_reference_or_prediction": False,
            }),
        )
        diagnostic_path = context.workdir / "node-local-input-ssm-diagnostic.json"
        result = self._ssm(
            instance_id,
            commands,
            timeout_seconds=STAGING_SSM_TIMEOUT_SECONDS,
            diagnostic_path=diagnostic_path,
            pre_model_safe_output=True,
        )
        state["staging_path"] = base
        self._save_state(context, state)
        return {"status": "PASS_NODE_LOCAL_INPUT_STAGE", "instance_id": instance_id, "bundle_hash_verified": True, "objects": len(node_objects), "ssm_command_id": result["command_id"], "ssm_diagnostic_sha256": result["diagnostic_sha256"], "command_bundle_sha256": command_bundle_sha256, "command_audit": command_audit, "endpoint_inventory_sha256": inventory["inventory_sha256"], "observed_s3_call_coverage": observed_call_coverage, "historical_live_pass_before_this_attempt": False, "credentials_in_container": False, "urls_in_container": False}

    def _image_prepull_qualification(
        self, context: AttemptContext
    ) -> dict[str, Any]:
        """Warm and prove the exact digest before any proof-control Pod runs."""

        state = self._state(context)
        node_name = state.get("node_name")
        if not isinstance(node_name, str) or not node_name:
            raise OperationRefusal(
                "IMAGE_PREPULL_NODE_ABSENT",
                "exact GPU node identity is absent before image pre-pull",
            )
        image = (
            f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPOSITORY}@"
            f"{context.bindings['image']['linux_amd64_digest']}"
        )
        pod_name = "asr-eval-image-prepull"
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": NAMESPACE,
                "labels": {
                    "app.kubernetes.io/name": "asr-eval-image-prepull",
                    "medzen.io/classification": "offline-evaluation-only",
                },
            },
            "spec": {
                "automountServiceAccountToken": False,
                "restartPolicy": "Never",
                "nodeName": node_name,
                "tolerations": [
                    {
                        "key": "nvidia.com/gpu",
                        "operator": "Equal",
                        "value": "true",
                        "effect": "NoSchedule",
                    }
                ],
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "runAsGroup": 10001,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "prepull",
                        "image": image,
                        "imagePullPolicy": "Always",
                        "command": ["/opt/venv/bin/python", "-c", "pass"],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
            },
        }
        spec_path = context.workdir / "image-prepull-pod.json"
        write_exclusive(spec_path, canonical_json(pod))
        applied = False
        primary: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            self._kubectl(context, "apply", "-f", "-", stdin=canonical_json(pod))
            applied = True
            terminal = self._wait_stage_pod_terminal(
                context,
                pod_name=pod_name,
                purpose="exact-image-prepull",
                timeout_seconds=POD_TERMINAL_TIMEOUT_SECONDS,
                poll_interval_seconds=POD_POLL_INTERVAL_SECONDS,
                stall_seconds=POD_PULL_STALL_SECONDS,
            )
            if terminal["phase"] != "Succeeded":
                raise OperationRefusal(
                    "IMAGE_PREPULL_POD_REFUSED",
                    f"exact-image pre-pull pod reached {terminal['phase']}",
                    outcome="BLOCKED_NETWORK_ISOLATION",
                )
            inventory_deadline = self._monotonic() + 180
            inventory_observations = 0
            stable_present = 0
            inventory_sequence: list[str] = []
            inventory: dict[str, Any] | None = None
            while self._monotonic() < inventory_deadline:
                node = self._kubectl(
                    context,
                    "get",
                    "node",
                    node_name,
                    timeout=30,
                    json_output=True,
                )
                try:
                    inventory = exact_image_in_node_inventory(
                        node, expected_node=node_name, expected_image=image
                    )
                except PodLifecycleRefusal as exc:
                    raise OperationRefusal(exc.reason_code, exc.detail) from exc
                inventory_observations += 1
                if inventory["exact_image_present"]:
                    stable_present += 1
                    inventory_sequence.append("PRESENT")
                    if stable_present >= 2:
                        break
                else:
                    stable_present = 0
                    inventory_sequence.append("ABSENT")
                self._sleeper(5)
            if inventory is None or stable_present < 2:
                raise OperationRefusal(
                    "IMAGE_PREPULL_NODE_INVENTORY_TIMEOUT",
                    "exact digest did not reach two stable node-inventory observations",
                    outcome="BLOCKED_NETWORK_ISOLATION",
                )
            result = {
                "schema_version": 1,
                "status": "PASS_EXACT_IMAGE_PREPULL_QUALIFICATION",
                "image": image,
                "image_sha256": hashlib.sha256(image.encode()).hexdigest(),
                "linux_amd64_digest": context.bindings["image"]["linux_amd64_digest"],
                "node_name": node_name,
                "pod_spec_sha256": _sha(spec_path),
                "terminal_observation": terminal,
                "measured_pull_duration_seconds": terminal["elapsed_seconds"],
                "historical_pull_duration_seconds": 197.027,
                "historical_pull_evidence": "ASR-BASE-MODEL-2026-002S-A20-LIVE",
                "hard_timeout_seconds": POD_TERMINAL_TIMEOUT_SECONDS,
                "stall_timeout_seconds": POD_PULL_STALL_SECONDS,
                "progress_stall_detector_enabled": True,
                "inventory": inventory,
                "inventory_observations": inventory_observations,
                "inventory_stable_present_observations": stable_present,
                "inventory_observation_sequence": inventory_sequence,
            }
            write_exclusive(
                context.workdir / "image-prepull-qualification.json",
                canonical_json(result),
            )
        except Exception as exc:
            primary = exc
        cleanup = (
            self._delete_stage_pod(
                context, pod_name=pod_name, purpose="exact-image-prepull"
            )
            if applied
            else {"status": "NOT_APPLICABLE_NOT_APPLIED"}
        )
        if primary is not None:
            if cleanup["status"] == "SECONDARY_STAGE_POD_CLEANUP_REFUSED":
                self._retain_secondary_pod_cleanup(
                    context,
                    pod_name=pod_name,
                    purpose="exact-image-prepull",
                    primary=primary,
                    cleanup=cleanup,
                )
            raise primary
        if cleanup["status"] != "PASS_NONBLOCKING_DELETE_AND_STABLE_ABSENCE":
            raise OperationRefusal(
                "IMAGE_PREPULL_FINALIZER_REFUSED",
                "image pre-pull passed but its stage-local Pod cleanup refused",
            )
        assert result is not None
        result["pod_cleanup"] = cleanup
        return result

    def _cross_pod_refusal(self, context: AttemptContext, pod_ip: str) -> dict[str, Any]:
        image = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPOSITORY}@{context.bindings['image']['linux_amd64_digest']}"
        probe = {
            "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "asr-eval-inbound-control", "namespace": NAMESPACE, "labels": {"app.kubernetes.io/name": "asr-eval-inbound-control"}},
            "spec": {"automountServiceAccountToken": False, **pod_dns_fields(), "restartPolicy": "Never", "nodeSelector": {"workload": "gpu"}, "tolerations": [{"key": "nvidia.com/gpu", "operator": "Equal", "value": "true", "effect": "NoSchedule"}], "containers": [{"name": "control", "image": image, "command": ["/opt/venv/bin/python", "-c", "import socket,sys; s=socket.socket(); s.settimeout(3); rc=s.connect_ex((sys.argv[1],8080)); sys.exit(0 if rc else 9)", pod_ip], "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]},
        }
        try:
            validate_pod_dns_fields(probe["spec"])
        except DnsAlignmentRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        encoded = canonical_json(probe)
        pod_name = "asr-eval-inbound-control"
        applied = False
        primary: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            self._kubectl(context, "apply", "-f", "-", stdin=encoded)
            applied = True
            terminal = self._wait_stage_pod_terminal(
                context,
                pod_name=pod_name,
                purpose="cross-pod-inbound-control",
                timeout_seconds=90,
                poll_interval_seconds=5,
            )
            logs = self._kubectl(
                context, "logs", "-n", NAMESPACE, f"pod/{pod_name}"
            ) or b""
            body = logs if isinstance(logs, bytes) else canonical_json(logs)
            if terminal["phase"] != "Succeeded":
                raise OperationRefusal(
                    "NETWORK_INBOUND_CONTROL_ACCEPTED",
                    "cross-pod TCP connection unexpectedly succeeded",
                )
            result = {
                "status": "REFUSED_AS_REQUIRED",
                "target_port": 8080,
                "logs_sha256": hashlib.sha256(body).hexdigest(),
                "terminal_observation": terminal,
            }
        except Exception as exc:
            primary = exc
        cleanup = (
            self._delete_stage_pod(
                context, pod_name=pod_name, purpose="cross-pod-inbound-control"
            )
            if applied
            else {"status": "NOT_APPLICABLE_NOT_APPLIED"}
        )
        if primary is not None:
            if cleanup["status"] == "SECONDARY_STAGE_POD_CLEANUP_REFUSED":
                self._retain_secondary_pod_cleanup(
                    context,
                    pod_name=pod_name,
                    purpose="cross-pod-inbound-control",
                    primary=primary,
                    cleanup=cleanup,
                )
            raise primary
        if cleanup["status"] != "PASS_NONBLOCKING_DELETE_AND_STABLE_ABSENCE":
            raise OperationRefusal(
                "INBOUND_CONTROL_FINALIZER_REFUSED",
                "inbound control passed but its stage-local Pod cleanup refused",
            )
        assert result is not None
        result["pod_cleanup"] = cleanup
        return result

    def _dns_resolution_consistency_gate(
        self, context: AttemptContext
    ) -> dict[str, Any]:
        """Resolve every allowed host from a policy-selected pod before launch."""

        workload = (context.workdir / "workload.yaml").read_text(encoding="utf-8")
        binding = json.loads((context.workdir / "network-binding.json").read_bytes())
        hosts = binding.get("allowed_tcp_443_hosts")
        if not isinstance(hosts, list) or not hosts or any(
            not isinstance(value, str) or not value for value in hosts
        ):
            raise OperationRefusal(
                "DNS_CONTROL_HOST_SET_DIFFERS",
                "network binding has no exact allowed-host set",
                outcome="BLOCKED_NETWORK_ISOLATION",
            )
        try:
            allowlist = workload_egress_allowlist(workload)
        except DnsAlignmentRefusal as exc:
            raise OperationRefusal(
                exc.reason_code, exc.detail, outcome="BLOCKED_NETWORK_ISOLATION"
            ) from exc
        image = (
            f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPOSITORY}@"
            f"{context.bindings['image']['linux_amd64_digest']}"
        )
        script = (
            "import json,pathlib,socket,sys,time\n"
            "hosts=json.loads(sys.argv[1]); expected=sys.argv[2]\n"
            "nameservers=[line.split()[1] for line in pathlib.Path('/etc/resolv.conf').read_text().splitlines() if line.startswith('nameserver ')]\n"
            "def emit(value,code): print(json.dumps(value,sort_keys=True,separators=(',',':')),flush=True); raise SystemExit(code)\n"
            "if nameservers != [expected]: emit({'schema_version':1,'status':'REFUSED','reason_code':'DNS_EFFECTIVE_RESOLVER_DIFFERS','effective_nameservers':nameservers,'torch_imported':False},70)\n"
            "last_errors={}\n"
            "for attempt in range(1,13):\n"
            " resolved={}; errors={}\n"
            " for host in hosts:\n"
            "  try: resolved[host]=sorted({row[4][0] for row in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})\n"
            "  except OSError as exc: errors[host]={'exception_class':type(exc).__name__,'errno':getattr(exc,'errno',None)}\n"
            " if len(resolved)==len(hosts) and all(resolved.values()): emit({'schema_version':1,'status':'PASS_VPC_RESOLVER_CONSISTENCY','effective_nameservers':nameservers,'resolved_ips':resolved,'attempts':attempt,'torch_imported':False},0)\n"
            " last_errors=errors; time.sleep(5)\n"
            "emit({'schema_version':1,'status':'REFUSED','reason_code':'DNS_RESOLVER_UNREACHABLE','effective_nameservers':nameservers,'resolution_errors':last_errors,'attempts':12,'torch_imported':False},71)\n"
        )
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": "asr-eval-dns-control",
                "namespace": NAMESPACE,
                "labels": {
                    "app.kubernetes.io/name": "asr-base-model-pilot",
                    "medzen.io/classification": "offline-evaluation-only",
                },
            },
            "spec": {
                "automountServiceAccountToken": False,
                "hostNetwork": False,
                **pod_dns_fields(),
                "restartPolicy": "Never",
                "nodeSelector": {"workload": "gpu"},
                "tolerations": [
                    {
                        "key": "nvidia.com/gpu",
                        "operator": "Equal",
                        "value": "true",
                        "effect": "NoSchedule",
                    }
                ],
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "runAsGroup": 10001,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "dns-control",
                        "image": image,
                        "imagePullPolicy": "IfNotPresent",
                        "env": [
                            {"name": "HOME", "value": "/tmp"},
                            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                        ],
                        "command": [
                            "/opt/venv/bin/python",
                            "-c",
                            script,
                            json.dumps(sorted(set(hosts)), separators=(",", ":")),
                            VPC_DNS_RESOLVER,
                        ],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                    }
                ],
                "volumes": [
                    {
                        "name": "tmp",
                        "emptyDir": {"sizeLimit": "64Mi"},
                    }
                ],
            },
        }
        try:
            validate_pod_dns_fields(pod["spec"])
        except DnsAlignmentRefusal as exc:
            raise OperationRefusal(
                exc.reason_code, exc.detail, outcome="BLOCKED_NETWORK_ISOLATION"
            ) from exc
        write_exclusive(
            context.workdir / "dns-control-pod.json", canonical_json(pod)
        )
        applied = False
        primary: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            self._kubectl(context, "apply", "-f", "-", stdin=canonical_json(pod))
            applied = True
            terminal = self._wait_stage_pod_terminal(
                context,
                pod_name="asr-eval-dns-control",
                purpose="dns-resolution-consistency",
                timeout_seconds=300,
                poll_interval_seconds=5,
            )
            raw = self._kubectl(
                context, "logs", "-n", NAMESPACE, "pod/asr-eval-dns-control"
            )
            body = raw if isinstance(raw, bytes) else canonical_json(raw)
            try:
                receipt = json.loads(body)
            except Exception as exc:
                raise OperationRefusal(
                    "DNS_CONTROL_RECEIPT_MALFORMED",
                    "DNS control pod did not emit one valid JSON receipt",
                    outcome="BLOCKED_NETWORK_ISOLATION",
                ) from exc
            try:
                result = validate_dns_resolution_receipt(
                    receipt,
                    expected_hosts=hosts,
                    allowed_tcp_443_cidrs=allowlist["tcp_443_cidrs"],
                )
            except DnsAlignmentRefusal as exc:
                raise OperationRefusal(
                    exc.reason_code,
                    exc.detail,
                    outcome="BLOCKED_NETWORK_ISOLATION",
                ) from exc
            if terminal["phase"] != "Succeeded":
                raise OperationRefusal(
                    "DNS_CONTROL_POD_REFUSED",
                    "DNS control pod did not reach Succeeded despite a valid receipt",
                    outcome="BLOCKED_NETWORK_ISOLATION",
                )
            result["control_pod_spec_sha256"] = _sha(
                context.workdir / "dns-control-pod.json"
            )
            result["terminal_observation"] = terminal
            write_exclusive(
                context.workdir / "dns-consistency-gate.json",
                canonical_json(result),
            )
        except Exception as exc:
            primary = exc
        cleanup = (
            self._delete_stage_pod(
                context,
                pod_name="asr-eval-dns-control",
                purpose="dns-resolution-consistency",
            )
            if applied
            else {"status": "NOT_APPLICABLE_NOT_APPLIED"}
        )
        if primary is not None:
            if cleanup["status"] == "SECONDARY_STAGE_POD_CLEANUP_REFUSED":
                self._retain_secondary_pod_cleanup(
                    context,
                    pod_name="asr-eval-dns-control",
                    purpose="dns-resolution-consistency",
                    primary=primary,
                    cleanup=cleanup,
                )
            raise primary
        if cleanup["status"] != "PASS_NONBLOCKING_DELETE_AND_STABLE_ABSENCE":
            raise OperationRefusal(
                "DNS_CONTROL_FINALIZER_REFUSED",
                "DNS control passed but its stage-local Pod cleanup refused",
                outcome="BLOCKED_NETWORK_ISOLATION",
            )
        assert result is not None
        result["pod_cleanup"] = cleanup
        return result

    @staticmethod
    def _append_runtime_telemetry(path: Path, sample: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = canonical_json(sample)
        mode = "ab" if path.exists() else "xb"
        with path.open(mode) as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())

    def _sample_runtime_resources(
        self,
        context: AttemptContext,
        *,
        observation: int,
        elapsed_seconds: float,
    ) -> dict[str, Any]:
        """Collect bounded node RAM and global GPU VRAM via the proven DRA path."""
        state = self._state(context)
        dra_pod = state.get("dra_pod")
        node_name = state.get("node_name")
        instance_id = state.get("instance_id")
        sample_path = context.workdir / "runtime-resource-telemetry.jsonl"
        base = {
            "schema_version": 1,
            "observation": observation,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "node_name": node_name,
            "instance_id": instance_id,
            "capture_transport": "KUBECTL_EXEC_DRA_NODE_POD",
            "proven_nvidia_smi_argv_sha256": canonical_argv_sha256(
                B6A_PROVEN_NVIDIA_SMI_ARGV
            ),
            "contains_model_audio_transcript_prediction_credentials_or_phi": False,
        }
        if not all(isinstance(value, str) and value for value in (dra_pod, node_name, instance_id)):
            sample = {**base, "status": "UNAVAILABLE_NODE_IDENTITY"}
            self._append_runtime_telemetry(sample_path, sample)
            return sample
        sample_program = (
            f"/busybox/printf '{RUNTIME_TELEMETRY_MARKER}\\n'; "
            "/busybox/awk '/^MemTotal:/{total=$2} /^MemAvailable:/{available=$2} "
            "END{printf \"ram_kib=%s,%s\\n\",total,available}' /proc/meminfo; "
            + shlex.join(B6A_PROVEN_NVIDIA_SMI_ARGV)
        )
        command = [
            "kubectl",
            "--kubeconfig",
            str(context.workdir / "kubeconfig"),
            "exec",
            "-n",
            "nvidia-dra-driver",
            dra_pod,
            "-c",
            "gpus",
            "--",
            "/busybox/sh",
            "-ec",
            sample_program,
        ]
        try:
            completed = self._command(command, timeout=30, check=False)
            stdout = completed.stdout or b""
            stderr = completed.stderr or b""
            lines = [line.strip() for line in stdout.decode(errors="replace").splitlines() if line.strip()]
            ram = re.fullmatch(r"ram_kib=([0-9]+),([0-9]+)", lines[1]) if len(lines) > 1 else None
            gpu = [field.strip() for field in lines[2].split(",")] if len(lines) > 2 else []
            valid_gpu = len(gpu) == 3 and all(re.fullmatch(r"[0-9]+", field) for field in gpu)
            if (
                completed.returncode != 0
                or len(lines) != 3
                or lines[0] != RUNTIME_TELEMETRY_MARKER
                or ram is None
                or not valid_gpu
            ):
                sample = {
                    **base,
                    "status": "UNAVAILABLE_SAMPLE_REFUSED_OR_MALFORMED",
                    "returncode": completed.returncode,
                    "stdout": sanitize_head_tail(stdout),
                    "stderr": sanitize_head_tail(stderr),
                }
            else:
                memory_total_kib, memory_available_kib = map(int, ram.groups())
                gpu_index, vram_used_mib, vram_total_mib = map(int, gpu)
                if (
                    memory_total_kib <= 0
                    or memory_available_kib < 0
                    or memory_available_kib > memory_total_kib
                    or gpu_index != 0
                    or vram_used_mib < 0
                    or vram_total_mib <= 0
                    or vram_used_mib > vram_total_mib
                ):
                    sample = {**base, "status": "UNAVAILABLE_SAMPLE_OUT_OF_RANGE"}
                else:
                    sample = {
                        **base,
                        "status": "PASS_NODE_RUNTIME_RESOURCE_SAMPLE",
                        "memory_total_kib": memory_total_kib,
                        "memory_available_kib": memory_available_kib,
                        "gpu_index": gpu_index,
                        "vram_used_mib": vram_used_mib,
                        "vram_total_mib": vram_total_mib,
                    }
        except Exception as exc:
            sample = {
                **base,
                "status": "UNAVAILABLE_SAMPLE_EXCEPTION",
                "exception_class": type(exc).__name__,
                "safe_error_text": sanitize_bytes(str(exc)),
            }
        self._append_runtime_telemetry(sample_path, sample)
        return sample

    @staticmethod
    def _runtime_telemetry_summary(context: AttemptContext) -> dict[str, Any]:
        path = context.workdir / "runtime-resource-telemetry.jsonl"
        if not path.is_file():
            return {"status": "ABSENT", "sample_count": 0, "pass_sample_count": 0}
        values: list[dict[str, Any]] = []
        malformed = 0
        for line in path.read_bytes().splitlines():
            try:
                value = json.loads(line)
            except Exception:
                malformed += 1
                continue
            if isinstance(value, dict):
                values.append(value)
            else:
                malformed += 1
        passed = [
            value
            for value in values
            if value.get("status") == "PASS_NODE_RUNTIME_RESOURCE_SAMPLE"
        ]
        return {
            "status": "CAPTURED" if values else "MALFORMED_OR_EMPTY",
            "path": path.name,
            "sha256": _sha(path),
            "sample_count": len(values),
            "pass_sample_count": len(passed),
            "unavailable_sample_count": len(values) - len(passed),
            "malformed_line_count": malformed,
            "minimum_memory_available_kib": (
                min(value["memory_available_kib"] for value in passed)
                if passed
                else None
            ),
            "peak_vram_used_mib": (
                max(value["vram_used_mib"] for value in passed) if passed else None
            ),
            "vram_total_mib": (
                passed[-1]["vram_total_mib"] if passed else None
            ),
            "persisted_before_cleanup": True,
        }

    def _capture_phase_journal(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        instance_id = state.get("instance_id")
        staging_path = state.get("staging_path")
        if not isinstance(instance_id, str) or not isinstance(staging_path, str):
            return {"status": "NOT_APPLICABLE_STAGING_UNAVAILABLE"}
        path = f"{staging_path}/output/pilot-phase-journal.jsonl"
        command = (
            f"if /usr/bin/test -s {shlex.quote(path)}; then "
            f"/usr/bin/printf '%s\\n' {PHASE_JOURNAL_PRESENT}; "
            f"bytes=$(/usr/bin/wc -c < {shlex.quote(path)}); "
            f"digest=$(/usr/bin/sha256sum {shlex.quote(path)}); digest=${{digest%% *}}; "
            "/usr/bin/printf '%s %s\\n' \"$bytes\" \"$digest\"; "
            f"/usr/bin/head -c 4096 {shlex.quote(path)}; "
            f"/usr/bin/printf '\\n%s\\n' {PHASE_JOURNAL_TAIL}; "
            f"/usr/bin/tail -c 4096 {shlex.quote(path)}; "
            "else "
            f"/usr/bin/printf '%s\\n' {PHASE_JOURNAL_ABSENT}; fi"
        )
        try:
            result = self._ssm(
                instance_id,
                [command],
                timeout_seconds=60,
                pre_model_safe_output=True,
            )
            marker, separator, rest = result["stdout"].partition("\n")
            if not separator:
                return {"status": "MALFORMED_OBSERVATION"}
            if marker == PHASE_JOURNAL_ABSENT:
                return {"status": "ABSENT"}
            metadata, separator, payload = rest.partition("\n")
            if marker != PHASE_JOURNAL_PRESENT or not separator:
                return {"status": "MALFORMED_OBSERVATION"}
            fields = metadata.split()
            head, tail_marker, tail = payload.partition(f"\n{PHASE_JOURNAL_TAIL}\n")
            if (
                len(fields) != 2
                or re.fullmatch(r"[0-9]+", fields[0]) is None
                or re.fullmatch(r"[0-9a-f]{64}", fields[1]) is None
                or not tail_marker
            ):
                return {"status": "MALFORMED_OBSERVATION"}
            last_event: dict[str, Any] | None = None
            for line in reversed(tail.splitlines()):
                try:
                    candidate = json.loads(line)
                except Exception:
                    continue
                if isinstance(candidate, dict):
                    last_event = {
                        key: candidate.get(key)
                        for key in (
                            "schema_version",
                            "sequence",
                            "phase",
                            "completed_rows",
                            "current_model",
                            "exception_class",
                            "safe_error_text",
                            "reason_code",
                        )
                        if key in candidate
                    }
                    if "safe_error_text" in last_event:
                        last_event["safe_error_text"] = sanitize_bytes(
                            last_event["safe_error_text"]
                        )
                    break
            return {
                "status": "CAPTURED",
                "bytes": int(fields[0]),
                "sha256": fields[1],
                "head_sanitized": sanitize_bytes(head),
                "tail_sanitized": sanitize_bytes(tail),
                "head_window_bytes": 4096,
                "tail_window_bytes": 4096,
                "window_policy": "SANITIZED_HEAD_AND_TAIL",
                "last_event": last_event,
            }
        except Exception as exc:
            return {
                "status": "UNAVAILABLE",
                "exception_class": type(exc).__name__,
                "safe_error_text": sanitize_bytes(str(exc)),
            }

    def _capture_pilot_workload_refusal_diagnostics(
        self,
        context: AttemptContext,
        *,
        pod_name: str | None,
        failure: Exception,
    ) -> dict[str, Any]:
        """Persist bounded public-eval diagnostics before cleanup.

        The pilot accepts only the frozen public research set and no PHI.  The
        response text is still sanitized and truncated; credentials and
        presigned URLs are never retained.
        """

        def capture_json(
            *args: str,
            normalize_termination: bool = False,
        ) -> dict[str, Any]:
            try:
                value = self._kubectl(context, *args, timeout=30, json_output=True)
                raw = canonical_json(value if isinstance(value, dict) else {})
                windows = sanitize_head_tail(raw)
                captured = {
                    "status": "CAPTURED",
                    **windows,
                    "sanitized": windows["head_sanitized"],
                }
                if normalize_termination:
                    captured["container_termination"] = normalize_container_termination(
                        value
                    )
                return captured
            except Exception as exc:
                return {
                    "status": "UNAVAILABLE",
                    "exception_class": type(exc).__name__,
                    "safe_error": sanitize_bytes(str(exc)),
                }

        def capture_logs() -> dict[str, Any]:
            if not pod_name:
                return {"status": "NOT_APPLICABLE_NO_POD"}
            try:
                raw = self._kubectl(
                    context,
                    "logs",
                    "-n",
                    NAMESPACE,
                    pod_name,
                    "--limit-bytes=65536",
                    timeout=30,
                )
                body = raw if isinstance(raw, bytes) else canonical_json(raw)
                windows = sanitize_head_tail(body)
                return {
                    "status": "CAPTURED",
                    **windows,
                    "sanitized": windows["head_sanitized"],
                }
            except Exception as exc:
                return {
                    "status": "UNAVAILABLE",
                    "exception_class": type(exc).__name__,
                    "safe_error": sanitize_bytes(str(exc)),
                }

        def capture_network_receipt() -> dict[str, Any]:
            state = self._state(context)
            instance_id = state.get("instance_id")
            staging_path = state.get("staging_path")
            if not isinstance(instance_id, str) or not isinstance(staging_path, str):
                return {"status": "NOT_APPLICABLE_STAGING_UNAVAILABLE"}
            path = f"{staging_path}/output/network-probe.json"
            command = (
                f"if /usr/bin/test -s {shlex.quote(path)}; then "
                f"/usr/bin/printf '%s\\n' MEDZEN_NETWORK_RECEIPT_PRESENT; "
                f"/usr/bin/cat {shlex.quote(path)}; else "
                "/usr/bin/printf '%s\\n' MEDZEN_NETWORK_RECEIPT_ABSENT; fi"
            )
            try:
                result = self._ssm(
                    instance_id,
                    [command],
                    timeout_seconds=60,
                    pre_model_safe_output=True,
                )
                body = result["stdout"]
                marker, _, payload = body.partition("\n")
                if marker == "MEDZEN_NETWORK_RECEIPT_ABSENT":
                    return {"status": "ABSENT"}
                if marker != "MEDZEN_NETWORK_RECEIPT_PRESENT" or not payload:
                    return {
                        "status": "MALFORMED_OBSERVATION",
                        "stdout_sha256": result["stdout_sha256"],
                    }
                parsed = json.loads(payload)
                raw = canonical_json(parsed)
                windows = sanitize_head_tail(raw)
                return {
                    "status": "CAPTURED",
                    "sha256": windows["sha256"],
                    "receipt_status": parsed.get("status"),
                    "reason_code": parsed.get("reason_code"),
                    "head_sanitized": windows["head_sanitized"],
                    "tail_sanitized": windows["tail_sanitized"],
                    "window_policy": windows["window_policy"],
                    "sanitized": windows["head_sanitized"],
                }
            except Exception as exc:
                return {
                    "status": "UNAVAILABLE",
                    "exception_class": type(exc).__name__,
                    "safe_error": sanitize_bytes(str(exc)),
                }

        def capture_network_policy_agent() -> dict[str, Any]:
            state = self._state(context)
            node_name = state.get("node_name")
            if not isinstance(node_name, str) or not node_name:
                return {"status": "NOT_APPLICABLE_NODE_UNAVAILABLE"}
            try:
                pods = self._kubectl(
                    context,
                    "get",
                    "pods",
                    "-n",
                    "kube-system",
                    "-l",
                    "k8s-app=aws-node",
                    timeout=30,
                    json_output=True,
                )
                matches = [
                    item
                    for item in pods.get("items", [])
                    if item.get("spec", {}).get("nodeName") == node_name
                ]
                if len(matches) != 1:
                    return {
                        "status": "UNAVAILABLE_AGENT_POD_AMBIGUOUS",
                        "matching_pods": len(matches),
                    }
                name = matches[0].get("metadata", {}).get("name")
                if not isinstance(name, str) or not name:
                    return {"status": "UNAVAILABLE_AGENT_POD_NAME_ABSENT"}
                container_names = [
                    item.get("name")
                    for item in matches[0].get("spec", {}).get("containers", [])
                    if isinstance(item.get("name"), str)
                ]
                container = next(
                    (
                        candidate
                        for candidate in (
                            "aws-eks-nodeagent",
                            "aws-network-policy-agent",
                            "aws-node",
                        )
                        if candidate in container_names
                    ),
                    None,
                )
                if container is None:
                    return {
                        "status": "UNAVAILABLE_AGENT_CONTAINER_ABSENT",
                        "container_names": sorted(container_names),
                    }
                raw = self._kubectl(
                    context,
                    "logs",
                    "-n",
                    "kube-system",
                    name,
                    "-c",
                    container,
                    "--since=10m",
                    "--tail=300",
                    timeout=30,
                )
                body = raw if isinstance(raw, bytes) else canonical_json(raw)
                windows = sanitize_head_tail(body)
                return {
                    "status": "CAPTURED",
                    "pod": name,
                    "container": container,
                    **windows,
                    "sanitized": windows["head_sanitized"],
                }
            except Exception as exc:
                return {
                    "status": "UNAVAILABLE",
                    "exception_class": type(exc).__name__,
                    "safe_error": sanitize_bytes(str(exc)),
                }

        diagnostic = {
            "schema_version": 3,
            "status": "CAPTURED_BEFORE_CLEANUP",
            "classification": "FROZEN_PUBLIC_RESEARCH_EVAL_NO_PHI_BOUNDED_DIAGNOSTICS",
            "failure_exception_class": type(failure).__name__,
            "failure_reason_code": getattr(failure, "reason_code", None),
            "failure_safe_text": sanitize_bytes(str(failure)),
            "pod_name": pod_name,
            "job": capture_json(
                "get",
                "job",
                f"asr-base-model-pilot-a{context.attempt}",
                "-n",
                NAMESPACE,
            ),
            "pod": (
                capture_json(
                    "get",
                    "pod",
                    pod_name,
                    "-n",
                    NAMESPACE,
                    normalize_termination=True,
                )
                if pod_name
                else {"status": "NOT_APPLICABLE_NO_POD"}
            ),
            "events": capture_json(
                "get", "events", "-n", NAMESPACE, "--sort-by=.lastTimestamp"
            ),
            "logs": capture_logs(),
            "phase_journal": self._capture_phase_journal(context),
            "runtime_resource_telemetry": self._runtime_telemetry_summary(context),
            "network_probe_receipt": capture_network_receipt(),
            "network_policy_agent": capture_network_policy_agent(),
            "diagnostic_window_policy": "SANITIZED_HEAD_AND_TAIL_4096_BYTES_EACH",
            "credentials_presigned_urls_or_environment_values_recorded": False,
        }
        diagnostic["container_termination"] = diagnostic["pod"].get(
            "container_termination",
            normalize_container_termination({}),
        )
        path = context.workdir / "pilot-workload-refusal-diagnostics.json"
        write_exclusive(path, canonical_json(diagnostic))
        return {"path": path, "sha256": _sha(path), **diagnostic}

    def _wait_pilot_network_receipts(
        self,
        context: AttemptContext,
        *,
        pod_name: str,
        instance_id: str,
        staging_path: str,
        timeout_seconds: int = PILOT_RECEIPT_TIMEOUT_SECONDS,
        poll_interval_seconds: int = PILOT_RECEIPT_POLL_INTERVAL_SECONDS,
        required_observations: int = PILOT_RECEIPT_STABLE_OBSERVATIONS,
    ) -> dict[str, Any]:
        validate_boundary_parameters(
            "pilot_receipt_readiness",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            required_observations=required_observations,
        )
        try:
            observation_command = network_receipt_observation_command(staging_path)
        except AsyncObservationRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        deadline = self._monotonic() + timeout_seconds
        observations = 0
        stable = 0
        stable_receipt_sha256: str | None = None
        network_absent_observations = 0
        listener_absent_observations = 0
        last_phase = "Unknown"
        while self._monotonic() < deadline:
            pod = self._kubectl(
                context,
                "get",
                "pod",
                pod_name,
                "-n",
                NAMESPACE,
                timeout=30,
                json_output=True,
            )
            try:
                pod_observation = pilot_pod_terminal_observation(pod)
            except AsyncObservationRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            last_phase = pod_observation["phase"]
            if pod_observation["terminal"]:
                raise OperationRefusal(
                    "PILOT_POD_TERMINAL_BEFORE_NETWORK_RECEIPT",
                    "pilot pod became terminal before stable network/listener receipts; "
                    f"phase={last_phase} terminated={len(pod_observation['terminated'])}",
                    outcome="BLOCKED_NETWORK_ISOLATION",
                )
            result = self._ssm(
                instance_id,
                [observation_command],
                timeout_seconds=60,
                pre_model_safe_output=True,
            )
            observations += 1
            try:
                observed = parse_network_receipt_observation(result["stdout"])
            except AsyncObservationRefusal as exc:
                raise OperationRefusal(
                    exc.reason_code,
                    exc.detail,
                    outcome="BLOCKED_NETWORK_ISOLATION",
                ) from exc
            if observed["status"] == "WAIT_NETWORK_RECEIPT":
                if stable_receipt_sha256 is not None:
                    raise OperationRefusal(
                        "NETWORK_PROBE_RECEIPT_REGRESSED",
                        "network receipt disappeared after a present observation",
                        outcome="BLOCKED_NETWORK_ISOLATION",
                    )
                network_absent_observations += 1
                stable = 0
            else:
                receipt_sha256 = hashlib.sha256(
                    canonical_json(observed["network_receipt"])
                ).hexdigest()
                if stable_receipt_sha256 is None:
                    stable_receipt_sha256 = receipt_sha256
                elif receipt_sha256 != stable_receipt_sha256:
                    raise OperationRefusal(
                        "NETWORK_PROBE_RECEIPT_DRIFT",
                        "network receipt changed between stable observations",
                        outcome="BLOCKED_NETWORK_ISOLATION",
                    )
                if not observed["listener_ready"]:
                    listener_absent_observations += 1
                    stable = 0
                else:
                    stable += 1
                    if stable >= required_observations:
                        return {
                            "status": "PASS_STABLE_NETWORK_AND_LISTENER_RECEIPTS",
                            "network_receipt": observed["network_receipt"],
                            "network_receipt_sha256": receipt_sha256,
                            "observations": observations,
                            "stable_observations": stable,
                            "required_stable_observations": required_observations,
                            "network_absent_observations": network_absent_observations,
                            "listener_absent_observations": listener_absent_observations,
                            "poll_interval_seconds": poll_interval_seconds,
                            "timeout_seconds": timeout_seconds,
                            "pod_phase": last_phase,
                        }
            remaining = deadline - self._monotonic()
            if remaining <= poll_interval_seconds:
                break
            self._sleeper(poll_interval_seconds)
        raise OperationRefusal(
            "NETWORK_PROBE_RECEIPT_TIMEOUT",
            "pilot pod remained non-terminal but stable network/listener receipts "
            f"were not observed within {timeout_seconds} seconds after "
            f"{observations} reads; phase={last_phase} "
            f"network_absent={network_absent_observations} "
            f"listener_absent={listener_absent_observations}",
            outcome="BLOCKED_NETWORK_ISOLATION",
        )

    def _wait_pilot_job_complete(
        self,
        context: AttemptContext,
        *,
        timeout_seconds: int = 9000,
        poll_interval_seconds: int = 10,
    ) -> dict[str, Any]:
        """Poll the real Job object so rehearsal must exercise active state."""

        validate_boundary_parameters(
            "pilot_job_completion",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        started = self._monotonic()
        deadline = started + timeout_seconds
        observations = 0
        state_sequence: list[str] = []
        job_name = f"asr-base-model-pilot-a{context.attempt}"
        # Attempts 36 and 37 were both refused by ONE transient host-network
        # failure of this read-only poll while the cluster Job ran on
        # unharmed. A bounded tolerance absorbs isolated blips: up to three
        # CONSECUTIVE poll failures are recorded and retried on the poll
        # cadence; a fourth consecutive failure — or the deadline — still
        # fails closed. The Job's own activeDeadlineSeconds and the
        # scheduled teardown action remain the cost backstops throughout.
        consecutive_poll_failures = 0
        while self._monotonic() < deadline:
            try:
                job = self._kubectl(
                    context,
                    "get",
                    "job",
                    job_name,
                    "-n",
                    NAMESPACE,
                    timeout=30,
                    json_output=True,
                )
            except OperationRefusal as exc:
                if exc.reason_code != "BOUNDED_COMMAND_REFUSED":
                    raise
                consecutive_poll_failures += 1
                state_sequence.append(
                    f"POLL_TRANSIENT_FAILURE_{consecutive_poll_failures}"
                )
                if consecutive_poll_failures > 3:
                    raise
                self._sleeper(poll_interval_seconds)
                continue
            consecutive_poll_failures = 0
            status = job.get("status") if isinstance(job, dict) else None
            if not isinstance(status, dict):
                raise OperationRefusal(
                    "PILOT_JOB_RESPONSE_MALFORMED",
                    "pilot Job response has no status object",
                )
            observations += 1
            conditions = status.get("conditions", [])
            complete = any(
                isinstance(item, dict)
                and item.get("type") == "Complete"
                and item.get("status") == "True"
                for item in conditions
            )
            failed = any(
                isinstance(item, dict)
                and item.get("type") == "Failed"
                and item.get("status") == "True"
                for item in conditions
            ) or bool(status.get("failed"))
            observed_state = "COMPLETE" if complete else "FAILED" if failed else "ACTIVE"
            state_sequence.append(observed_state)
            self._sample_runtime_resources(
                context,
                observation=observations,
                elapsed_seconds=self._monotonic() - started,
            )
            if complete:
                telemetry = self._runtime_telemetry_summary(context)
                if telemetry["pass_sample_count"] < 2:
                    raise OperationRefusal(
                        "RUNTIME_RESOURCE_TELEMETRY_INSUFFICIENT",
                        "pilot Job completed without two bounded node RAM/VRAM samples",
                    )
                return {
                    "status": "PASS_PILOT_JOB_COMPLETE",
                    "observations": observations,
                    "state_sequence": state_sequence,
                    "sleep_branch_executed": observations > 1,
                    "timeout_seconds": timeout_seconds,
                    "poll_interval_seconds": poll_interval_seconds,
                    "runtime_resource_telemetry": telemetry,
                }
            if failed:
                raise OperationRefusal(
                    "PILOT_JOB_REFUSED",
                    "offline pilot Job reached a failed terminal state",
                )
            self._sleeper(poll_interval_seconds)
        raise OperationRefusal(
            "PILOT_JOB_TIMEOUT",
            f"offline pilot Job did not complete inside {timeout_seconds} seconds",
        )

    def pilot_rows(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        workload = (context.workdir / "workload.yaml").read_bytes()
        documents = list(__import__("yaml").safe_load_all(workload))
        pilot_container = documents[-1]["spec"]["template"]["spec"]["containers"][0]
        pilot_workload_argv = [
            *pilot_container["command"],
            *pilot_container["args"],
        ]
        pilot_workload_argv_sha256 = hashlib.sha256(
            b"\0".join(value.encode() for value in pilot_workload_argv)
        ).hexdigest()
        write_exclusive(
            context.workdir / "pilot-workload-command-binding.json",
            canonical_json({
                "schema_version": 1,
                "status": "BOUND_BEFORE_FIRST_LIVE_WORKLOAD_NOT_HISTORICALLY_PROVEN",
                "container_argv_sha256": pilot_workload_argv_sha256,
                "workload_sha256": hashlib.sha256(workload).hexdigest(),
                "contains_credentials_phi_audio_reference_or_prediction": False,
            }),
        )
        pod_name = None
        try:
            infrastructure = __import__("yaml").safe_dump_all(documents[:-1], sort_keys=False).encode()
            job = __import__("yaml").safe_dump(documents[-1], sort_keys=False).encode()
            self._kubectl(context, "apply", "-f", "-", stdin=infrastructure)
            state["namespace"] = True
            self._save_state(context, state)
            image_prepull = self._image_prepull_qualification(context)
            dns_consistency = self._dns_resolution_consistency_gate(context)
            self._kubectl(context, "apply", "-f", "-", stdin=job)
            stop = time.monotonic() + 900
            while time.monotonic() < stop:
                pods = self._kubectl(context, "get", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/name=asr-base-model-pilot", json_output=True)
                if len(pods.get("items", [])) == 1:
                    pod = pods["items"][0]
                    pod_name = pod["metadata"]["name"]
                    pod_ip = pod.get("status", {}).get("podIP")
                    if pod_ip:
                        staging = state["staging_path"]
                        network = self._wait_pilot_network_receipts(
                            context,
                            pod_name=pod_name,
                            instance_id=state["instance_id"],
                            staging_path=staging,
                        )
                        inbound = self._cross_pod_refusal(context, pod_ip)
                        self._ssm(state["instance_id"], [
                            root_command("/usr/bin/sudo", "/usr/bin/touch", f"{staging}/input/network-release"),
                            root_command("/usr/bin/sudo", "/usr/bin/chown", "10001:10001", f"{staging}/input/network-release"),
                            root_command("/usr/bin/sudo", "/usr/bin/chmod", "0444", f"{staging}/input/network-release"),
                        ])
                        break
                self._sleeper(5)
            else:
                raise OperationRefusal("NETWORK_PROBE_RECEIPT_TIMEOUT", "pre-torch network receipt was not observed", outcome="BLOCKED_NETWORK_ISOLATION")
            job_completion = self._wait_pilot_job_complete(
                context,
                timeout_seconds=bound_attempt_window(context.bindings)[
                    "job_active_deadline_seconds"
                ],
            )
            aggregate = self._ssm(state["instance_id"], [
                root_command("/usr/bin/test", "-s", f"{state['staging_path']}/output/aggregate.json"),
                root_command("/usr/bin/sha256sum", f"{state['staging_path']}/output/aggregate.json"),
            ])
        except Exception as exc:
            diagnostic = self._capture_pilot_workload_refusal_diagnostics(
                context, pod_name=pod_name, failure=exc
            )
            if isinstance(exc, OperationRefusal):
                raise OperationRefusal(
                    exc.reason_code,
                    f"{exc.detail}; pilot_diagnostic_sha256={diagnostic['sha256']}",
                    outcome=exc.outcome,
                ) from exc
            raise OperationRefusal(
                "PILOT_WORKLOAD_UNEXPECTED_EXCEPTION",
                f"pilot workload raised {type(exc).__name__}; pilot_diagnostic_sha256={diagnostic['sha256']}",
            ) from exc
        return {"status": "PASS_PILOT_ROWS", "pod": pod_name, "image_prepull_qualification": image_prepull, "dns_resolution_consistency": dns_consistency, "network_probe": "PASS_PRE_TORCH", "network_receipt_readiness": {key: value for key, value in network.items() if key != "network_receipt"}, "inbound_control": inbound, "job_completion": job_completion, "aggregate_receipt_present": True, "aggregate_sha_command": aggregate["command_id"], "pilot_workload_argv_sha256": pilot_workload_argv_sha256, "historical_live_pass_before_this_attempt": False}

    def _ssm_capture_stdout(
        self, instance_id: str, command_text: str, *, timeout_seconds: int = 120
    ) -> str:
        """Run one bounded shell command via SSM and return its Success stdout.

        The returned text is subject to the SSM API's 24,000-character
        StandardOutputContent cap, so callers must keep every command's
        output safely below it (see _ssm_read_file_chunked)."""
        command = self.ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command_text]},
        )
        command_id = command["Command"]["CommandId"]
        stop = time.monotonic() + timeout_seconds
        while time.monotonic() < stop:
            try:
                result = self.ssm.get_command_invocation(
                    CommandId=command_id, InstanceId=instance_id
                )
            except self.ssm.exceptions.InvocationDoesNotExist:
                self._sleeper(2)
                continue
            if result["Status"] == "Success":
                return result["StandardOutputContent"]
            if result["Status"] in {"Failed", "TimedOut", "Cancelled"}:
                raise OperationRefusal(
                    "SSM_CAPTURE_COMMAND_REFUSED",
                    f"bounded SSM capture ended {result['Status']}",
                )
            self._sleeper(2)
        raise OperationRefusal(
            "SSM_CAPTURE_COMMAND_TIMEOUT",
            f"bounded SSM capture exceeded {timeout_seconds} seconds",
        )

    def _ssm_read_file_chunked(
        self, instance_id: str, path: str, *, maximum_bytes: int
    ) -> tuple[bytes, dict[str, Any]]:
        """Read a node file through SSM in bounded base64 chunks.

        A single GetCommandInvocation truncates StandardOutputContent at
        24,000 characters (the attempt-27 refusal), so the file is sliced
        node-side into raw chunks whose base64 encoding stays below the cap,
        reassembled locally, and verified against the node-side SHA-256
        before any parsing."""
        quoted = shlex.quote(path)
        identity = self._ssm_capture_stdout(
            instance_id,
            f"/usr/bin/wc -c < {quoted}; /usr/bin/sha256sum {quoted}",
        )
        lines = [line for line in identity.splitlines() if line.strip()]
        try:
            expected_bytes = int(lines[0].strip())
            expected_sha256 = lines[1].split()[0]
        except (IndexError, ValueError) as exc:
            raise OperationRefusal(
                "FILE_READBACK_IDENTITY_MALFORMED",
                "chunked readback size/sha probe output is malformed",
            ) from exc
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or expected_bytes <= 0:
            raise OperationRefusal(
                "FILE_READBACK_IDENTITY_MALFORMED",
                "chunked readback identity values are outside bounds",
            )
        if expected_bytes > maximum_bytes:
            raise OperationRefusal(
                "FILE_READBACK_TOO_LARGE",
                f"file is {expected_bytes} bytes; the readback bound is {maximum_bytes}",
            )
        chunk_raw_bytes = SSM_READBACK_RAW_CHUNK_BYTES
        chunk_count = (expected_bytes + chunk_raw_bytes - 1) // chunk_raw_bytes
        parts: list[bytes] = []
        for index in range(chunk_count):
            offset = index * chunk_raw_bytes
            encoded = self._ssm_capture_stdout(
                instance_id,
                f"/usr/bin/tail -c +{offset + 1} {quoted} | "
                f"/usr/bin/head -c {chunk_raw_bytes} | "
                "/usr/bin/base64 | /usr/bin/tr -d '\\n'",
            )
            try:
                parts.append(base64.b64decode(encoded.strip(), validate=True))
            except Exception as exc:
                raise OperationRefusal(
                    "FILE_READBACK_CHUNK_MALFORMED",
                    f"chunk {index + 1} of {chunk_count} is not valid base64",
                ) from exc
        body = b"".join(parts)
        if len(body) != expected_bytes or hashlib.sha256(body).hexdigest() != expected_sha256:
            raise OperationRefusal(
                "FILE_READBACK_INTEGRITY_DIFFERS",
                "reassembled file differs from the node-side size or SHA-256",
            )
        return body, {
            "bytes": expected_bytes,
            "sha256": expected_sha256,
            "chunk_count": chunk_count,
            "chunk_raw_bytes": chunk_raw_bytes,
            "window_policy": "CHUNKED_BASE64_UNDER_SSM_OUTPUT_CAP",
        }

    def aggregate_report(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        body, readback = self._ssm_read_file_chunked(
            state["instance_id"],
            f"{state['staging_path']}/output/aggregate.json",
            maximum_bytes=AGGREGATE_READBACK_MAXIMUM_BYTES,
        )
        try:
            value = json.loads(body)
        except Exception as exc:
            raise OperationRefusal("AGGREGATE_RECEIPT_MALFORMED", "aggregate is not JSON") from exc
        if not isinstance(value, dict) or value.get("status") not in {"PASS_AGGREGATE", "INCOMPLETE_MEASUREMENT"}:
            raise OperationRefusal("AGGREGATE_STATUS_DIFFERS", "aggregate status differs")
        expected_rows = context.bindings["input_freeze"]["pilot_rows"]
        minimum = expected_rows * 3
        selection = json.loads((context.workdir / "pilot-selection.json").read_bytes())
        conditioning = json.loads((self.root / "services/asr-eval-runtime/assets/language-conditioning-v1.json").read_bytes())["languages"]
        conditioned = sum(
            int(conditioning[row["language"]][provider] is not None)
            for row in selection["rows"]
            for provider in ("whisper", "meta_llm")
        )
        expected_completed = minimum + conditioned
        expected_not_applicable = expected_rows * 2 - conditioned
        if (
            value.get("runtime_rows") != expected_rows
            or value.get("completed_inferences") != expected_completed
            or value.get("not_applicable") != expected_not_applicable
        ):
            raise OperationRefusal("AGGREGATE_COMPLETENESS_DIFFERS", "required unconditioned rows are incomplete")
        output = context.workdir / "aggregate-report.json"
        write_exclusive(output, canonical_json(value))
        return {"status": "PASS_AGGREGATE_REPORT" if value["status"] == "PASS_AGGREGATE" else "INCOMPLETE_MEASUREMENT", "aggregate_sha256": _sha(output), "readback": readback, "runtime_rows": value["runtime_rows"], "completed_inferences": value["completed_inferences"], "not_applicable": value["not_applicable"], "gpu_memory": value["aggregate"]["gpu_memory"], "groups": len(value["aggregate"]["groups"])}

    def cleanup_and_expiry(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        errors = []
        try:
            self._update_kubeconfig(context)
            if state.get("namespace"):
                self._command(["kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "delete", "namespace", NAMESPACE, "--ignore-not-found=true", "--wait=true", "--timeout=5m"], check=False, timeout=360)
            if state.get("dra_installed"):
                self._command(["kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "delete", "namespace", "nvidia-dra-driver", "--ignore-not-found=true", "--wait=true", "--timeout=5m"], check=False, timeout=360)
        except Exception as exc:
            errors.append(f"kubernetes:{type(exc).__name__}")
        if state.get("instance_id"):
            try:
                self._ssm(state["instance_id"], [
                    "#!/bin/bash",
                    "set -euo pipefail",
                    root_command("/usr/bin/sudo", "/usr/bin/rm", "-rf", "--", f"/var/lib/medzen-asr-eval/attempt-{context.attempt}"),
                    "/usr/bin/mountpoint -q /var/lib/medzen-asr-eval && /usr/bin/sudo /usr/bin/umount /var/lib/medzen-asr-eval || true",
                ], timeout_seconds=180)
            except Exception as exc:
                errors.append(f"staging:{type(exc).__name__}")
        if state.get("volume_id"):
            volume_errors: list[str] = []
            try:
                self.ec2.detach_volume(VolumeId=state["volume_id"], Force=False)
                self.ec2.get_waiter("volume_available").wait(VolumeIds=[state["volume_id"]], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
            except Exception as exc:
                volume_errors.append(f"detach:{type(exc).__name__}")
            try:
                self.ec2.delete_volume(VolumeId=state["volume_id"])
            except Exception as exc:
                volume_errors.append(f"delete:{type(exc).__name__}")
            if volume_errors:
                errors.append("volume:" + ",".join(volume_errors))
        try:
            if state.get("gpu_scaled"):
                self.eks.update_nodegroup_config(clusterName=CLUSTER, nodegroupName=GPU_NODEGROUP, scalingConfig={"minSize": 0, "maxSize": 1, "desiredSize": 0})
                self._wait_nodegroup(0)
            else:
                group = self._nodegroup(GPU_NODEGROUP)
                scaling = group["scalingConfig"]
                if (
                    group["status"] != "ACTIVE"
                    or scaling["desiredSize"] != 0
                    or group.get("health", {}).get("issues")
                ):
                    raise RuntimeError("GPU node group is not safely zero")
        except Exception as exc:
            errors.append(f"gpu:{type(exc).__name__}")
        if state.get("endpoint_ids"):
            try:
                self.ec2.delete_vpc_endpoints(VpcEndpointIds=state["endpoint_ids"])
                stop = time.monotonic() + 600
                while time.monotonic() < stop:
                    remaining = self.ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-endpoint-id", "Values": state["endpoint_ids"]}])["VpcEndpoints"]
                    if not remaining:
                        break
                    self._sleeper(5)
                else:
                    raise RuntimeError("endpoint deletion timeout")
            except Exception as exc:
                errors.append(f"endpoints:{type(exc).__name__}")
        if state.get("endpoint_security_group"):
            try:
                self.ec2.delete_security_group(GroupId=state["endpoint_security_group"])
            except Exception as exc:
                errors.append(f"endpoint-sg:{type(exc).__name__}")
        if state.get("cni_changed"):
            try:
                self.eks.update_addon(clusterName=CLUSTER, addonName="vpc-cni", configurationValues=state["cni_addon_before"] or "{}", resolveConflicts="PRESERVE")
                self._update_kubeconfig(context)
                env = state.get("cni_daemonset_env_before") or []
                previous = next((item.get("value") for item in env if item.get("name") == "NETWORK_POLICY_ENFORCING_MODE"), None)
                assignment = "NETWORK_POLICY_ENFORCING_MODE-" if previous is None else f"NETWORK_POLICY_ENFORCING_MODE={previous}"
                self._kubectl(context, "set", "env", "daemonset/aws-node", "-n", "kube-system", assignment)
            except Exception as exc:
                errors.append(f"cni:{type(exc).__name__}")
        if state.get("scan_configuration_before") is not None:
            try:
                before = validate_configuration(state["scan_configuration_before"])
                current = self.ecr.get_registry_scanning_configuration()[
                    "scanningConfiguration"
                ]
                if canonical_configuration(current) != canonical_configuration(before):
                    self.ecr.put_registry_scanning_configuration(
                        scanType=before["scanType"], rules=before["rules"]
                    )
                    self._wait_registry_scanning_configuration(before)
            except Exception as exc:
                errors.append(f"ecr-scan-config:{type(exc).__name__}")
        if state.get("deadline_action"):
            try:
                self.asg.delete_scheduled_action(AutoScalingGroupName=self._gpu_asg(context), ScheduledActionName=state["deadline_action"])
            except Exception as exc:
                errors.append(f"deadline:{type(exc).__name__}")
        gpu = self._nodegroup(GPU_NODEGROUP)["scalingConfig"]["desiredSize"]
        cpu = self._nodegroup(CPU_NODEGROUP)["scalingConfig"]["desiredSize"]
        endpoints = self.ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [VPC]}, {"Name": "tag:MedZenPurpose", "Values": ["asr-base-model-eval"]}])["VpcEndpoints"]
        volumes = self.ec2.describe_volumes(Filters=[{"Name": "tag:MedZenPurpose", "Values": ["asr-base-model-eval"]}, {"Name": "status", "Values": ["available", "in-use", "creating"]}])["Volumes"]
        zero = {"cpu_desired": cpu, "gpu_desired": gpu, "endpoints": len(endpoints), "volumes": len(volumes)}
        if errors or any(zero.values()):
            raise OperationRefusal("CLEANUP_ZERO_STATE_REFUSED", f"cleanup errors={','.join(errors)[:256]} zero={zero}")
        return {"status": "PASS_CLEANUP_AND_EXPIRY", **zero, "namespace": 0, "staging": 0, "deadline_actions": 0, "reservation_closed": True}
