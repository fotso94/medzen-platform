"""B7 Phase 4: the alias rollback plans correctly and refuses everything unsafe."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.b7_alias_rollback import (
    RollbackRefusal,
    execute_rollback,
    plan_rollback,
)


class StubSSM:
    def __init__(self, versions):
        self._versions = versions
        self.put_calls = []

    def get_parameter_history(self, Name, WithDecryption):
        return {"Parameters": [
            {"Version": i + 1, "Value": v} for i, v in enumerate(self._versions)
        ]}

    def put_parameter(self, **kwargs):
        self.put_calls.append(kwargs)
        self._versions.append(kwargs["Value"])
        return {"Version": len(self._versions)}

    def get_parameter(self, Name, WithDecryption):
        return {"Parameter": {"Value": self._versions[-1]}}


ALIAS = "/medzen/registry/test/b6/alias"


def test_plan_targets_the_previous_version():
    plan = plan_rollback(StubSSM(["v1", "v2", "v3"]), ALIAS)
    assert plan["restore_value"] == "v2"
    assert plan["current_version"] == 3 and plan["previous_version"] == 2


def test_execute_restores_and_verifies_readback():
    stub = StubSSM(["v1", "v2"])
    plan = plan_rollback(stub, ALIAS)
    result = execute_rollback(stub, plan)
    assert result["status"] == "PASS_ALIAS_ROLLBACK"
    assert stub.put_calls[0]["Value"] == "v1"
    assert result["new_version"] == 3  # restore is a NEW version, history intact


def test_production_pointer_is_refused_outright():
    with pytest.raises(RollbackRefusal, match="own reviewed packet"):
        plan_rollback(StubSSM(["a", "b"]), "/medzen/registry/serving/current")


def test_out_of_prefix_parameter_is_refused():
    with pytest.raises(RollbackRefusal, match="outside"):
        plan_rollback(StubSSM(["a", "b"]), "/medzen/other/thing")


def test_single_version_has_nothing_to_roll_back_to():
    with pytest.raises(RollbackRefusal, match="nothing to roll back"):
        plan_rollback(StubSSM(["only"]), ALIAS)


def test_noop_rollback_is_refused():
    with pytest.raises(RollbackRefusal, match="no-op"):
        plan_rollback(StubSSM(["same", "same"]), ALIAS)


def test_tampered_readback_fails_closed():
    class LyingSSM(StubSSM):
        def get_parameter(self, Name, WithDecryption):
            return {"Parameter": {"Value": "something-else"}}

    stub = LyingSSM(["v1", "v2"])
    plan = plan_rollback(stub, ALIAS)
    with pytest.raises(RollbackRefusal, match="readback differs"):
        execute_rollback(stub, plan)
