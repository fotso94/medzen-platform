"""Round 13 (Codex): the ADMISSION side of the promotion gate — the
complete sealed-run contract, the binding of row files to the sealed
job's own immutable output objects, and the baked scorer registry."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/model-loader"))
sys.path.insert(0, str(ROOT / "tests"))

from medzen_model_loader.promotion_check import (  # noqa: E402
    CONTRACT_FIELDS, PromotionCheckRefusal, validate_sealed_run_contract,
    verify_packet_chronology, verify_sealed_outputs)
from medzen_model_loader.loader_v2 import (  # noqa: E402
    describe_sealed_job_contract)
import test_b6v2_real_provider as rp  # noqa: E402
from test_b6v2_real_provider import (  # noqa: E402
    SEALED_OUTPUT_STORE, SEALED_RUN_CONTRACT, _promotion_bundle,
    _stub_output_fetch, _stub_output_writer)

ARM1_DESCRIBE = ROOT / (
    "platform/evidence/receipts/ARM1-B5-2026-005-COMPLETION/"
    "describe-training-job.json")


def _described_from_contract(contract, *, creation="2026-08-23T12:00:00Z",
                             end="2026-08-23T13:00:00Z", **over):
    """What a faithful sealed_start_fetch returns for a job that EQUALS
    the predeclared contract."""
    described = {k: v for k, v in contract.items()
                 if k not in ("job_name", "account_id", "region")}
    described.update({
        "creation_utc": creation, "end_utc": end, "status": "Completed",
        "arn": (f"arn:aws:sagemaker:{contract['region']}:"
                f"{contract['account_id']}:training-job/"
                f"{contract['job_name']}"),
        "output_artifact_s3_uri": None})
    described.update(over)
    return described


def _admission_material(tmp_path, **bundle_over):
    from medzen_model_loader.loader_v2 import artifact_tree_sha256
    tree = artifact_tree_sha256("ab" * 32, "12" * 32)
    bundle, _pin = _promotion_bundle(tmp_path, tree, **bundle_over)
    report = json.loads((bundle / "T6-GATE-REPORT.json").read_bytes())
    packet_bytes = (bundle / "CANDIDATE-PACKET.json").read_bytes()
    packet = json.loads(packet_bytes)
    envelope = json.loads((bundle / "ANCHOR-ENVELOPE.json").read_bytes())

    def rows_bytes(label):
        path = bundle / f"{label}.rows.jsonl"
        return path.read_bytes() if path.is_file() else None
    return tree, report, packet, packet_bytes, envelope, rows_bytes


def _run_admission(tmp_path, *, described_over=None, store_mutate=None,
                   packet_over=None, report_mutate=None):
    tree, report, packet, packet_bytes, envelope, rows_bytes = (
        _admission_material(tmp_path, packet_over=packet_over))
    if report_mutate:
        report_mutate(report)
    contract = packet["sealed_run"]
    described = _described_from_contract(contract, **(described_over or {}))
    if store_mutate:
        store_mutate(SEALED_OUTPUT_STORE)
    return verify_packet_chronology(
        report, anchor_envelope=envelope, packet_bytes=packet_bytes,
        candidate_packet=packet,
        anchor_fetch=lambda storage: (packet_bytes, "2026-08-23T10:00:00Z"),
        sealed_start_fetch=lambda job: described,
        artifact_tree_sha256=tree, rows_bytes=rows_bytes,
        output_object_fetch=_stub_output_fetch,
        output_writer_fetch=_stub_output_writer)


def test_admission_path_passes_and_attests_the_verified_identities(tmp_path):
    receipt = _run_admission(tmp_path)
    assert receipt["sealed_job"]["status"] == "Completed"
    assert receipt["sealed_job"]["end_utc"] == "2026-08-23T13:00:00Z"
    for field in CONTRACT_FIELDS:
        if field not in ("job_name", "account_id", "region"):
            assert field in receipt["sealed_job"], field
    assert set(receipt["sealed_outputs"]["rows"]) >= {"english", "code_switch"}
    assert receipt["sealed_outputs"]["decoding_config_sha256"] == "dc" * 32


@pytest.mark.parametrize("field,value", [
    ("environment_sha256", "99" * 32),
    ("vpc_config", {"security_group_ids": ["sg-other"],
                    "subnets": ["subnet-00232b25bc1ac407a"]}),
    ("max_runtime_seconds", 999999),
    ("instance_count", 2),
    ("volume_size_gb", 30),
    ("checkpoint_config", "s3://somewhere/ckpt/"),
    ("network_isolation", False),
    ("channels", dict(SEALED_RUN_CONTRACT["channels"],
                      **{"sealed-one": dict(
                          SEALED_RUN_CONTRACT["channels"]["sealed-one"],
                          input_mode="Pipe")})),
])
def test_every_described_dimension_that_drifts_refuses(tmp_path, field, value):
    """Codex round 13 reproduced acceptance after changing environment,
    VPC, runtime limits, instance count, volume size and checkpoint
    configuration. Each now refuses."""
    with pytest.raises(PromotionCheckRefusal, match=f"sealed job {field}"):
        _run_admission(tmp_path, described_over={field: value})


@pytest.mark.parametrize("image", [
    "repo@sha256:x",
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-sealed-eval:latest",
    "medzen-sealed-eval@sha256:" + "8e" * 32,
    ("558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-sealed-eval"
     "@sha256:" + "8e" * 31),
])
def test_malformed_or_mutable_image_references_refuse(image):
    contract = dict(SEALED_RUN_CONTRACT, image_digest=image)
    with pytest.raises(PromotionCheckRefusal, match="IMMUTABLE ECR"):
        validate_sealed_run_contract(contract)


def test_predeclared_network_isolation_false_refuses():
    contract = dict(SEALED_RUN_CONTRACT, network_isolation=False)
    with pytest.raises(PromotionCheckRefusal, match="REQUIRE network isolation"):
        validate_sealed_run_contract(contract)


def test_channel_without_full_semantics_refuses():
    contract = dict(SEALED_RUN_CONTRACT,
                    channels={"sealed-one": "s3://medzen-speech/eval/sealed/one"})
    with pytest.raises(PromotionCheckRefusal, match="a URI alone is not an input contract"):
        validate_sealed_run_contract(contract)


def test_rows_written_outside_the_job_window_refuse(tmp_path):
    def late(store):
        for key, (body, _) in list(store.items()):
            if key[0].endswith("english.rows.jsonl"):
                store[key] = (body, "2026-08-23T13:00:01Z")
    with pytest.raises(PromotionCheckRefusal, match="outside the sealed job"):
        _run_admission(tmp_path, store_mutate=late)


def test_rows_not_under_the_job_output_prefix_refuse(tmp_path):
    def elsewhere(report):
        report["sealed_outputs"]["rows"]["english"]["s3_uri"] = (
            "s3://medzen-speech/other/english.rows.jsonl")
    with pytest.raises(PromotionCheckRefusal, match="not under the sealed job"):
        _run_admission(tmp_path, report_mutate=elsewhere)


def test_bundled_rows_that_differ_from_the_job_output_refuse(tmp_path):
    """A fabricated but internally consistent prediction file is not the
    job's output object — it refuses even though every statistic
    recomputes from it."""
    def swap(store):
        for key, (body, modified) in list(store.items()):
            if key[0].endswith("english.rows.jsonl"):
                store[key] = (body + b'{"extra":1}\n', modified)
    with pytest.raises(PromotionCheckRefusal, match="do not hash to the declared"):
        _run_admission(tmp_path, store_mutate=swap)


def test_inference_receipt_for_another_artifact_refuses(tmp_path):
    """A receipt naming a DIFFERENT artifact refuses at the artifact-tree
    check (the provenance/hash checks pass first)."""
    tree, report, packet, packet_bytes, envelope, rows_bytes = (
        _admission_material(tmp_path))
    ref = report["sealed_outputs"]["inference_receipt"]
    body, modified = SEALED_OUTPUT_STORE[(ref["s3_uri"], ref["version_id"])]
    doc = json.loads(body)
    doc["artifact_tree_sha256"] = "ee" * 32
    new_body = json.dumps(doc, sort_keys=True).encode()
    SEALED_OUTPUT_STORE[(ref["s3_uri"], ref["version_id"])] = (new_body, modified)
    ref["sha256"] = hashlib.sha256(new_body).hexdigest()
    described = _described_from_contract(packet["sealed_run"])
    with pytest.raises(PromotionCheckRefusal, match="did not attest to running THIS"):
        verify_packet_chronology(
            report, anchor_envelope=envelope, packet_bytes=packet_bytes,
            candidate_packet=packet,
            anchor_fetch=lambda s: (packet_bytes, "2026-08-23T10:00:00Z"),
            sealed_start_fetch=lambda job: described,
            artifact_tree_sha256=tree, rows_bytes=rows_bytes,
            output_object_fetch=_stub_output_fetch,
            output_writer_fetch=_stub_output_writer)


def test_output_written_by_a_non_execution_role_refuses(tmp_path, monkeypatch):
    """Codex review #15 finding 1: a bare S3 writer fabricates internally-
    consistent outputs but CANNOT write as the sealed execution role —
    the CloudTrail PutObject principal check refuses."""
    monkeypatch.setattr(rp, "SEALED_WRITER_PRINCIPAL",
                        "arn:aws:sts::558069890522:assumed-role/some-other-role/x")
    with pytest.raises(PromotionCheckRefusal, match="not the predeclared sealed execution role"):
        _run_admission(tmp_path)


def test_output_without_object_lock_refuses(tmp_path, monkeypatch):
    """An output object with no Object-Lock retention is not tamper-evident."""
    monkeypatch.setattr(rp, "SEALED_OBJECT_LOCK",
                        {"object_lock_mode": None, "object_lock_retain_until": None})
    with pytest.raises(PromotionCheckRefusal, match="no Object-Lock retention"):
        _run_admission(tmp_path)


def test_missing_decoding_config_refuses(tmp_path):
    with pytest.raises(PromotionCheckRefusal, match="decoding_config_sha256"):
        _run_admission(tmp_path, packet_over={"decoding_config_sha256": ""})


def test_real_arm1_describe_projects_to_a_complete_contract():
    """The contract reducer applied to the REAL arm-1 DescribeTrainingJob
    (the committed completion receipt) yields every contract dimension
    with the values the launch packet predeclared."""
    described = json.loads(ARM1_DESCRIBE.read_bytes())
    for key in ("CreationTime", "TrainingEndTime", "TrainingStartTime",
                "LastModifiedTime"):
        described[key] = datetime.fromisoformat(described[key])
    contract = describe_sealed_job_contract(described, kind="sagemaker_training")
    packet = json.loads((ROOT / "platform/manifests/"
                         "B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-005.json"
                         ).read_bytes())
    assert contract["image_digest"] == packet["image_uri_with_digest"]
    assert contract["instance_type"] == packet["instance_type"]
    assert contract["instance_count"] == 1
    assert contract["volume_size_gb"] == packet["volume_gb"]
    assert contract["max_runtime_seconds"] == packet["max_runtime_seconds"]
    assert contract["vpc_config"] == {
        "security_group_ids": sorted(packet["security_group_ids"]),
        "subnets": sorted(packet["subnets"])}
    assert contract["output_kms_key_arn"] == packet["kms_key_arn"]
    assert contract["status"] == "Completed"
    assert contract["creation_utc"] < contract["end_utc"]
    assert contract["end_utc"] == "2026-08-23T13:22:15Z"
    env_sha = hashlib.sha256(json.dumps(
        {str(k): str(v) for k, v in packet["environment"].items()},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert contract["environment_sha256"] == env_sha
    for name, spec in contract["channels"].items():
        assert set(spec) == {"s3_uri", "s3_data_type",
                             "s3_data_distribution_type", "content_type",
                             "compression_type", "input_mode"}
        assert spec["s3_uri"].startswith("s3://medzen-speech/")
    assert contract["output_artifact_s3_uri"].endswith("model.tar.gz")
    for field in CONTRACT_FIELDS:
        if field not in ("job_name", "account_id", "region"):
            assert field in contract, field
