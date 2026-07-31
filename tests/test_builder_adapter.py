"""Behavioural tests for the bounded B4 image-builder lifecycle."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import budget
from pipeline.builder_adapter import (
    BuilderConfig, EC2Builder, build_run_id, render_user_data)


class Body:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class FakeS3:
    def __init__(self, git_sha):
        run = build_run_id(git_sha, "attempt-1")
        image = {
            "git_sha": git_sha,
            "adoptable": True,
            "digest": "sha256:" + "c" * 64,
            "pin": "example/medzen-trainer@sha256:" + "c" * 64,
            "scan": {"status": "COMPLETE", "unwaived_total": 0},
            "dependencies": {"runtime_versions": {"peft": "0.20.0"}},
        }
        self.objects = {
            f"candidates/build/{run}/exit_code": b"0\n",
            f"candidates/build/{run}/image.json":
                json.dumps(image).encode(),
        }

    def get_object(self, Bucket, Key):
        return {"Body": Body(self.objects[Key])}


class FakeEC2:
    def __init__(self):
        self.launches = []

    def run_instances(self, **kwargs):
        self.launches.append(kwargs)
        return {"Instances": [{
            "InstanceId": "i-builder",
            "LaunchTime": datetime.now(timezone.utc),
            "BlockDeviceMappings": [{
                "Ebs": {"VolumeId": "vol-builder"}}],
        }]}


class FakeECR:
    def describe_image_scan_findings(self, **kwargs):
        return {"imageScanStatus": {"status": "COMPLETE"}}


class Unused:
    pass


class FakeSession:
    def __init__(self, git_sha):
        self.s3 = FakeS3(git_sha)
        self.ec2 = FakeEC2()
        self.ecr = FakeECR()

    def client(self, name, region_name=None):
        return {
            "s3": self.s3, "ec2": self.ec2, "ecr": self.ecr,
            "sts": Unused(), "iam": Unused(),
        }[name]


def test_builder_user_data_is_closed_bounded_and_syntax_valid():
    git_sha, tar_sha = "a" * 40, "b" * 64
    run_id = build_run_id(git_sha, "attempt-1")
    text, digest = render_user_data(
        git_sha, tar_sha, run_id, watchdog_s=1800)
    assert len(digest) == 64
    assert f'GIT_SHA="{git_sha}"' in text
    assert f'TAR_SHA256="{tar_sha}"' in text
    assert f'BUILD_RUN_ID="{run_id}"' in text
    assert 'WATCHDOG="1800"' in text
    assert 'SCAN_MAX_CRITICAL="0"' in text
    assert 'SCAN_MAX_HIGH="0"' in text
    assert "shutdown -h now" in text


def test_builder_reconciles_only_after_termination_and_volume_deletion(
        monkeypatch):
    git_sha, tar_sha = "a" * 40, "b" * 64
    session = FakeSession(git_sha)
    builder = EC2Builder(
        session, BuilderConfig(poll_seconds=0, termination_grace_seconds=1))
    monkeypatch.setattr(builder, "_preflight", lambda *args: None)
    terminal = {
        "InstanceId": "i-builder",
        "State": {"Name": "terminated"},
        "BlockDeviceMappings": [{
            "Ebs": {"VolumeId": "vol-builder"}}],
    }
    monkeypatch.setattr(
        builder, "_wait_terminated",
        lambda instance_id, watchdog: (terminal, datetime.now(timezone.utc)))
    monkeypatch.setattr(builder, "_volume_deleted", lambda volume_id: True)
    calls = []
    monkeypatch.setattr(
        budget, "reserve",
        lambda cli, stage, attempt: calls.append(
            ("reserve", stage, attempt)) or {"reservation_id": "r"})
    monkeypatch.setattr(
        budget, "reconcile",
        lambda cli, stage, attempt, seconds, instance_id: calls.append(
            ("reconcile", stage, attempt, instance_id))
        or {"actual_usd": 0.01})

    result = builder.run(git_sha, tar_sha)
    assert calls[0] == ("reserve", "builder", "attempt-1")
    assert calls[-1] == (
        "reconcile", "builder", "attempt-1", "i-builder")
    assert result["aws_final_state"] == "terminated"
    assert result["root_volume_deleted"] is True
    assert result["image_digest"] == "sha256:" + "c" * 64
    assert result["eks_involved"] is False
    assert result["spot_involved"] is False
    launch = session.ec2.launches[0]
    assert launch["MinCount"] == launch["MaxCount"] == 1
    assert launch["InstanceType"] == "c6i.2xlarge"
    assert launch["IamInstanceProfile"]["Name"] == "medzen-builder-profile"
    assert launch["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert launch["MetadataOptions"]["HttpTokens"] == "required"
    assert launch["BlockDeviceMappings"][0]["Ebs"][
        "DeleteOnTermination"] is True
