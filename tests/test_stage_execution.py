"""Behavioural tests for the real B4 execution boundary."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import orchestrate, stage_descriptor
from pipeline.campaign_tracking import CampaignTracker
from pipeline.ec2_stage_adapter import (
    EC2StageAdapter, EC2StageConfig, StageLaunchError, render_user_data)
from pipeline.stage_runner import (
    _training_command, require_runtime_provenance, upload_tree)


def descriptor(stage="sweep", **over):
    is_base = stage == "base_and_preflight"
    values = {
        "campaign_run": "b4-test", "attempt": "1", "stage": stage,
        "git_sha": "a" * 40, "bundle_tar_sha256": "b" * 64,
        "image_digest": "sha256:" + "c" * 64,
        "policy_sha256": "d" * 64,
        "adoption_key":
            "curated/_versions/v2/ADOPTION-B4-CORRECTED.json",
        "dataset_fingerprint": "e" * 64,
        "base_manifest_sha256": "f" * 64,
        "validation_manifest_sha256": "0" * 64,
        "base_arm_key": None if is_base else "1" * 64,
        "base_artifact_key": (
            None if is_base
            else "candidates/evaluations/b4-test/base.json"),
        "base_artifact_sha256": None if is_base else "2" * 64,
        "generation_config_fingerprint": "3" * 64,
        "evaluator_sha256": "4" * 64,
        "lr": None if is_base else 1e-4,
        "seed": 0,
        "max_steps": 0 if is_base else 100,
        "checkpoint_steps": [],
        "reservation_id": "r1",
        "watchdog_s": 60,
        "input_prefix": "curated/_versions/v2/",
        "output_prefix": "candidates/evaluations/b4-test/attempt-1/"
                         + stage + "/",
        "mlflow_parent_run_id": "parent",
        "mlflow_child_run_id": "child",
        "purpose": "training_system_validation",
        "promotable": False,
    }
    values.update(over)
    return stage_descriptor.build(**values)


def test_user_data_runs_one_digest_pinned_direct_ec2_container():
    d = descriptor()
    text, digest = render_user_data(d, EC2StageConfig())
    assert len(digest) == 64
    assert "__" not in text
    assert "@sha256:" in text
    assert "--gpus all" in text
    assert "--read-only" in text
    assert "pipeline.stage_runner" in text
    assert "shutdown -h now" in text
    assert "eks" not in text.lower()
    assert "spot" not in text.lower()
    assert "--if-none-match '*'" in text
    assert "fileb://" not in text


def test_trainer_image_contains_runtime_governance_records():
    dockerfile = (ROOT / "pipeline/Dockerfile.trainer").read_text()
    assert "DQ-2026-003-policy-deferral-corrected.json" in dockerfile
    assert "VAL-2026-001-frozen-validation-sets.json" in dockerfile


@pytest.mark.parametrize("field,value", [
    ("git_sha", "G" * 40),
    ("image_digest", "sha256:" + "z" * 64),
    ("campaign_run", "../escape"),
    ("attempt", "1/other"),
    ("output_prefix", "candidates/evaluations/other/attempt-1/sweep/"),
])
def test_descriptor_refuses_malformed_identity_or_path_escape(field, value):
    values = descriptor()
    values[field] = value
    with pytest.raises(SystemExit, match="REFUSING"):
        stage_descriptor.build(**values)


def test_runtime_provenance_must_match_every_descriptor_pin(monkeypatch):
    d = descriptor()
    monkeypatch.setenv("MEDZEN_CODE_GIT_SHA", d["git_sha"])
    monkeypatch.setenv("MEDZEN_GIT_SHA", d["git_sha"])
    monkeypatch.setenv("MEDZEN_CODE_TAR_SHA256", d["bundle_tar_sha256"])
    monkeypatch.setenv("MEDZEN_IMAGE_DIGEST", d["image_digest"])
    require_runtime_provenance(d)
    monkeypatch.setenv("MEDZEN_IMAGE_DIGEST", "sha256:" + "9" * 64)
    with pytest.raises(SystemExit, match="provenance differs"):
        require_runtime_provenance(d)


def test_final_segment_keeps_600_step_schedule_while_pausing_at_300():
    d = descriptor(
        stage="final", max_steps=600,
        checkpoint_steps=[100, 200, 300, 400, 500, 600])
    cmd = _training_command(
        d, Path("/cache/final"), lr=3e-4, max_steps=600,
        stop_at_step=300, resume=Path("/cache/final/checkpoint-200"))
    assert cmd[cmd.index("--max-steps") + 1] == "600"
    assert cmd[cmd.index("--stop-at-step") + 1] == "300"
    assert cmd[cmd.index("--resume") + 1].endswith("checkpoint-200")


def test_saved_adapter_smoke_is_a_hard_gate():
    clean = orchestrate.evaluate_gates(
        {l: 0.5 for l in orchestrate.VALIDATION_LANGUAGES},
        {l: 0.6 for l in orchestrate.VALIDATION_LANGUAGES},
        {l: 1.0 for l in orchestrate.VALIDATION_LANGUAGES},
        {l: 0.0 for l in orchestrate.VALIDATION_LANGUAGES},
    )
    out = orchestrate.apply_checkpoint_controls(
        clean, {"passed": False, "reasons": ["adapter inert"]})
    assert not out["passed"]
    assert out["gates"]["saved_adapter_smoke"] is False
    assert "adapter inert" in " ".join(out["failures"])


def test_real_tiny_whisper_lora_collate_save_reload_and_generate(tmp_path):
    """The exact boundary the previous source-only task-type test missed."""
    torch = pytest.importorskip("torch")
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import (BatchEncoding, WhisperConfig,
                              WhisperForConditionalGeneration)

    from pipeline.generation import extract_sequence
    from pipeline.smoke import (adapter_effect_verdict,
                                lora_structure_verdict)
    from pipeline.train_asr import collate

    class TinyTokenizer:
        pad_token_id = 0

        def pad(self, features, return_tensors=None):
            sequences = [f["input_ids"] for f in features]
            width = max(map(len, sequences))
            ids = torch.tensor(
                [s + [0] * (width - len(s)) for s in sequences])
            mask = torch.tensor(
                [[1] * len(s) + [0] * (width - len(s))
                 for s in sequences])
            return BatchEncoding(
                {"input_ids": ids, "attention_mask": mask})

    class TinyProcessor:
        tokenizer = TinyTokenizer()

    cfg = WhisperConfig(
        vocab_size=32, num_mel_bins=8, d_model=16,
        encoder_layers=1, decoder_layers=1,
        encoder_attention_heads=2, decoder_attention_heads=2,
        encoder_ffn_dim=32, decoder_ffn_dim=32,
        max_source_positions=16, max_target_positions=16,
        pad_token_id=0, bos_token_id=2, eos_token_id=2,
        decoder_start_token_id=1,
    )
    torch.manual_seed(0)
    base = WhisperForConditionalGeneration(cfg)
    pristine = {k: v.detach().clone() for k, v in base.state_dict().items()}
    model = get_peft_model(
        base,
        LoraConfig(
            r=2, lora_alpha=4, target_modules=["q_proj", "v_proj"],
            task_type=None))
    batch = collate(TinyProcessor(), decoder_start_token_id=1)([{
        "input_features": torch.randn(8, 32),
        "labels": [1, 3, 4, 2],
    }])
    optimiser = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=0.1)
    first = model(**batch)
    assert torch.isfinite(first.loss)
    first.loss.backward()
    optimiser.step()

    with pytest.warns(
            UserWarning, match="vocabulary was not modified"):
        model.save_pretrained(tmp_path)
    saved = hashlib.sha256(
        (tmp_path / "adapter_model.safetensors").read_bytes()).hexdigest()
    fresh = WhisperForConditionalGeneration(cfg)
    fresh.load_state_dict(pristine)
    reloaded = PeftModel.from_pretrained(
        fresh, tmp_path, is_trainable=True)
    assert lora_structure_verdict(reloaded)["passed"]
    reloaded_hash = hashlib.sha256(
        (tmp_path / "adapter_model.safetensors").read_bytes()).hexdigest()
    with torch.no_grad():
        logits_on = reloaded(**batch).logits
        with reloaded.disable_adapter():
            logits_off = reloaded(**batch).logits
    norms = {
        n: float(p.detach().norm())
        for n, p in reloaded.named_parameters() if "lora_B" in n
    }
    effect = adapter_effect_verdict(
        logits_on, logits_off, norms,
        checkpoint_sha256=saved,
        tested_artifact_sha256=reloaded_hash)
    assert effect["passed"]

    generated = reloaded.generate(
        batch["input_features"], max_new_tokens=2,
        return_dict_in_generate=True, force_unique_generate_call=True)
    assert extract_sequence(generated)[0] == cfg.decoder_start_token_id


def test_real_mlflow_parent_child_structure(tmp_path):
    tracker = CampaignTracker(tmp_path / "mlflow.db", "camp", "7")
    child = tracker.start_stage("sweep-lr-1e-4", {
        "code_git_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "dataset_fingerprint": "c" * 64,
        "promotable": False,
    })
    tracker.finish_stage("sweep-lr-1e-4", {
        "instance_id": "i-123",
        "root_volume_deleted": True,
        "stage_descriptor_sha256": "d" * 64,
        "actual_seconds": 12,
        "steps_completed": 100,
        "wer": {"acholi": 0.5},
    })
    tracker.finish_parent(True, "ok")
    run = tracker.client.get_run(child)
    assert run.data.tags["mlflow.parentRunId"] == tracker.parent_run_id
    assert run.data.tags["purpose"] == "training_system_validation"
    assert run.data.tags["promotable"] == "false"
    assert run.data.params["code_git_sha"] == "a" * 40
    assert run.data.metrics["val_wer_acholi"] == 0.5
    assert tracker.client.search_registered_models() == []


class Body:
    def __init__(self, value):
        self.value = value

    def read(self, amount=None):
        if amount is None:
            value, self.value = self.value, b""
            return value
        value, self.value = self.value[:amount], self.value[amount:]
        return value


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if MaxKeys is not None:
            keys = keys[:MaxKeys]
        return {"KeyCount": len(keys),
                "Contents": [{"Key": k} for k in keys]}

    def put_object(self, Bucket, Key, Body, **kwargs):
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise AssertionError("overwrite")
        self.objects[Key] = Body.read() if hasattr(Body, "read") else Body
        self.puts.append((Key, kwargs))
        return {}

    def get_object(self, Bucket, Key):
        return {"Body": Body(self.objects[Key])}


def test_artifact_tree_uses_conditional_writes_and_sha256_readback(tmp_path):
    (tmp_path / "adapter.safetensors").write_bytes(b"adapter-bytes")
    cli = FakeS3()
    manifest = upload_tree(cli, tmp_path, "candidates/test/artifact")
    assert manifest["files"]["adapter.safetensors"]["sha256"] == \
        hashlib.sha256(b"adapter-bytes").hexdigest()
    upload = next(
        kwargs for key, kwargs in cli.puts
        if key.endswith("/adapter.safetensors"))
    assert upload["IfNoneMatch"] == "*"
    assert upload["ChecksumSHA256"]
    assert upload["ServerSideEncryption"] == "aws:kms"


class FakeECR:
    def batch_get_image(self, repositoryName, imageIds, **kwargs):
        return {"images": [{"imageId": {
            "imageDigest": imageIds[0]["imageDigest"]}}]}

    def describe_image_scan_findings(self, **kwargs):
        return {"imageScanStatus": {"status": "COMPLETE"}}

    def describe_images(self, repositoryName, imageIds):
        return {"imageDetails": [{
            "imageDigest": imageIds[0]["imageDigest"],
            "imageTags": ["a" * 40],
        }]}


class FakeEC2:
    def __init__(self, s3, tamper=False, active=False):
        self.s3 = s3
        self.tamper = tamper
        self.active = active
        self.launched = []

    def describe_instances(self, Filters=None, InstanceIds=None):
        if Filters is not None:
            active = [{
                "InstanceId": "i-orphan"
            }] if self.active else []
            return {"Reservations": [{"Instances": active}] if active else []}
        return {"Reservations": [{"Instances": [{
            "InstanceId": InstanceIds[0],
            "State": {"Name": "terminated"},
            "BlockDeviceMappings": [{
                "Ebs": {"VolumeId": "vol-1"}}],
        }]}]}

    def describe_images(self, ImageIds):
        return {"Images": [{
            "ImageId": ImageIds[0], "State": "available",
            "Architecture": "x86_64", "OwnerId": "898082745236",
        }]}

    def describe_subnets(self, SubnetIds):
        return {"Subnets": [{
            "SubnetId": SubnetIds[0], "VpcId": "vpc-1",
            "AvailabilityZone": "eu-central-1a",
        }]}

    def describe_security_groups(self, GroupIds):
        return {"SecurityGroups": [{
            "GroupId": GroupIds[0], "VpcId": "vpc-1",
        }]}

    def run_instances(self, **kwargs):
        self.launched.append(kwargs)
        descriptor_key = next(
            k for k in self.s3.objects if k.endswith("/descriptor.json"))
        d = json.loads(self.s3.objects[descriptor_key])
        prefix = descriptor_key.rsplit("/", 1)[0] + "/"
        result = {
            "stage_descriptor_sha256":
                ("9" * 64 if self.tamper
                 else stage_descriptor.descriptor_hash(d)),
            "campaign_run": d["campaign_run"],
            "attempt": d["attempt"],
            "stage": d["stage"],
            "wer": {l: 0.5 for l in orchestrate.VALIDATION_LANGUAGES},
            "eos_rate": {l: 1.0 for l in orchestrate.VALIDATION_LANGUAGES},
            "cap_hit_rate": {l: 0.0
                             for l in orchestrate.VALIDATION_LANGUAGES},
            "artifact_sha256": "5" * 64,
            "smoke": {"passed": True, "reasons": []},
        }
        self.s3.objects[prefix + "container-result.json"] = (
            json.dumps(result).encode())
        self.s3.objects[prefix + "container-exit-code"] = b"0\n"
        return {"Instances": [{
            "InstanceId": "i-stage",
            "LaunchTime": datetime.now(timezone.utc),
        }]}

    def describe_volumes(self, VolumeIds):
        from botocore.exceptions import ClientError
        raise ClientError(
            {"Error": {"Code": "InvalidVolume.NotFound"}},
            "DescribeVolumes")

    def terminate_instances(self, InstanceIds):
        raise AssertionError("watchdog fallback was not expected")


class FakeSession:
    def __init__(self, tamper=False, active=False):
        self.s3 = FakeS3()
        self.ecr = FakeECR()
        self.ec2 = FakeEC2(self.s3, tamper=tamper, active=active)
        self.sts = type("STS", (), {
            "get_caller_identity": lambda self: {
                "Account": "558069890522"}})()
        self.iam = type("IAM", (), {
            "get_instance_profile": lambda self, **kw: {
                "InstanceProfile": {"Roles": [{"RoleName": "trainer"}]}}})()

    def client(self, name, region_name=None):
        return {
            "s3": self.s3, "ecr": self.ecr, "ec2": self.ec2,
            "sts": self.sts, "iam": self.iam,
        }[name]


def test_ec2_adapter_observes_termination_and_volume_deletion():
    session = FakeSession()
    cfg = EC2StageConfig(poll_seconds=0, termination_grace_seconds=1)
    result = EC2StageAdapter(session, cfg).run(descriptor())
    assert result["instance_id"] == "i-stage"
    assert result["aws_final_state"] == "terminated"
    assert result["root_volume_id"] == "vol-1"
    assert result["root_volume_deleted"] is True
    assert result["lifecycle"] == "on-demand-direct-ec2"
    assert result["eks_involved"] is False and result["spot_involved"] is False
    stage_descriptor.verify_result(descriptor(), result)
    assert any(k.endswith("/stage-result.json")
               for k in session.s3.objects)
    launch = session.ec2.launched[0]
    assert launch["MinCount"] == launch["MaxCount"] == 1
    assert len(launch["ClientToken"]) == 64
    assert launch["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert launch["MetadataOptions"]["HttpTokens"] == "required"
    assert launch["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] is True
    assert launch["BlockDeviceMappings"][0]["DeviceName"] == "/dev/xvda"


def test_direct_ec2_preflight_verifies_infrastructure_without_mutation():
    session = FakeSession()
    d = descriptor()
    result = EC2StageAdapter(session).preflight_campaign(
        d["git_sha"], d["image_digest"])
    assert result["active_b4_instances"] == 0
    assert result["availability_zone"] == "eu-central-1a"
    assert result["eks_involved"] is False
    assert session.s3.objects == {}
    assert session.ec2.launched == []


def test_ec2_adapter_returns_terminated_lifecycle_for_semantic_reconciliation():
    session = FakeSession(tamper=True)
    d = descriptor()
    result = EC2StageAdapter(
        session,
        EC2StageConfig(poll_seconds=0, termination_grace_seconds=1),
    ).run(d)
    assert result["aws_final_state"] == "terminated"
    assert result["identity_problems"]
    with pytest.raises(SystemExit, match="instance ran something else"):
        stage_descriptor.verify_result(d, result)


def test_orphan_gpu_refuses_before_any_s3_mutation():
    session = FakeSession(active=True)
    with pytest.raises(StageLaunchError, match="active MedZen B4 instance"):
        EC2StageAdapter(session).run(descriptor())
    assert session.s3.objects == {}
    assert session.ec2.launched == []
