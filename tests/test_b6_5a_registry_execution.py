import json
import sys
from pathlib import Path

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_b6_5a_registry_publication import (  # noqa: E402
    inspect_snapshot,
    load_controls,
    publish,
)


class FakeSsm:
    def __init__(self, request):
        self.request = request
        self.values = {}
        self.tags = {}
        self.puts = []

    def get_parameter(self, Name, WithDecryption):
        assert WithDecryption is True
        if Name not in self.values:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "absent"}},
                "GetParameter",
            )
        return {"Parameter": self.values[Name]}

    def put_parameter(self, **value):
        assert value["Overwrite"] is False
        assert value["Name"] not in self.values
        self.puts.append(value["Name"])
        self.values[value["Name"]] = {
            "Name": value["Name"],
            "Value": value["Value"],
            "Type": value["Type"],
            "Version": 1,
        }
        self.tags[value["Name"]] = {
            item["Key"]: item["Value"] for item in value["Tags"]
        }
        return {"Version": 1}

    def list_tags_for_resource(self, ResourceType, ResourceId):
        assert ResourceType == "Parameter"
        return {"TagList": [
            {"Key": key, "Value": value}
            for key, value in sorted(self.tags[ResourceId].items())
        ]}

    def describe_parameters(self, ParameterFilters):
        name = ParameterFilters[0]["Values"][0]
        expected = next(item for item in self.request["parameters"] if item["Name"] == name)
        return {"Parameters": [{
            "Name": name,
            "Type": "SecureString",
            "KeyId": expected["KeyId"],
            "Tier": "Standard",
            "DataType": "text",
        }]}


def request():
    _, value = load_controls()
    return value


def test_approved_control_hashes_and_identity_are_current():
    authorization, value = load_controls()
    assert authorization["status"] == "OWNER_APPROVED_FOR_EXECUTION"
    assert value["snapshot"]["parameter_count"] == 3


def test_create_only_publication_writes_manifest_last_and_reuses_identically():
    value = request()
    client = FakeSsm(value)
    mode, before = inspect_snapshot(client, client, value)
    assert mode == "CREATE"
    assert {item["state"] for item in before} == {"ABSENT"}
    outcome, receipts = publish(client, client, value, mode)
    assert outcome == "PUBLISHED_VERIFIED_NON_SERVING"
    assert client.puts[-1].endswith("/_manifest")
    assert all(item["state"] == "PRESENT_IDENTICAL" for item in receipts)
    mode, _ = inspect_snapshot(client, client, value)
    assert mode == "REUSE_IDENTICAL_COMPLETE"
    count = len(client.puts)
    outcome, _ = publish(client, client, value, mode)
    assert outcome == "REUSE_IDENTICAL_COMPLETE"
    assert len(client.puts) == count


def test_partial_or_tampered_snapshot_refuses():
    from scripts.run_b6_5a_registry_publication import PublicationRefusal

    value = request()
    client = FakeSsm(value)
    first = sorted(value["parameters"], key=lambda item: item["PublishOrder"])[0]
    client.put_parameter(
        **{key: first[key] for key in ("Name", "Value", "Type", "KeyId", "Overwrite", "Tier", "DataType")},
        Tags=[{"Key": key, "Value": item} for key, item in value["allocation"]["tags"].items()],
        Description="test",
    )
    try:
        inspect_snapshot(client, client, value)
    except PublicationRefusal as exc:
        assert "partial" in str(exc)
    else:
        raise AssertionError("partial snapshot was accepted")

    client.values[first["Name"]]["Value"] = json.dumps({"tampered": True})
    try:
        inspect_snapshot(client, client, value)
    except PublicationRefusal as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("tampered snapshot was accepted")
