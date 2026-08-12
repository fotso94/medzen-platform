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
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


from pipeline.asr_base_model_pilot_receipts import canonical_json, write_exclusive
from scripts.asr_external_tool import ExternalToolTimeout, run_external
from scripts.asr_base_model_pilot_assets import (
    AssetRefusal,
    ObjectStore,
    select_pilot_rows,
    sha256_file,
    stage_assets,
)
from scripts.asr_base_model_ecr_scanning import (
    canonical_configuration,
    merge_scan_on_push_filter,
    validate_configuration,
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
from scripts.asr_base_model_pilot_integrity import (
    PilotIntegrityRefusal,
    read_committed_artifact,
    validate_executor_module_bindings,
    validate_governance_commit_boundary,
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
    GPU_ASG,
    NAMESPACE,
    NODE_SG,
    PROFILE,
    REGION,
    VPC,
    exact_plan,
    validate_plan,
)
from scripts.asr_base_model_pilot_runner import (
    AttemptContext,
    OperationRefusal,
    validate_authorization_payload,
)


CALLER = f"arn:aws:iam::{ACCOUNT}:user/s.fotso"
BUCKET = "medzen-speech"
GPU_NODEGROUP = "gpu"
CPU_NODEGROUP = "cpu"
ECR_REPOSITORY = "medzen-asr-eval-runtime"
DRA_MANIFEST = Path("platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml")
EXPECTED_HIGHS = {
    ("CVE-2026-24747", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2026-4538", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2025-55552", "torch", "2.8.0+cu128", "HIGH"),
    ("CVE-2025-55551", "torch", "2.8.0+cu128", "HIGH"),
}


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
    def __init__(self, root: Path):
        self.root = root
        try:
            import boto3
        except Exception as exc:
            raise OperationRefusal("BOTO3_ABSENT", "the reviewed AWS SDK is unavailable") from exc
        self.session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        self.sts = self.session.client("sts")
        self.eks = self.session.client("eks")
        self.ec2 = self.session.client("ec2")
        self.ecr = self.session.client("ecr")
        self.s3 = self.session.client("s3")
        self.asg = self.session.client("autoscaling")
        self.ssm = self.session.client("ssm")

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
            }
        return json.loads(snapshots[-1].read_bytes())

    def _save_state(self, context: AttemptContext, state: dict[str, Any]) -> None:
        directory = context.workdir / "state"
        sequence = len(list(directory.glob("*.json"))) + 1 if directory.exists() else 1
        write_exclusive(directory / f"{sequence:04d}.json", canonical_json(state))

    def _aws(self, *args: str, timeout: int = 900) -> dict[str, Any]:
        return _json_command(["aws", "--profile", PROFILE, "--region", REGION, *args, "--output", "json"], timeout=timeout)

    def _kubectl(self, context: AttemptContext, *args: str, stdin: bytes | None = None,
                 timeout: int = 900, json_output: bool = False) -> dict[str, Any] | bytes:
        kubeconfig = context.workdir / "kubeconfig"
        command = ["kubectl", "--kubeconfig", str(kubeconfig), *args]
        if json_output:
            return _json_command(command + ["-o", "json"], timeout=timeout)
        return _run(command, stdin=stdin, timeout=timeout).stdout

    def _update_kubeconfig(self, context: AttemptContext) -> None:
        _run([
            "aws", "--profile", PROFILE, "--region", REGION, "eks", "update-kubeconfig",
            "--name", CLUSTER, "--kubeconfig", str(context.workdir / "kubeconfig"), "--alias", "medzen-asr-eval",
        ])

    def _nodegroup(self, name: str) -> dict[str, Any]:
        return self.eks.describe_nodegroup(clusterName=CLUSTER, nodegroupName=name)["nodegroup"]

    def _wait_nodegroup(self, desired: int, timeout_seconds: int = 1200) -> dict[str, Any]:
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
            time.sleep(10)
        raise OperationRefusal("GPU_NODEGROUP_STABILITY_TIMEOUT", "GPU nodegroup did not reach three stable observations")

    def _ssm(self, instance_id: str, commands: list[str], *, timeout_seconds: int = 900) -> dict[str, Any]:
        response = self.ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            TimeoutSeconds=timeout_seconds,
            Parameters={"commands": commands},
        )
        command_id = response["Command"]["CommandId"]
        stop = time.monotonic() + timeout_seconds + 60
        while time.monotonic() < stop:
            try:
                value = self.ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            except self.ssm.exceptions.InvocationDoesNotExist:
                time.sleep(2)
                continue
            status = value["Status"]
            if status == "Success":
                stdout = value.get("StandardOutputContent", "")
                return {"command_id": command_id, "status": status, "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(), "stdout": stdout}
            if status in {"Cancelled", "TimedOut", "Failed", "Cancelling"}:
                raise OperationRefusal("SSM_COMMAND_REFUSED", f"SSM command {command_id} ended {status}")
            time.sleep(2)
        raise OperationRefusal("SSM_COMMAND_TIMEOUT", f"SSM command {command_id} exceeded its bound")

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
        )
        try:
            source_integrity = validate_executor_module_bindings(
                self.root,
                bindings.get("executor_modules"),
            )
        except PilotIntegrityRefusal as exc:
            raise OperationRefusal(exc.reason_code, exc.detail) from exc
        expires = datetime.fromisoformat(authorization["expires_utc"].replace("Z", "+00:00"))
        if _utc() >= expires:
            raise OperationRefusal("RISK_ACCEPTANCE_EXPIRED", "offline evaluation acceptance has expired")
        if _sha(context.packet_path) != context.receipts.packet_sha256 or _sha(context.authorization_path) != context.receipts.authorization_sha256:
            raise OperationRefusal("REVIEWED_FILE_HASH_DIFFERS", "packet or authorization changed after binding")
        if context.attempt == 9:
            prestage_binding = bindings.get("artifact_prestage_proof")
            if not isinstance(prestage_binding, dict):
                raise OperationRefusal(
                    "COMMITTED_PRESTAGE_PROOF_ABSENT",
                    "attempt 9 requires the committed complete-bundle pre-stage proof",
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
        self.asg.put_scheduled_update_group_action(
            AutoScalingGroupName=GPU_ASG,
            ScheduledActionName=action,
            StartTime=deadline,
            DesiredCapacity=0,
        )
        state["deadline_action"] = action
        self._save_state(context, state)
        readback = self.asg.describe_scheduled_actions(AutoScalingGroupName=GPU_ASG, ScheduledActionNames=[action])["ScheduledUpdateGroupActions"]
        if len(readback) != 1 or readback[0].get("DesiredCapacity") != 0:
            raise OperationRefusal("DEADLINE_READBACK_DIFFERS", "deadline scheduled action is not exact")
        return {**common, "deadline_utc": deadline.isoformat()}

    def input_freeze_and_no_phi(self, context: AttemptContext) -> dict[str, Any]:
        binding = context.bindings["input_freeze"]
        manifest_root = context.workdir / "manifests"
        manifest_root.mkdir(parents=True, exist_ok=False)
        _run([
            "aws", "--profile", PROFILE, "--region", REGION, "s3", "sync",
            "s3://medzen-speech/eval/", str(manifest_root), "--exclude", "*", "--include", "*/asr/*/manifest*.jsonl",
        ], timeout=1800)
        outputs = []
        for run in (1, 2):
            completed = _run([
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
        selection = select_pilot_rows(manifest_root)
        if selection["public_row_list_sha256"] != binding["pilot_row_list_sha256"] or len(selection["rows"]) > 540:
            raise OperationRefusal("PILOT_ROW_LIST_DIFFERS", "deterministic pilot row list differs")
        write_exclusive(context.workdir / "pilot-selection.json", canonical_json(selection))
        return {"status": "PASS_INPUT_FREEZE_AND_NO_PHI", "runs": 2, "byte_identical": True, "rows": audit["inventory"]["rows"], "pilot_rows": len(selection["rows"]), "pilot_row_list_sha256": selection["public_row_list_sha256"], "phi": False}

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

    def _image_scan(self, repository: str, digest: str) -> dict[str, Any]:
        stop = time.monotonic() + 1800
        while time.monotonic() < stop:
            try:
                response = self.ecr.describe_image_scan_findings(repositoryName=repository, imageId={"imageDigest": digest})
            except Exception:
                time.sleep(10)
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
                if critical or high != EXPECTED_HIGHS:
                    raise OperationRefusal("AUTHORITATIVE_SCAN_FINDINGS_DIFFER", "authoritative critical/high tuple set differs")
                return {"status": "COMPLETE", "critical": 0, "high": len(high), "high_tuples": sorted(high)}
            time.sleep(10)
        raise OperationRefusal("AUTHORITATIVE_SCAN_TIMEOUT", "ECR scan did not complete")

    def _wait_registry_scanning_configuration(
        self, expected: dict[str, Any], *, timeout_seconds: int = 120
    ) -> dict[str, Any]:
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
            time.sleep(2)
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

    def image_publication_and_scan(self, context: AttemptContext) -> dict[str, Any]:
        image = context.bindings["image"]
        state = self._state(context)
        try:
            repository = self.ecr.describe_repositories(repositoryNames=[ECR_REPOSITORY])["repositories"][0]
        except self.ecr.exceptions.RepositoryNotFoundException:
            raise OperationRefusal(
                "ECR_EVALUATION_REPOSITORY_ABSENT",
                "the packet-2026-002A evaluation repository does not exist",
            )
        if repository["imageTagMutability"] != "IMMUTABLE" or repository["encryptionConfiguration"]["encryptionType"] != "KMS":
            raise OperationRefusal("ECR_REPOSITORY_BOUNDARY_DIFFERS", "evaluation repository is not immutable and KMS-encrypted")
        if context.attempt in {5, 6, 7, 8, 9}:
            exact = self._existing_exact_image(image)
            try:
                gate_binding = validate_security_binding(context.bindings.get("security_gate", {}))
                with tempfile.TemporaryDirectory(prefix="digest-rescan-", dir=context.workdir) as temporary:
                    scan = scan_exact_ecr_child(
                        self.ecr,
                        ECR_REPOSITORY,
                        image,
                        Path(temporary),
                    )
                    retained_sarif = context.workdir / "docker-scout-ecr-rescan.sarif.json"
                    source_sarif = Path(temporary) / "docker-scout.sarif.json"
                    write_exclusive(retained_sarif, source_sarif.read_bytes())
                    scan["docker_scout"]["sarif_path"] = str(retained_sarif)
                    scan["docker_scout"].pop("scanned_oci_layout", None)
            except DigestRescanRefusal as exc:
                raise OperationRefusal(exc.reason_code, exc.detail) from exc
            return {
                "status": "PASS_IMAGE_PUBLICATION_AND_SCAN",
                "repository": ECR_REPOSITORY,
                "oci_index_digest": image["oci_index_digest"],
                "linux_amd64_digest": exact["child"]["digest"],
                "publication": {"status": "SKIPPED_EXISTING_EXACT_IMAGE", "aws_image_mutations": 0},
                "security_gate_binding": gate_binding,
                "security_gate": scan,
            }
        local = _run(["docker", "image", "inspect", image["local_tag"], "--format", "{{json .Config.Labels}}"])
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
                publication = publish_exact_image(
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
        scan = self._image_scan(ECR_REPOSITORY, children[0]["digest"])
        return {"status": "PASS_IMAGE_PUBLICATION_AND_SCAN", "repository": ECR_REPOSITORY, "oci_index_digest": image["oci_index_digest"], "linux_amd64_digest": children[0]["digest"], "publication": publication, **scan}

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
            verification = verify_prestaged_bundle(
                self.s3,
                proof,
                expected_bundle_sha256=expected["sha256"],
                destination=bundle_path,
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
            **verification,
        }

    def _endpoint_policy(self, context: AttemptContext, service: str) -> str:
        prefix = context.bindings["pilot_bundle"]["sha256"]
        if service == "s3":
            resources = [
                f"arn:aws:s3:::{BUCKET}/research/asr-base-model/pilot/{prefix}/*",
                f"arn:aws:s3:::{BUCKET}/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/*",
                "arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*",
            ]
            statement = [{"Effect": "Allow", "Principal": "*", "Action": ["s3:GetObject"], "Resource": resources}]
        else:
            repositories = [
                (ACCOUNT, ECR_REPOSITORY),
                (ACCOUNT, "medzen-nvidia-dra"),
                ("602401143452", "amazon-k8s-cni-init"),
                ("602401143452", "amazon-k8s-cni"),
                ("602401143452", "amazon/aws-network-policy-agent"),
                ("602401143452", "eks/eks-pod-identity-agent"),
                ("602401143452", "eks/kube-proxy"),
            ]
            repository_arns = [f"arn:aws:ecr:{REGION}:{account}:repository/{name}" for account, name in repositories]
            statement = [
                {"Effect": "Allow", "Principal": "*", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
                {"Effect": "Allow", "Principal": "*", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], "Resource": repository_arns},
            ]
        return json.dumps({"Version": "2012-10-17", "Statement": statement}, sort_keys=True)

    def private_endpoint_and_policy_gate(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        aws = context.bindings["aws"]
        sg = self.ec2.create_security_group(GroupName=f"medzen-asr-eval-vpce-a{context.attempt}", Description="MedZen ASR offline evaluation endpoint TLS", VpcId=VPC, TagSpecifications=[{"ResourceType": "security-group", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["GroupId"]
        state["endpoint_security_group"] = sg
        self._save_state(context, state)
        self.ec2.revoke_security_group_egress(GroupId=sg, IpPermissions=[{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        self.ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "UserIdGroupPairs": [{"GroupId": NODE_SG}]}])
        endpoint_ids = []
        for service in ("ecr.api", "ecr.dkr"):
            value = self.ec2.create_vpc_endpoint(VpcEndpointType="Interface", VpcId=VPC, ServiceName=f"com.amazonaws.{REGION}.{service}", SubnetIds=aws["private_subnet_ids"], SecurityGroupIds=[sg], PrivateDnsEnabled=True, PolicyDocument=self._endpoint_policy(context, "ecr"), TagSpecifications=[{"ResourceType": "vpc-endpoint", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["VpcEndpoint"]
            endpoint_ids.append(value["VpcEndpointId"])
            state["endpoint_ids"] = list(endpoint_ids)
            self._save_state(context, state)
        s3_endpoint = self.ec2.create_vpc_endpoint(VpcEndpointType="Gateway", VpcId=VPC, ServiceName=f"com.amazonaws.{REGION}.s3", RouteTableIds=aws["private_route_table_ids"], PolicyDocument=self._endpoint_policy(context, "s3"), TagSpecifications=[{"ResourceType": "vpc-endpoint", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["VpcEndpoint"]
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
            time.sleep(10)
        else:
            raise OperationRefusal("PRIVATE_ENDPOINT_AVAILABILITY_TIMEOUT", "private endpoints did not become available")
        interfaces = [item for item in described if item["VpcEndpointType"] == "Interface"]
        eni_ids = [eni for item in interfaces for eni in item["NetworkInterfaceIds"]]
        enis = self.ec2.describe_network_interfaces(NetworkInterfaceIds=eni_ids)["NetworkInterfaces"]
        endpoint_ips = sorted(item["PrivateIpAddress"] for item in enis)
        prefix_list_id = next(item["PrefixListId"] for item in described if item["VpcEndpointType"] == "Gateway")
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
        return {"status": "PASS_PRIVATE_ENDPOINT_AND_POLICY_GATE", "endpoint_ids": endpoint_ids, "endpoint_ips": endpoint_ips, "s3_prefix_list_id": prefix_list_id, "s3_cidrs": s3_cidrs, "cni_mode": "strict", "workload_sha256": _sha(context.workdir / "workload.yaml"), "empirical_pre_torch_probe_pending": True}

    def gpu_and_sampler_gate(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        self.eks.update_nodegroup_config(clusterName=CLUSTER, nodegroupName=GPU_NODEGROUP, scalingConfig={"minSize": 0, "maxSize": 1, "desiredSize": 1})
        state["gpu_scaled"] = True
        self._save_state(context, state)
        stable = self._wait_nodegroup(1)
        instance_id = stable["instance_ids"][0]
        state["instance_id"] = instance_id
        self._save_state(context, state)
        self._update_kubeconfig(context)
        node = self._kubectl(context, "get", "nodes", "-l", "workload=gpu", json_output=True)
        if len(node.get("items", [])) != 1:
            raise OperationRefusal("EXACT_GPU_NODE_ABSENT", "exactly one Kubernetes GPU node is required")
        node_name = node["items"][0]["metadata"]["name"]
        state["node_name"] = node_name
        self._save_state(context, state)
        instance = self.ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
        volume = self.ec2.create_volume(AvailabilityZone=instance["Placement"]["AvailabilityZone"], Size=60, VolumeType="gp3", Encrypted=True, KmsKeyId=context.bindings["aws"]["ebs_kms_key_arn"], TagSpecifications=[{"ResourceType": "volume", "Tags": [{"Key": "MedZenPurpose", "Value": "asr-base-model-eval"}]}])["VolumeId"]
        state["volume_id"] = volume
        self._save_state(context, state)
        waiter = self.ec2.get_waiter("volume_available")
        waiter.wait(VolumeIds=[volume], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        self.ec2.attach_volume(VolumeId=volume, InstanceId=instance_id, Device="/dev/sdf")
        volume_serial = volume.replace("-", "")
        self._ssm(instance_id, [
            "set -euo pipefail",
            f"device=$(readlink -f /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_{volume_serial})",
            "test -b \"$device\"",
            "sudo mkfs.ext4 -F \"$device\" >/dev/null",
            "sudo mkdir -p /var/lib/medzen-asr-eval",
            "sudo mount \"$device\" /var/lib/medzen-asr-eval",
            "sudo chown 10001:10001 /var/lib/medzen-asr-eval",
        ])
        dra = (self.root / DRA_MANIFEST).read_bytes()
        self._kubectl(context, "apply", "-f", "-", stdin=dra)
        state["dra_installed"] = True
        self._save_state(context, state)
        from scripts.run_b6a_003c_c_proof import wait_for_stable_dra
        readiness = wait_for_stable_dra(kubeconfig=context.workdir / "kubeconfig", timeout_seconds=600)
        dra_pod = self._kubectl(context, "get", "pods", "-n", "nvidia-dra-driver", "-l", "dra-driver-nvidia-gpu-component=kubelet-plugin", json_output=True)["items"][0]["metadata"]["name"]
        sample = _run([
            "kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "exec", "-n", "nvidia-dra-driver", dra_pod, "-c", "gpus", "--",
            "/busybox/sh", "-c", "i=0; while [ $i -lt 120 ]; do /driver-root/usr/bin/nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; i=$((i+1)); /busybox/sleep 0.1; done",
        ], timeout=120)
        samples = [float(line) for line in sample.stdout.decode().splitlines() if line.strip()]
        if len(samples) != 120 or any(value < 0 for value in samples):
            raise OperationRefusal("GPU_SAMPLER_SELF_TEST_REFUSED", "exactly 120 numeric sampler observations are required")
        return {"status": "PASS_GPU_AND_SAMPLER_GATE", "gpu_node": node_name, "instance_id": instance_id, "volume_id": volume, "volume_gib": 60, "dra": readiness, "samples": len(samples), "baseline_mib": samples[0], "peak_mib": max(samples)}

    def node_local_input_stage(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        instance_id = state.get("instance_id")
        if not instance_id:
            raise OperationRefusal("GPU_INSTANCE_ID_ABSENT", "node-local staging requires the exact GPU instance")
        bundle = json.loads((context.workdir / "pilot-bundle.json").read_bytes())
        prefix = f"research/asr-base-model/pilot/{context.bindings['pilot_bundle']['sha256']}/"
        commands = [
            "set -euo pipefail",
            f"base=/var/lib/medzen-asr-eval/attempt-{context.attempt}",
            "sudo rm -rf \"$base\"",
            "sudo install -d -o 10001 -g 10001 \"$base/input/audio\" \"$base/input/models/whisper-large-v3-ct2\" \"$base/output\"",
        ]
        node_objects: list[dict[str, Any]] = []
        for item in bundle["objects"]:
            key = item["key"]
            if key.endswith(("runtime-rows.json", "model-bindings.json")):
                relative = key.removeprefix(prefix)
            elif "/bundles/" in key:
                relative = "parts/" + key.removeprefix(prefix + "bundles/")
            else:
                continue
            url = self.s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": key, "VersionId": item["version_id"]}, ExpiresIn=900)
            destination = f"$base/input/{relative}"
            concrete_destination = destination.replace("$base", f"/var/lib/medzen-asr-eval/attempt-{context.attempt}")
            parent = str(Path(concrete_destination).parent)
            commands.extend([
                f"sudo install -d -o 10001 -g 10001 {json.dumps(parent)}",
                f"sudo -u '#10001' curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 {json.dumps(url)} -o {json.dumps(concrete_destination)}",
                f"test \"$(sha256sum {json.dumps(concrete_destination)} | cut -d' ' -f1)\" = {item['sha256']}",
                f"test \"$(stat -c %s {json.dumps(concrete_destination)})\" = {item['bytes']}",
            ])
            node_objects.append({"key": key, "sha256": item["sha256"], "bytes": item["bytes"]})
        for name, assembly in bundle["assemblies"].items():
            root = f"/var/lib/medzen-asr-eval/attempt-{context.attempt}"
            parts = " ".join(json.dumps(f"{root}/input/parts/{item['key'].removeprefix(prefix + 'bundles/')}") for item in assembly["parts"])
            destination = f"{root}/input/{assembly['destination']}"
            commands.extend([
                f"sudo install -d -o 10001 -g 10001 {json.dumps(str(Path(destination).parent))}",
                f"sudo -u '#10001' sh -c {json.dumps(f'cat {parts} > {destination}')}",
                f"test \"$(sha256sum {json.dumps(destination)} | cut -d' ' -f1)\" = {assembly['sha256']}",
                f"test \"$(stat -c %s {json.dumps(destination)})\" = {assembly['bytes']}",
            ])
            if assembly.get("archive"):
                commands.extend([
                    f"sudo -u '#10001' tar --extract --file {json.dumps(destination)} --directory {json.dumps(root + '/input')} --no-same-owner --no-same-permissions",
                    f"test \"$(find {json.dumps(root + '/input/audio')} -type f | wc -l)\" = {assembly['files']}",
                    f"sudo rm -f {json.dumps(destination)}",
                ])
        whisper_prefix = "b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/"
        model_bindings = json.loads((context.workdir / "asset-staging/model-bindings.json").read_bytes())
        for relative, item in sorted(model_bindings["whisper_files"].items()):
            url = self.s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": whisper_prefix + relative}, ExpiresIn=900)
            destination = f"/var/lib/medzen-asr-eval/attempt-{context.attempt}/input/models/whisper-large-v3-ct2/{relative}"
            commands.extend([
                f"sudo install -d -o 10001 -g 10001 {json.dumps(str(Path(destination).parent))}",
                f"sudo -u '#10001' curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 {json.dumps(url)} -o {json.dumps(destination)}",
                f"test \"$(sha256sum {json.dumps(destination)} | cut -d' ' -f1)\" = {item['sha256']}",
            ])
        network = base64.b64encode((context.workdir / "network-binding.json").read_bytes()).decode()
        commands.extend([
            f"printf %s {json.dumps(network)} | base64 -d | sudo -u '#10001' tee \"$base/input/network-binding.json\" >/dev/null",
            "sudo find \"$base/input\" -type d -exec chmod 0555 {} +",
            "sudo find \"$base/input\" -type f -exec chmod 0444 {} +",
        ])
        result = self._ssm(instance_id, commands, timeout_seconds=1800)
        state["staging_path"] = f"/var/lib/medzen-asr-eval/attempt-{context.attempt}"
        self._save_state(context, state)
        return {"status": "PASS_NODE_LOCAL_INPUT_STAGE", "instance_id": instance_id, "bundle_hash_verified": True, "objects": len(node_objects), "ssm_command_id": result["command_id"], "credentials_in_container": False, "urls_in_container": False}

    def _cross_pod_refusal(self, context: AttemptContext, pod_ip: str) -> dict[str, Any]:
        image = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPOSITORY}@{context.bindings['image']['linux_amd64_digest']}"
        probe = {
            "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "asr-eval-inbound-control", "namespace": NAMESPACE, "labels": {"app.kubernetes.io/name": "asr-eval-inbound-control"}},
            "spec": {"automountServiceAccountToken": False, "restartPolicy": "Never", "nodeSelector": {"workload": "gpu"}, "tolerations": [{"key": "nvidia.com/gpu", "operator": "Equal", "value": "true", "effect": "NoSchedule"}], "containers": [{"name": "control", "image": image, "command": ["python", "-c", "import socket,sys; s=socket.socket(); s.settimeout(3); rc=s.connect_ex((sys.argv[1],8080)); sys.exit(0 if rc else 9)", pod_ip], "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]},
        }
        encoded = canonical_json(probe)
        self._kubectl(context, "apply", "-f", "-", stdin=encoded)
        completed = _run(["kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "wait", "-n", NAMESPACE, "--for=jsonpath={.status.phase}=Succeeded", "pod/asr-eval-inbound-control", "--timeout=60s"], check=False, timeout=90)
        logs_hash = hashlib.sha256(self._kubectl(context, "logs", "-n", NAMESPACE, "pod/asr-eval-inbound-control") or b"").hexdigest()
        self._kubectl(context, "delete", "pod/asr-eval-inbound-control", "-n", NAMESPACE, "--wait=true")
        if completed.returncode != 0:
            raise OperationRefusal("NETWORK_INBOUND_CONTROL_ACCEPTED", "cross-pod TCP connection unexpectedly succeeded")
        return {"status": "REFUSED_AS_REQUIRED", "target_port": 8080, "logs_sha256": logs_hash}

    def pilot_rows(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        workload = (context.workdir / "workload.yaml").read_bytes()
        documents = list(__import__("yaml").safe_load_all(workload))
        infrastructure = __import__("yaml").safe_dump_all(documents[:-1], sort_keys=False).encode()
        job = __import__("yaml").safe_dump(documents[-1], sort_keys=False).encode()
        self._kubectl(context, "apply", "-f", "-", stdin=infrastructure)
        state["namespace"] = True
        self._save_state(context, state)
        self._kubectl(context, "apply", "-f", "-", stdin=job)
        stop = time.monotonic() + 900
        pod_name = None
        while time.monotonic() < stop:
            pods = self._kubectl(context, "get", "pods", "-n", NAMESPACE, "-l", "app.kubernetes.io/name=asr-base-model-pilot", json_output=True)
            if len(pods.get("items", [])) == 1:
                pod = pods["items"][0]
                pod_name = pod["metadata"]["name"]
                pod_ip = pod.get("status", {}).get("podIP")
                if pod_ip:
                    network = self._ssm(state["instance_id"], [f"test -s {state['staging_path']}/output/network-probe.json", f"test -s {state['staging_path']}/output/inbound-listener-ready", f"cat {state['staging_path']}/output/network-probe.json"], timeout_seconds=60)
                    try:
                        network_value = json.loads(network["stdout"])
                    except Exception as exc:
                        raise OperationRefusal("NETWORK_PROBE_RECEIPT_MALFORMED", "pre-torch network receipt is not JSON", outcome="BLOCKED_NETWORK_ISOLATION") from exc
                    if network_value.get("status") != "PASS_NETWORK_ISOLATION_PRE_TORCH" or network_value.get("torch_imported") is not False:
                        raise OperationRefusal("NETWORK_PROBE_REFUSED", "pre-torch private-endpoint probe did not pass", outcome="BLOCKED_NETWORK_ISOLATION")
                    inbound = self._cross_pod_refusal(context, pod_ip)
                    self._ssm(state["instance_id"], [f"sudo touch {state['staging_path']}/input/network-release", f"sudo chown 10001:10001 {state['staging_path']}/input/network-release", f"sudo chmod 0444 {state['staging_path']}/input/network-release"])
                    break
            time.sleep(5)
        else:
            raise OperationRefusal("NETWORK_PROBE_RECEIPT_TIMEOUT", "pre-torch network receipt was not observed", outcome="BLOCKED_NETWORK_ISOLATION")
        waited = _run(["kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "wait", "-n", NAMESPACE, "--for=condition=complete", f"job/asr-base-model-pilot-a{context.attempt}", "--timeout=9000s"], check=False, timeout=9060)
        if waited.returncode != 0:
            raise OperationRefusal("PILOT_JOB_REFUSED", "offline pilot job did not complete")
        aggregate = self._ssm(state["instance_id"], [f"test -s {state['staging_path']}/output/aggregate.json", f"sha256sum {state['staging_path']}/output/aggregate.json"])
        return {"status": "PASS_PILOT_ROWS", "pod": pod_name, "network_probe": "PASS_PRE_TORCH", "inbound_control": inbound, "aggregate_receipt_present": True, "aggregate_sha_command": aggregate["command_id"]}

    def aggregate_report(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        command = self.ssm.send_command(InstanceIds=[state["instance_id"]], DocumentName="AWS-RunShellScript", Parameters={"commands": [f"cat {state['staging_path']}/output/aggregate.json"]})
        command_id = command["Command"]["CommandId"]
        stop = time.monotonic() + 120
        value = None
        while time.monotonic() < stop:
            try:
                result = self.ssm.get_command_invocation(CommandId=command_id, InstanceId=state["instance_id"])
            except self.ssm.exceptions.InvocationDoesNotExist:
                time.sleep(2)
                continue
            if result["Status"] == "Success":
                try:
                    value = json.loads(result["StandardOutputContent"])
                except Exception as exc:
                    raise OperationRefusal("AGGREGATE_RECEIPT_MALFORMED", "aggregate is not JSON") from exc
                break
            if result["Status"] in {"Failed", "TimedOut", "Cancelled"}:
                raise OperationRefusal("AGGREGATE_RECEIPT_UNREADABLE", "aggregate read failed")
            time.sleep(2)
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
        return {"status": "PASS_AGGREGATE_REPORT" if value["status"] == "PASS_AGGREGATE" else "INCOMPLETE_MEASUREMENT", "aggregate_sha256": _sha(output), "runtime_rows": value["runtime_rows"], "completed_inferences": value["completed_inferences"], "not_applicable": value["not_applicable"], "gpu_memory": value["aggregate"]["gpu_memory"], "groups": len(value["aggregate"]["groups"])}

    def cleanup_and_expiry(self, context: AttemptContext) -> dict[str, Any]:
        state = self._state(context)
        errors = []
        try:
            self._update_kubeconfig(context)
            if state.get("namespace"):
                _run(["kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "delete", "namespace", NAMESPACE, "--ignore-not-found=true", "--wait=true", "--timeout=5m"], check=False, timeout=360)
            if state.get("dra_installed"):
                _run(["kubectl", "--kubeconfig", str(context.workdir / "kubeconfig"), "delete", "namespace", "nvidia-dra-driver", "--ignore-not-found=true", "--wait=true", "--timeout=5m"], check=False, timeout=360)
        except Exception as exc:
            errors.append(f"kubernetes:{type(exc).__name__}")
        if state.get("instance_id"):
            try:
                self._ssm(state["instance_id"], [f"sudo rm -rf /var/lib/medzen-asr-eval/attempt-{context.attempt}", "mountpoint -q /var/lib/medzen-asr-eval && sudo umount /var/lib/medzen-asr-eval || true"], timeout_seconds=180)
            except Exception as exc:
                errors.append(f"staging:{type(exc).__name__}")
        if state.get("volume_id"):
            try:
                try:
                    self.ec2.detach_volume(VolumeId=state["volume_id"], Force=False)
                    self.ec2.get_waiter("volume_available").wait(VolumeIds=[state["volume_id"]], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
                finally:
                    self.ec2.delete_volume(VolumeId=state["volume_id"])
            except Exception as exc:
                errors.append(f"volume:{type(exc).__name__}")
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
                    time.sleep(5)
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
                self.asg.delete_scheduled_action(AutoScalingGroupName=GPU_ASG, ScheduledActionName=state["deadline_action"])
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
