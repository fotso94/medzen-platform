"""Direct-EC2 lifecycle adapter for one corrected-B4 stage.

One call means one reservation and one instance.  The instance produces a
container result and self-terminates.  This adapter then verifies AWS-observed
termination, proves the root volume was deleted, augments the result with that
lifecycle evidence, and publishes an immutable ``stage-result.json``.

No EKS, Spot, Auto Scaling group, launch template, or model registry is used.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import orchestrate, stage_descriptor


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "pipeline" / "stage_userdata.sh"
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class StageLaunchError(SystemExit):
    """The direct-EC2 boundary refused or failed."""


@dataclass(frozen=True)
class EC2StageConfig:
    region: str = "eu-central-1"
    bucket: str = "medzen-speech"
    account_id: str = "558069890522"
    ecr_repository: str = "medzen-trainer"
    ami_id: str = "ami-01b08a3e47b323a73"
    subnet_id: str = "subnet-00232b25bc1ac407a"
    security_group_id: str = "sg-0ec6a550611714d0c"
    instance_profile: str = "medzen-trainer-profile"
    instance_type: str = "g6.xlarge"
    root_device_name: str = "/dev/xvda"
    root_gb: int = 100
    poll_seconds: int = 15
    termination_grace_seconds: int = 600

    def image(self, digest: str) -> str:
        return (
            f"{self.account_id}.dkr.ecr.{self.region}.amazonaws.com/"
            f"{self.ecr_repository}@{digest}"
        )


def _utc(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).strftime(UTC_FORMAT)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _descriptor_bytes(value: dict) -> bytes:
    """Exactly the byte representation used by descriptor_hash()."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def render_user_data(descriptor: dict, cfg: EC2StageConfig) -> tuple[str, str]:
    """Render a closed template and refuse unresolved placeholders."""
    raw_descriptor = _descriptor_bytes(descriptor)
    descriptor_sha = _sha256(raw_descriptor)
    prefix = descriptor["output_prefix"].rstrip("/") + "/"
    values = {
        "__REGION__": cfg.region,
        "__BUCKET__": cfg.bucket,
        "__OUTPUT_PREFIX__": prefix,
        "__DESCRIPTOR_KEY__": prefix + "descriptor.json",
        "__DESCRIPTOR_SHA256__": descriptor_sha,
        "__IMAGE__": cfg.image(descriptor["image_digest"]),
        "__IMAGE_DIGEST__": descriptor["image_digest"],
        "__GIT_SHA__": descriptor["git_sha"],
        "__BUNDLE_TAR_SHA256__": descriptor["bundle_tar_sha256"],
        "__WATCHDOG_S__": str(descriptor["watchdog_s"]),
    }
    text = TEMPLATE.read_text()
    for key, value in values.items():
        if any(ch in str(value) for ch in ("\n", "\r", '"')):
            raise StageLaunchError(
                f"REFUSING: unsafe value for user-data placeholder {key}")
        text = text.replace(key, str(value))
    unresolved = [part for part in values if part in text]
    if unresolved or "__" in text:
        raise StageLaunchError(
            f"REFUSING: unresolved user-data placeholders {unresolved}")
    return text, hashlib.sha256(text.encode()).hexdigest()


class EC2StageAdapter:
    """Launch, monitor, read back, terminate, and prove cleanup."""

    def __init__(self, session: Any, config: EC2StageConfig | None = None):
        self.config = config or EC2StageConfig()
        self.session = session
        self.ec2 = session.client("ec2", region_name=self.config.region)
        self.ecr = session.client("ecr", region_name=self.config.region)
        self.s3 = session.client("s3", region_name=self.config.region)

    # Services consumed by pipeline.campaign.
    def run_base_and_preflight(self, descriptor: dict) -> dict:
        return self.run(descriptor)

    def run_sweep(self, descriptor: dict, lr: float) -> dict:
        if descriptor["lr"] != lr:
            raise StageLaunchError("REFUSING: sweep LR differs from descriptor")
        return self.run(descriptor)

    def run_final(self, descriptor: dict, lr: float) -> dict:
        if descriptor["lr"] != lr:
            raise StageLaunchError("REFUSING: final LR differs from descriptor")
        return self.run(descriptor)

    def _require_image(self, descriptor: dict) -> None:
        digest = descriptor["image_digest"]
        try:
            out = self.ecr.batch_get_image(
                repositoryName=self.config.ecr_repository,
                imageIds=[{"imageDigest": digest}],
                acceptedMediaTypes=[
                    "application/vnd.docker.distribution.manifest.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise StageLaunchError(
                f"REFUSING: cannot verify ECR digest {digest}: "
                f"{type(exc).__name__}") from exc
        images = out.get("images") or []
        if len(images) != 1 or images[0]["imageId"].get("imageDigest") != digest:
            raise StageLaunchError(
                f"REFUSING: ECR does not contain authorised digest {digest}")
        try:
            scan = self.ecr.describe_image_scan_findings(
                repositoryName=self.config.ecr_repository,
                imageId={"imageDigest": digest})
        except Exception as exc:  # noqa: BLE001
            raise StageLaunchError(
                f"REFUSING: cannot verify ECR scan for {digest}: "
                f"{type(exc).__name__}") from exc
        status = (scan.get("imageScanStatus") or {}).get("status")
        if status != "COMPLETE":
            raise StageLaunchError(
                f"REFUSING: ECR scan status is {status!r}, not COMPLETE")

    def _require_no_active_gpu(self) -> None:
        out = self.ec2.describe_instances(Filters=[
            {"Name": "instance-state-name",
             "Values": ["pending", "running", "stopping", "shutting-down"]},
            {"Name": "instance-type", "Values": [self.config.instance_type]},
            {"Name": "tag:Project", "Values": ["MedZen"]},
            {"Name": "tag:Phase", "Values": ["B4"]},
        ])
        active = [
            i["InstanceId"] for r in out.get("Reservations", [])
            for i in r.get("Instances", [])
        ]
        if active:
            raise StageLaunchError(
                f"REFUSING: active MedZen B4 GPU instance(s) {active}; stages "
                "are sequential and an orphan must be resolved first")

    def _require_empty_prefix(self, prefix: str) -> None:
        try:
            page = self.s3.list_objects_v2(
                Bucket=self.config.bucket, Prefix=prefix, MaxKeys=1)
        except Exception as exc:  # noqa: BLE001
            raise StageLaunchError(
                f"REFUSING: cannot establish whether {prefix} is empty "
                f"({type(exc).__name__}); an error is not absence") from exc
        if page.get("KeyCount", 0):
            raise StageLaunchError(
                f"REFUSING: s3://{self.config.bucket}/{prefix} is occupied; "
                "stage outputs are write-once")

    def _put_immutable(self, key: str, body: bytes,
                       content_type: str = "application/json") -> str:
        self.s3.put_object(
            Bucket=self.config.bucket, Key=key, Body=body,
            ContentType=content_type, IfNoneMatch="*",
            ServerSideEncryption="aws:kms")
        back = self.s3.get_object(
            Bucket=self.config.bucket, Key=key)["Body"].read()
        if back != body:
            raise StageLaunchError(
                f"REFUSING: readback differs for s3://"
                f"{self.config.bucket}/{key}")
        return _sha256(back)

    def _container_result(self, prefix: str) -> tuple[dict, int]:
        try:
            raw = self.s3.get_object(
                Bucket=self.config.bucket,
                Key=prefix + "container-result.json")["Body"].read()
            result = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            result = {
                "container_error":
                    f"container result absent or unreadable: "
                    f"{type(exc).__name__}"
            }
        try:
            raw_rc = self.s3.get_object(
                Bucket=self.config.bucket,
                Key=prefix + "container-exit-code")["Body"].read()
            rc = int(raw_rc.decode().strip())
        except Exception:  # noqa: BLE001
            rc = int(result.get("exit_status", 1))
        return result, rc

    def _wait_terminated(self, instance_id: str, deadline: float
                         ) -> tuple[dict, datetime]:
        last: dict = {}
        forced = False
        while True:
            out = self.ec2.describe_instances(InstanceIds=[instance_id])
            last = out["Reservations"][0]["Instances"][0]
            state = last["State"]["Name"]
            if state == "terminated":
                return last, datetime.now(timezone.utc)
            if time.monotonic() >= deadline and not forced:
                # The instance watchdog should win.  This is the independent
                # operator-side ceiling if cloud-init or Docker never started.
                self.ec2.terminate_instances(InstanceIds=[instance_id])
                forced = True
                deadline = time.monotonic() + self.config.termination_grace_seconds
            elif time.monotonic() >= deadline:
                raise StageLaunchError(
                    f"REFUSING: {instance_id} did not reach terminated after "
                    "the watchdog and operator termination request")
            time.sleep(self.config.poll_seconds)

    def _volume_deleted(self, volume_id: str | None) -> bool:
        if not volume_id:
            return False
        from botocore.exceptions import ClientError
        deadline = time.monotonic() + self.config.termination_grace_seconds
        while time.monotonic() < deadline:
            try:
                self.ec2.describe_volumes(VolumeIds=[volume_id])
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("InvalidVolume.NotFound", "InvalidVolumeID.NotFound"):
                    return True
                raise
            time.sleep(self.config.poll_seconds)
        return False

    def run(self, descriptor: dict) -> dict:
        # Validate before the first AWS mutation.
        stage_descriptor.build(**descriptor)
        self._require_image(descriptor)
        self._require_no_active_gpu()
        prefix = descriptor["output_prefix"].rstrip("/") + "/"
        self._require_empty_prefix(prefix)

        descriptor_body = _descriptor_bytes(descriptor)
        descriptor_sha = self._put_immutable(
            prefix + "descriptor.json", descriptor_body)
        if descriptor_sha != stage_descriptor.descriptor_hash(descriptor):
            raise StageLaunchError(
                "REFUSING: descriptor upload hash differs from canonical hash")
        user_data, user_data_sha = render_user_data(descriptor, self.config)

        tags = [
            {"Key": "Name",
             "Value": f"medzen-b4-{descriptor['stage']}-{descriptor['attempt']}"},
            {"Key": "Project", "Value": "MedZen"},
            {"Key": "Phase", "Value": "B4"},
            {"Key": "Campaign", "Value": descriptor["campaign_run"]},
            {"Key": "Attempt", "Value": str(descriptor["attempt"])},
            {"Key": "Stage", "Value": descriptor["stage"]},
            {"Key": "ManagedBy", "Value": "medzen-b4-campaign"},
            {"Key": "Promotable", "Value": "false"},
        ]
        launched = self.ec2.run_instances(
            ImageId=self.config.ami_id,
            InstanceType=self.config.instance_type,
            MinCount=1, MaxCount=1,
            SubnetId=self.config.subnet_id,
            SecurityGroupIds=[self.config.security_group_id],
            IamInstanceProfile={"Name": self.config.instance_profile},
            UserData=user_data,
            InstanceInitiatedShutdownBehavior="terminate",
            DisableApiTermination=False,
            MetadataOptions={
                "HttpTokens": "required",
                "HttpPutResponseHopLimit": 2,
                "HttpEndpoint": "enabled",
            },
            BlockDeviceMappings=[{
                "DeviceName": self.config.root_device_name,
                "Ebs": {
                    "VolumeSize": self.config.root_gb,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                    "Encrypted": True,
                },
            }],
            TagSpecifications=[
                {"ResourceType": "instance", "Tags": tags},
                {"ResourceType": "volume", "Tags": tags},
            ],
        )
        instances = launched.get("Instances") or []
        if len(instances) != 1:
            raise StageLaunchError(
                f"REFUSING: run_instances returned {len(instances)} instances")
        initial = instances[0]
        instance_id = initial["InstanceId"]
        launch_time = initial.get("LaunchTime") or datetime.now(timezone.utc)
        volume_id = None
        for mapping in initial.get("BlockDeviceMappings", []):
            if mapping.get("Ebs", {}).get("VolumeId"):
                volume_id = mapping["Ebs"]["VolumeId"]
                break

        deadline = (
            time.monotonic() + descriptor["watchdog_s"]
            + self.config.termination_grace_seconds)
        terminal, observed_terminated = self._wait_terminated(
            instance_id, deadline)
        if volume_id is None:
            for mapping in terminal.get("BlockDeviceMappings", []):
                volume_id = mapping.get("Ebs", {}).get("VolumeId") or volume_id
        deleted = self._volume_deleted(volume_id)
        actual_seconds = max(
            0.0, (observed_terminated - launch_time).total_seconds())
        container, rc = self._container_result(prefix)
        identity_problems = []
        expected_hash = stage_descriptor.descriptor_hash(descriptor)
        if container.get("stage_descriptor_sha256") != expected_hash:
            identity_problems.append(
                "container descriptor hash does not match the authorised "
                "descriptor")
        for field in ("campaign_run", "attempt", "stage"):
            if container.get(field) != descriptor[field]:
                identity_problems.append(
                    f"container {field}={container.get(field)!r}, expected "
                    f"{descriptor[field]!r}")
        if identity_problems:
            raise StageLaunchError(
                "REFUSING: terminated instance returned a result for another "
                "stage:\n  " + "\n  ".join(identity_problems))

        final = {
            **container,
            "instance_id": instance_id,
            "launched_utc": _utc(launch_time),
            "terminated_utc": _utc(observed_terminated),
            "actual_seconds": round(actual_seconds, 1),
            "exit_status": rc,
            "root_volume_id": volume_id,
            "root_volume_deleted": deleted,
            "aws_final_state": terminal["State"]["Name"],
            "user_data_sha256": user_data_sha,
            "instance_type": self.config.instance_type,
            "lifecycle": "on-demand-direct-ec2",
            "eks_involved": False,
            "spot_involved": False,
        }
        self._put_immutable(prefix + "stage-result.json", _json_bytes(final))
        return final
