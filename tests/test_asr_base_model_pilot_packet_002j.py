from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_aws_read_fixtures import (
    FixtureCatalog,
    _read_methods,
    source_read_inventory,
    validate_dynamic_paths,
)
from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_integrity import (
    RECORDED_AWS_REHEARSAL_EXECUTOR_MODULE_PATHS,
)
from scripts.asr_base_model_pilot_plan import exact_plan


PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002J-attempt-11.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002J.json"
CAPTURE = ROOT / "platform/evidence/ASR-BASE-MODEL-AWS-READ-FIXTURE-CAPTURE-2026-001.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002I-ATTEMPT-10-NETWORK-ISOLATION-REFUSAL.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002J-COLD/cold-rehearsal.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def catalog() -> FixtureCatalog:
    return FixtureCatalog(ROOT, bound()["aws_read_fixtures"])


def test_packet_is_non_executable_attempt_eleven_request() -> None:
    value = bound()
    text = PACKET.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002J only" in text
    assert value["attempts"] == {
        "authorized_numbers": [11],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10.0,
        "attempts_1_through_10_reuse_permitted": False,
    }


def test_attempt_ten_refusal_and_all_history_are_write_once() -> None:
    history = bound()["write_once_history"]
    assert history["attempt_10_refusal"]["sha256"] == sha(REFUSAL)
    for item in history.values():
        assert sha(ROOT / item["path"]) == item["sha256"]


def test_every_discovered_aws_read_has_a_hash_bound_real_fixture() -> None:
    value = bound()
    evidence = json.loads(CAPTURE.read_bytes())
    result = catalog().summary()
    assert value["aws_read_fixtures"]["sha256"] == sha(CAPTURE)
    assert evidence["aws"]["mutations"] == 0
    assert result["runtime_read_api_count"] == len(source_read_inventory()) == 22
    assert result["fixture_count"] == 40
    assert result["uncovered_read_apis"] == 0
    assert result["boundary_fake_invented_fields"] == 0
    assert validate_dynamic_paths(catalog())["invented_field_count"] == 0


def test_new_unmapped_aws_read_fails_the_static_inventory(tmp_path: Path) -> None:
    source = tmp_path / "executor.py"
    source.write_text(
        "def execute(self):\n    return self.ec2.describe_route_tables()\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="unmapped executor AWS read methods"):
        _read_methods(source)


def test_gateway_response_has_no_prefix_id_and_live_gate_uses_prefix_list_api() -> None:
    gateway = catalog().payload("ec2-describe-vpc-endpoint-gateway-template")["VpcEndpoints"][0]
    prefix = catalog().payload("ec2-describe-prefix-lists-s3")["PrefixLists"]
    source = (ROOT / "scripts/asr_base_model_pilot_live.py").read_text(encoding="utf-8")
    fake = (ROOT / "scripts/asr_base_model_pilot_fake.py").read_text(encoding="utf-8")
    assert "PrefixListId" not in gateway
    assert len(prefix) == 1
    assert prefix[0]["PrefixListId"] == "pl-6ea54007"
    assert prefix[0]["PrefixListName"] == "com.amazonaws.eu-central-1.s3"
    assert prefix[0]["Cidrs"]
    assert "self.ec2.describe_prefix_lists(" in source
    assert 'next(item["PrefixListId"] for item in described' not in source
    assert 'value["PrefixListId"]' not in fake


def test_fixture_replay_refuses_unobserved_or_undeclared_fields() -> None:
    with pytest.raises(AssertionError, match="undeclared fields"):
        catalog().replay(
            "ec2-describe-vpc-endpoint-gateway-template",
            {"VpcEndpoints.0.PrefixListId": "pl-invented"},
        )
    with pytest.raises(AssertionError, match="undeclared fields"):
        catalog().replay(
            "ec2-describe-vpc-endpoint-gateway-template",
            {"VpcEndpoints.0.Tags.9.Key": "invented"},
        )


def test_rehearsal_boundaries_replay_correct_prefix_api_shape() -> None:
    operations, state = build_rehearsal_operations(bound())
    endpoints = state.fixtures.payload("ec2-describe-vpc-endpoint-gateway-template")
    assert "PrefixListId" not in endpoints["VpcEndpoints"][0]
    assert operations.ec2.describe_prefix_lists()["PrefixLists"][0]["PrefixListId"] == "pl-6ea54007"
    assert operations.ec2.get_managed_prefix_list_entries()["Entries"]


def test_all_seventeen_executor_modules_are_bound_at_reviewed_commit() -> None:
    value = bound()
    assert tuple(value["executor_modules"]) == RECORDED_AWS_REHEARSAL_EXECUTOR_MODULE_PATHS
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_attempt_eleven_plan_reuses_image_and_prestaged_bundle_read_only() -> None:
    value = bound()
    plan = exact_plan(value, 11)
    assert plan["permanent_create_only"] == []
    assert "ecr:repository/medzen-asr-eval-runtime" in plan["read_only_existing"]
    assert "s3:" + value["pilot_bundle"]["s3_prefix"].removeprefix("s3://") + "**" in plan["read_only_existing"]


def test_final_rehearsal_receipt_binds_real_shapes_and_is_in_packet() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["bindings_source"]["sha256"] == sha(BINDINGS)
    assert receipt["full_pass_runs"] == 1
    assert receipt["injected_failure_runs"] == 8
    assert receipt["aws_read_fixture_coverage"]["runtime_read_api_count"] == 22
    assert receipt["aws_read_fixture_coverage"]["fixture_count"] == 40
    assert receipt["aws_read_dynamic_paths"]["invented_field_count"] == 0
    assert receipt["scenarios"]["clean_pass"]["outcome"] == "PASS_PILOT"
    assert all(item["zero_state"] is True for item in receipt["scenarios"].values())
    packet = PACKET.read_text(encoding="utf-8")
    assert sha(COLD) in packet
    assert sha(BINDINGS) in packet
