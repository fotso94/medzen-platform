"""Attempt-38 hardening: the Job poll absorbs isolated transient failures.

Attempts 36 and 37 were each refused by a single host-network blip of the
read-only Job poll while the cluster Job ran on unharmed. The waiter now
tolerates up to three CONSECUTIVE poll failures on its own cadence; a
fourth — or the deadline — still fails closed, and non-transient refusals
pass straight through.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.asr_base_model_pilot_live import LiveOperations
from scripts.asr_base_model_pilot_runner import OperationRefusal


class _Harness:
    """Drives _wait_pilot_job_complete with scripted kubectl outcomes."""

    def __init__(self, outcomes):
        self.ops = LiveOperations.__new__(LiveOperations)
        self.outcomes = list(outcomes)
        self.sleeps = []
        clock = {"now": 0.0}

        def kubectl_runner(context, *args, **kwargs):
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        self.ops._kubectl_runner = kubectl_runner
        self.ops._sleeper = lambda s: (self.sleeps.append(s),
                                       clock.__setitem__("now", clock["now"] + s))[0]
        self.ops._monotonic = lambda: clock["now"]
        self.ops._sample_runtime_resources = lambda *a, **k: None
        self.ops._runtime_telemetry_summary = lambda c: {"pass_sample_count": 2}
        self.clock = clock

    def run(self, timeout_seconds=9000):
        class Ctx:
            attempt = 38
        return LiveOperations._wait_pilot_job_complete(
            self.ops, Ctx(), timeout_seconds=timeout_seconds,
            poll_interval_seconds=10)


COMPLETE = {"status": {"conditions": [{"type": "Complete", "status": "True"}]}}
ACTIVE = {"status": {"active": 1}}


def _blip():
    return OperationRefusal("BOUNDED_COMMAND_REFUSED", "kubectl refused: transient")


def test_isolated_blips_are_absorbed_and_recorded():
    h = _Harness([ACTIVE, _blip(), _blip(), ACTIVE, COMPLETE])
    result = h.run()
    assert result["status"] == "PASS_PILOT_JOB_COMPLETE"
    seq = result["state_sequence"]
    assert "POLL_TRANSIENT_FAILURE_1" in seq and "POLL_TRANSIENT_FAILURE_2" in seq
    assert seq[-1] == "COMPLETE"


def test_success_resets_the_consecutive_counter():
    h = _Harness([_blip(), _blip(), _blip(), ACTIVE,
                  _blip(), _blip(), _blip(), COMPLETE])
    result = h.run()
    assert result["status"] == "PASS_PILOT_JOB_COMPLETE"


def test_a_fourth_consecutive_failure_fails_closed():
    h = _Harness([ACTIVE, _blip(), _blip(), _blip(), _blip()])
    with pytest.raises(OperationRefusal, match="kubectl refused"):
        h.run()


def test_non_transient_refusals_pass_straight_through():
    h = _Harness([OperationRefusal("KUBECTL_FORBIDDEN", "rbac denied")])
    with pytest.raises(OperationRefusal, match="rbac denied"):
        h.run()


def test_deadline_still_rules_during_blips():
    """Blip sleeps consume the same bounded budget as ordinary polls."""
    h = _Harness([_blip()] * 3 + [ACTIVE] * 10000)
    with pytest.raises(OperationRefusal, match="did not complete inside"):
        h.run(timeout_seconds=25)  # 3 tolerated blips x 10s sleeps exceed it
