"""Bounded direct-EC2 image builder for the corrected B4 campaign.

The builder is deliberately separate from the GPU stage adapter. It runs on a
public Amazon Linux 2023 x86_64 AMI, verifies a commit-scoped bundle before
executing it, pushes one immutable ECR tag, scans it, self-terminates, and
returns only after AWS confirms the root volume is deleted.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline import budget


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "pipeline" / "builder_userdata.sh"
BUCKET = "medzen-speech"


class BuilderError(SystemExit):
    """The builder boundary refused or the build failed."""


@dataclass(frozen=True)
class BuilderConfig:
    region: str = "eu-central-1"
    account_id: str = "558069890522"
    repository: str = "medzen-trainer"
    ami_id: str = "ami-0352a6b853b4367b3"
    ami_owner: str = "137112412989"
    subnet_id: str = "subnet-00232b25bc1ac407a"
    security_group_id: str = "sg-0ec6a550611714d0c"
    instance_profile: str = "medzen-trainer-profile"
    instance_type: str = "c6i.2xlarge"
    root_device_name: str = "/dev/xvda"
    root_gb: int = 50
    poll_seconds: int = 15
    termination_grace_seconds: int = 600


def build_run_id(git_sha: str, attempt: str) -> str:
    safe = attempt.replace("_", "-")
    if not safe or any(c not in "0123456789abcdefghijklmnopqrstuvwxyz-" for c in safe):
        raise BuilderError("REFUSING: builder attempt must be lowercase path-safe")
    return f"b4-image-{git_sha[:12]}-{safe}"


def render_user_data(git_sha: str, tar_sha256: str, run_id: str,
                     watchdog_s: int = 1800) -> tuple[str, str]:
    values = {
        "${GIT_SHA}": git_sha,
        "${TAR_SHA256}": tar_sha256,
        "${BUILD_RUN_ID}": run_id,
        "${SCAN_MAX_CRITICAL}": "0",
        "${SCAN_MAX_HIGH}": "0",
        "${WATCHDOG}": str(watchdog_s),
    }
    text = TEMPLATE.read_text()
    for marker, value in values.items():
        text = text.replace(marker, value)
    unresolved = [m for m in values if m in text]
    if unresolved:
        raise BuilderError(
            f"REFUSING: unresolved builder user-data values {unresolved}")
    check = subprocess.run(
        ["bash", "-n"], input=text, text=True, capture_output=True)
    if check.returncode:
        raise BuilderError(
            f"REFUSING: rendered builder user-data is invalid: {check.stderr}")
    return text, hashlib.sha256(text.encode()).hexdigest()


class EC2Builder:
    def __init__(self, session: Any, config: BuilderConfig | None = None):
        self.cfg = config or BuilderConfig()
        self.ec2 = session.client("ec2", region_name=self.cfg.region)
        self.ecr = session.client("ecr", region_name=self.cfg.region)
        self.s3 = session.client("s3", region_name=self.cfg.region)
        self.sts = session.client("sts", region_name=self.cfg.region)
        self.iam = session.client("iam", region_name=self.cfg.region)

    def _preflight(self, git_sha: str, tar_sha256: str, run_id: str) -> None:
        ident = self.sts.get_caller_identity()
        if ident.get("Account") != self.cfg.account_id:
            raise BuilderError(
                f"REFUSING: caller account {ident.get('Account')} is not "
                f"{self.cfg.account_id}")
        image = self.ec2.describe_images(
            ImageIds=[self.cfg.ami_id])["Images"]
        if len(image) != 1:
            raise BuilderError("REFUSING: builder AMI is absent")
        ami = image[0]
        if (ami.get("State"), ami.get("Architecture"), ami.get("OwnerId"),
                ami.get("RootDeviceName")) != (
                "available", "x86_64", self.cfg.ami_owner,
                self.cfg.root_device_name):
            raise BuilderError(
                "REFUSING: builder AMI provenance or architecture changed")
        subnet = self.ec2.describe_subnets(
            SubnetIds=[self.cfg.subnet_id])["Subnets"][0]
        groups = self.ec2.describe_security_groups(
            GroupIds=[self.cfg.security_group_id])["SecurityGroups"]
        if len(groups) != 1 or groups[0]["VpcId"] != subnet["VpcId"]:
            raise BuilderError("REFUSING: builder subnet/SG VPC mismatch")
        self.iam.get_instance_profile(
            InstanceProfileName=self.cfg.instance_profile)

        active = self.ec2.describe_instances(Filters=[
            {"Name": "instance-state-name",
             "Values": ["pending", "running", "stopping", "shutting-down"]},
            {"Name": "instance-type", "Values": [self.cfg.instance_type]},
            {"Name": "tag:Project", "Values": ["MedZen"]},
            {"Name": "tag:Phase", "Values": ["B4"]},
            {"Name": "tag:Stage", "Values": ["builder"]},
        ])
        if any(r.get("Instances") for r in active.get("Reservations", [])):
            raise BuilderError("REFUSING: an active MedZen B4 builder exists")

        found = self.ecr.batch_get_image(
            repositoryName=self.cfg.repository,
            imageIds=[{"imageTag": git_sha}]).get("images") or []
        if found:
            raise BuilderError(
                f"REFUSING: immutable ECR tag {git_sha} already exists")

        prefix = f"candidates/build/{run_id}/"
        page = self.s3.list_objects_v2(
            Bucket=BUCKET, Prefix=prefix, MaxKeys=1)
        if page.get("KeyCount", 0):
            raise BuilderError(
                f"REFUSING: s3://{BUCKET}/{prefix} is occupied")

        bundle = json.loads(self.s3.get_object(
            Bucket=BUCKET,
            Key=f"candidates/bootstrap/{git_sha}/BUNDLE.json")["Body"].read())
        if (bundle.get("git_sha"), bundle.get("tar_sha256")) != (
                git_sha, tar_sha256):
            raise BuilderError(
                "REFUSING: published bundle does not match authorised pins")

    def _wait_terminated(self, instance_id: str, watchdog_s: int
                         ) -> tuple[dict, datetime]:
        deadline = time.monotonic() + watchdog_s + self.cfg.termination_grace_seconds
        forced = False
        while True:
            out = self.ec2.describe_instances(InstanceIds=[instance_id])
            instance = out["Reservations"][0]["Instances"][0]
            if instance["State"]["Name"] == "terminated":
                return instance, datetime.now(timezone.utc)
            if time.monotonic() >= deadline and not forced:
                self.ec2.terminate_instances(InstanceIds=[instance_id])
                forced = True
                deadline = time.monotonic() + self.cfg.termination_grace_seconds
            elif time.monotonic() >= deadline:
                raise BuilderError(
                    f"REFUSING: builder {instance_id} did not terminate")
            time.sleep(self.cfg.poll_seconds)

    def _volume_deleted(self, volume_id: str | None) -> bool:
        if not volume_id:
            return False
        from botocore.exceptions import ClientError
        deadline = time.monotonic() + self.cfg.termination_grace_seconds
        while time.monotonic() < deadline:
            try:
                self.ec2.describe_volumes(VolumeIds=[volume_id])
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in (
                        "InvalidVolume.NotFound", "InvalidVolumeID.NotFound"):
                    return True
                raise
            time.sleep(self.cfg.poll_seconds)
        return False

    def run(self, git_sha: str, tar_sha256: str,
            attempt: str = "attempt-1") -> dict:
        for label, value, size in (
                ("git sha", git_sha, 40), ("tar sha256", tar_sha256, 64)):
            if len(value) != size or any(c not in "0123456789abcdef"
                                         for c in value):
                raise BuilderError(f"REFUSING: malformed {label}")
        run_id = build_run_id(git_sha, attempt)
        watchdog_s = budget.WATCHDOG_S["builder"]
        user_data, user_data_sha = render_user_data(
            git_sha, tar_sha256, run_id, watchdog_s)
        self._preflight(git_sha, tar_sha256, run_id)
        reservation = budget.reserve(self.s3, "builder", attempt)

        tags = [
            {"Key": "Name", "Value": f"medzen-{run_id}"},
            {"Key": "Project", "Value": "MedZen"},
            {"Key": "Phase", "Value": "B4"},
            {"Key": "Stage", "Value": "builder"},
            {"Key": "BuildRun", "Value": run_id},
            {"Key": "ManagedBy", "Value": "medzen-b4-campaign"},
            {"Key": "Promotable", "Value": "false"},
        ]
        token = hashlib.sha256(
            f"medzen/{run_id}/{git_sha}".encode()).hexdigest()
        launched = self.ec2.run_instances(
            ImageId=self.cfg.ami_id,
            InstanceType=self.cfg.instance_type,
            MinCount=1, MaxCount=1,
            ClientToken=token,
            SubnetId=self.cfg.subnet_id,
            SecurityGroupIds=[self.cfg.security_group_id],
            IamInstanceProfile={"Name": self.cfg.instance_profile},
            UserData=user_data,
            InstanceInitiatedShutdownBehavior="terminate",
            DisableApiTermination=False,
            MetadataOptions={
                "HttpTokens": "required",
                "HttpPutResponseHopLimit": 1,
                "HttpEndpoint": "enabled",
            },
            BlockDeviceMappings=[{
                "DeviceName": self.cfg.root_device_name,
                "Ebs": {
                    "VolumeSize": self.cfg.root_gb,
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
            raise BuilderError(
                f"REFUSING: run_instances returned {len(instances)} builders")
        initial = instances[0]
        instance_id = initial["InstanceId"]
        launched_at = initial.get("LaunchTime") or datetime.now(timezone.utc)
        volume_id = next((
            m.get("Ebs", {}).get("VolumeId")
            for m in initial.get("BlockDeviceMappings", [])
            if m.get("Ebs", {}).get("VolumeId")), None)
        terminal, terminated_at = self._wait_terminated(
            instance_id, watchdog_s)
        if volume_id is None:
            volume_id = next((
                m.get("Ebs", {}).get("VolumeId")
                for m in terminal.get("BlockDeviceMappings", [])
                if m.get("Ebs", {}).get("VolumeId")), None)
        deleted = self._volume_deleted(volume_id)
        seconds = max(0.0, (terminated_at - launched_at).total_seconds())
        reconciled = budget.reconcile(
            self.s3, "builder", attempt, seconds, instance_id)
        if not deleted:
            raise BuilderError(
                f"builder {instance_id} terminated but root volume "
                f"{volume_id!r} was not proved deleted")

        prefix = f"candidates/build/{run_id}/"
        try:
            rc = int(self.s3.get_object(
                Bucket=BUCKET, Key=prefix + "exit_code")["Body"].read())
            image = json.loads(self.s3.get_object(
                Bucket=BUCKET, Key=prefix + "image.json")["Body"].read())
        except Exception as exc:  # noqa: BLE001
            raise BuilderError(
                f"builder evidence is absent or invalid: "
                f"{type(exc).__name__}") from exc
        if rc != 0 or image.get("git_sha") != git_sha \
                or image.get("adoptable") is not True:
            raise BuilderError(
                f"builder failed closed: exit={rc}, "
                f"git={image.get('git_sha')}, "
                f"adoptable={image.get('adoptable')}")
        digest = image.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise BuilderError("builder returned no valid image digest")
        scan = self.ecr.describe_image_scan_findings(
            repositoryName=self.cfg.repository,
            imageId={"imageDigest": digest})
        if (scan.get("imageScanStatus") or {}).get("status") != "COMPLETE":
            raise BuilderError("ECR scan is not COMPLETE after build")
        return {
            "build_run_id": run_id,
            "instance_id": instance_id,
            "root_volume_id": volume_id,
            "root_volume_deleted": deleted,
            "aws_final_state": terminal["State"]["Name"],
            "actual_seconds": round(seconds, 1),
            "actual_usd": reconciled["actual_usd"],
            "git_sha": git_sha,
            "tar_sha256": tar_sha256,
            "image_digest": digest,
            "image_pin": image["pin"],
            "scan": image["scan"],
            "dependencies": image["dependencies"],
            "user_data_sha256": user_data_sha,
            "eks_involved": False,
            "spot_involved": False,
        }
