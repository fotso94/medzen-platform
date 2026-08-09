import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_b6_5a_ssm_packet import (  # noqa: E402
    EXPECTED_ROOT,
    MANIFEST,
    PacketRefusal,
    validate,
)


PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-001-b6-5a-ssm-test-registry.md"


def request() -> dict:
    return json.loads(MANIFEST.read_bytes())


def test_packet_manifest_is_exact_and_locally_validated():
    value = validate()
    assert value["status"] == "PROPOSED_NOT_AUTHORIZED"
    assert value["snapshot"]["root"] == EXPECTED_ROOT
    assert value["snapshot"]["parameter_count"] == 3
    assert value["snapshot"]["maximum_parameter_count"] == 3
    assert hashlib.sha256(MANIFEST.read_bytes()).hexdigest() == (
        "75ec85328d1424acc80a0db55d5f407571c3ffcdc0e4f9e8a4ebe8962075edb3"
    )


def test_write_set_is_non_serving_securestring_create_only_and_manifest_last():
    value = request()
    parameters = value["parameters"]
    assert {item["PublishOrder"] for item in parameters} == {1, 2, 3}
    assert next(item for item in parameters if item["Name"].endswith("/_manifest"))[
        "PublishOrder"
    ] == 3
    assert all(item["Name"].startswith(EXPECTED_ROOT + "/") for item in parameters)
    assert all(item["Type"] == "SecureString" for item in parameters)
    assert all(item["Tier"] == "Standard" for item in parameters)
    assert all(item["Overwrite"] is False for item in parameters)
    wire = json.dumps(parameters, sort_keys=True)
    for forbidden in ("/medzen/registry/serving/current", "approved_version", '"artifact"'):
        assert forbidden not in wire


def test_allocation_tags_and_cost_ceiling_are_exact():
    allocation = request()["allocation"]
    assert allocation["allocation_id"] == "B6-SSM-TEST-REGISTRY"
    assert allocation["maximum_incremental_cost_usd"] == 0.10
    assert allocation["tags"] == {
        "BudgetRegistry": "COST-REGISTRY-2026-001",
        "CostCenter": "speech-platform",
        "Environment": "dev",
        "Project": "medzen-speech",
        "Stage": "B6.5A",
        "Workstream": "ssm-test-registry",
    }


def test_publisher_policy_adds_tag_readback_but_preserves_delete_deny():
    source = (ROOT / "infra/ssm.tf").read_text()
    assert 'actions   = ["ssm:AddTagsToResource"]' in source
    assert 'actions   = ["ssm:ListTagsForResource"]' in source
    assert 'variable = "aws:TagKeys"' in source
    for key, expected in request()["allocation"]["tags"].items():
        assert f'variable = "aws:RequestTag/{key}"' in source
        assert f'values   = ["{expected}"]' in source
    assert '"ssm:DeleteParameter", "ssm:DeleteParameters"' in source
    assert 'sid       = "DenyParameterDeletion"' in source


def test_packet_requires_separate_review_and_owner_approval_and_exact_rollback():
    value = PACKET.read_text()
    assert "AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL — NOT AUTHORIZED" in value
    assert "0 add, 1 change, 0 destroy" in value
    assert "Delete the\nmanifest first" in value
    assert "Use the verified owner/operator identity, never the publisher role" in value
    assert "does not add an owner trust path" in value
    assert "actual Pod Identity\n  read remains a B6.6 runtime check" in value
    assert "Approval of this packet does not approve B6.6" in value


def test_validator_fails_closed_on_value_or_root_tamper(monkeypatch, tmp_path):
    value = request()
    value["parameters"][1]["Value"] = "{}"
    tampered = tmp_path / "manifest.json"
    tampered.write_text(json.dumps(value))
    monkeypatch.setattr("scripts.check_b6_5a_ssm_packet.MANIFEST", tampered)
    with pytest.raises(PacketRefusal, match="differs from the reviewed fixture"):
        validate()

    changed = copy.deepcopy(request())
    changed["snapshot"]["root"] = "/medzen/registry/test/b6/" + "0" * 64
    tampered.write_text(json.dumps(changed))
    with pytest.raises(PacketRefusal, match="root differs"):
        validate()
