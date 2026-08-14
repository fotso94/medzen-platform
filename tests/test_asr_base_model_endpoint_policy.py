from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_endpoint_policy import (
    EndpointPolicyRefusal,
    PILOT_BUCKET,
    WHISPER_PREFIX,
    build_call_inventory,
    derive_policy,
    validate_observed_s3_calls,
    validate_policy_coverage,
)
from scripts.asr_base_model_pilot_live import PRIVATE_PULL_REPOSITORIES


BUNDLE = ROOT / "tests/fixtures/asr_base_model_pilot/pilot-bundle-2026-001.json"
MODELS = ROOT / "tests/fixtures/asr_base_model_pilot/model-bindings-2026-001.json"
BUNDLE_SHA = "1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee"


def inventory() -> dict:
    return build_call_inventory(
        bundle_sha256=BUNDLE_SHA,
        pilot_bundle=json.loads(BUNDLE.read_bytes()),
        model_bindings=json.loads(MODELS.read_bytes()),
        account="558069890522",
        region="eu-central-1",
        ecr_repositories=PRIVATE_PULL_REPOSITORIES,
    )


def actions(policy: dict) -> set[str]:
    return {
        action
        for statement in policy["Statement"]
        for action in statement["Action"]
    }


def test_versioned_and_unversioned_gets_derive_distinct_actions() -> None:
    value = inventory()
    s3_calls = [row for row in value["calls"] if row["service"] == "s3"]
    versioned = [
        row for row in s3_calls if row["parameters"]["version_id_present"]
    ]
    unversioned = [
        row for row in s3_calls if not row["parameters"]["version_id_present"]
    ]
    assert len(versioned) == 8
    assert len(unversioned) == 5
    assert {row["required_action"] for row in versioned} == {
        "s3:GetObjectVersion"
    }
    assert {row["required_action"] for row in unversioned} == {"s3:GetObject"}
    assert all("version_id_sha256" in row["parameters"] for row in versioned)
    assert all("version_id_sha256" not in row["parameters"] for row in unversioned)


def test_derived_s3_policy_adds_version_action_without_prefix_broadening() -> None:
    policy = derive_policy(inventory(), "s3")
    assert actions(policy) == {"s3:GetObject", "s3:GetObjectVersion"}
    resources = {
        resource
        for statement in policy["Statement"]
        for resource in statement["Resource"]
    }
    assert (
        f"arn:aws:s3:::{PILOT_BUCKET}/research/asr-base-model/pilot/{BUNDLE_SHA}/*"
        in resources
    )
    assert f"arn:aws:s3:::{PILOT_BUCKET}/{WHISPER_PREFIX}*" in resources
    assert f"arn:aws:s3:::{PILOT_BUCKET}/*" not in resources
    assert "arn:aws:s3:::medzen-speech/research/asr-base-model/pilot/*" not in resources


def test_derived_ecr_policy_covers_exact_pull_inventory() -> None:
    value = inventory()
    policy = derive_policy(value, "ecr")
    assert actions(policy) == {
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
    }
    result = validate_policy_coverage(value, policy, "ecr")
    assert result["recorded_call_count"] == 22
    assert result["uncovered_calls"] == 0


def test_missing_get_object_version_refuses_static_coverage() -> None:
    value = inventory()
    policy = derive_policy(value, "s3")
    policy["Statement"] = [
        row for row in policy["Statement"] if row["Action"] != ["s3:GetObjectVersion"]
    ]
    with pytest.raises(EndpointPolicyRefusal) as captured:
        validate_policy_coverage(value, policy, "s3")
    assert captured.value.reason_code == "ENDPOINT_POLICY_CALL_UNCOVERED"


def test_request_version_flag_and_recorded_action_cannot_drift() -> None:
    value = copy.deepcopy(inventory())
    versioned = next(
        row
        for row in value["calls"]
        if row["service"] == "s3"
        and row["parameters"]["version_id_present"] is True
    )
    versioned["parameters"]["version_id_present"] = False
    with pytest.raises(EndpointPolicyRefusal) as captured:
        derive_policy(value, "s3")
    assert captured.value.reason_code == "ENDPOINT_RECORDED_ACTION_DIFFERS"


def test_observed_node_requests_must_equal_policy_inventory() -> None:
    value = inventory()
    observed = [
        {
            "operation": row["parameters"]["operation"],
            "bucket": row["parameters"]["bucket"],
            "key": row["parameters"]["key"],
            "version_id_present": row["parameters"]["version_id_present"],
        }
        for row in value["calls"]
        if row["service"] == "s3"
    ]
    result = validate_observed_s3_calls(value, observed)
    assert result == {
        "status": "PASS_OBSERVED_S3_CALLS_MATCH_POLICY_INVENTORY",
        "observed_call_count": 13,
        "unique_call_count": 13,
        "versioned_call_count": 8,
        "unversioned_call_count": 5,
    }
    observed[0]["version_id_present"] = not observed[0]["version_id_present"]
    with pytest.raises(EndpointPolicyRefusal) as captured:
        validate_observed_s3_calls(value, observed)
    assert captured.value.reason_code == "ENDPOINT_OBSERVED_S3_CALLS_DIFFER"

    duplicate = [
        {
            "operation": row["parameters"]["operation"],
            "bucket": row["parameters"]["bucket"],
            "key": row["parameters"]["key"],
            "version_id_present": row["parameters"]["version_id_present"],
        }
        for row in value["calls"]
        if row["service"] == "s3"
    ]
    duplicate.append(dict(duplicate[0]))
    with pytest.raises(EndpointPolicyRefusal) as captured:
        validate_observed_s3_calls(value, duplicate)
    assert captured.value.reason_code == "ENDPOINT_OBSERVED_S3_CALLS_DIFFER"


def test_no_other_s3_version_variant_is_needed() -> None:
    result = validate_policy_coverage(inventory(), derive_policy(inventory(), "s3"), "s3")
    assert result["required_actions"] == ["s3:GetObject", "s3:GetObjectVersion"]
    assert result["version_variant_actions"] == ["s3:GetObjectVersion"]
    assert result["versioned_call_count"] == 8
