from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.b6_6_registry_readback import (
    RAG_INDEX_SHA256,
    REGISTRY_ROOT,
    RegistryReadbackRefusal,
    verify,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_CAPTURE = ROOT / "tests/fixtures/aws/ssm-get-parameters-by-path-b6-test-registry.json"
SERVING_CAPTURE = ROOT / "tests/fixtures/aws/ssm-get-parameters-by-path-serving-empty.json"


class RecordedSSM:
    def __init__(self, registry: dict | None = None, serving: dict | None = None):
        self.registry = registry or json.loads(REGISTRY_CAPTURE.read_bytes())
        self.serving = serving or json.loads(SERVING_CAPTURE.read_bytes())
        self.calls: list[dict] = []

    def get_parameters_by_path(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if kwargs["Path"] == REGISTRY_ROOT:
            return copy.deepcopy(self.registry)
        if kwargs["Path"] == "/medzen/registry/serving":
            return copy.deepcopy(self.serving)
        raise AssertionError("unexpected SSM path")


def test_recorded_live_snapshot_is_exactly_reusable_without_writes() -> None:
    client = RecordedSSM()
    result = verify(client)
    assert result["status"] == "PASS_REUSE_IDENTICAL_COMPLETE"
    assert result["publication_mode"] == "READ_ONLY_REUSE_NO_WRITE"
    assert result["parameter_count"] == 3
    assert result["rag_index_sha256"] == RAG_INDEX_SHA256
    assert result["production_serving_pointer_present"] is False
    assert result["aws_read_calls"] == 2
    assert result["aws_write_calls"] == 0
    assert result["parameters_created"] == 0
    assert result["parameters_changed"] == 0
    assert result["parameters_deleted"] == 0
    assert [item["Path"] for item in client.calls] == [
        REGISTRY_ROOT,
        "/medzen/registry/serving",
    ]


def test_any_registry_value_mismatch_refuses() -> None:
    registry = json.loads(REGISTRY_CAPTURE.read_bytes())
    registry["Parameters"][2]["Value"] += " "
    with pytest.raises(
        RegistryReadbackRefusal,
        match="SSM_CONTENT_ADDRESSED_SNAPSHOT_DIFFERS",
    ):
        verify(RecordedSSM(registry=registry))


def test_production_pointer_or_other_serving_parameter_refuses() -> None:
    for name in (
        "/medzen/registry/serving/current",
        "/medzen/registry/serving/unexpected",
    ):
        serving = {"Parameters": [{"Name": name}]}
        with pytest.raises(RegistryReadbackRefusal):
            verify(RecordedSSM(serving=serving))


def test_recorded_real_response_fixtures_are_used_unmodified() -> None:
    assert len(json.loads(REGISTRY_CAPTURE.read_bytes())["Parameters"]) == 3
    assert json.loads(SERVING_CAPTURE.read_bytes()) == {"Parameters": []}


def test_window_reads_exact_snapshot_before_rotating_credential() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    readback = operations.index("b6_6_registry_readback.py")
    rotation = operations.index("b6_6_credential.py")
    assert readback < rotation
    assert "STAGE0_REGISTRY_READBACK_REFUSED" in operations
