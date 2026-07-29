"""A6 — resilience behaviour, with a deterministic clock (no sleeps)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "platform" / "lib"))

import resilience as R  # noqa: E402


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, s): self.t += s


def breaker(**kw):
    c = Clock()
    return R.CircuitBreaker(name="test", _clock=c, **kw), c


# --------------------------------------------------------------------------- #
def test_closed_breaker_allows_calls():
    b, _ = breaker()
    assert b.call(lambda: "ok") == "ok"
    assert b.state is R.State.CLOSED


def test_opens_after_failure_threshold():
    b, _ = breaker(failure_threshold=3)
    for _ in range(3):
        with pytest.raises(ValueError):
            b.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert b.state is R.State.OPEN
    with pytest.raises(R.BreakerOpen):
        b.call(lambda: "should not run")


def test_timeouts_trip_on_their_own_lower_threshold():
    b, _ = breaker(failure_threshold=99, timeout_threshold=3)
    for _ in range(3):
        with pytest.raises(TimeoutError):
            b.call(lambda: (_ for _ in ()).throw(TimeoutError()))
    assert b.state is R.State.OPEN


def test_failures_outside_window_do_not_accumulate():
    b, c = breaker(failure_threshold=3, window_s=30)
    for _ in range(2):
        with pytest.raises(ValueError):
            b.call(lambda: (_ for _ in ()).throw(ValueError()))
    c.advance(31)                       # old failures age out
    with pytest.raises(ValueError):
        b.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert b.state is R.State.CLOSED


def test_half_open_probe_recovers_or_reopens():
    b, c = breaker(failure_threshold=2, open_duration_s=15)
    for _ in range(2):
        with pytest.raises(ValueError):
            b.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert b.state is R.State.OPEN
    c.advance(15)
    assert b.state is R.State.HALF_OPEN
    assert b.call(lambda: "recovered") == "recovered"
    assert b.state is R.State.CLOSED


def test_failed_probe_reopens_immediately():
    b, c = breaker(failure_threshold=2, open_duration_s=15)
    for _ in range(2):
        with pytest.raises(ValueError):
            b.call(lambda: (_ for _ in ()).throw(ValueError()))
    c.advance(15)
    with pytest.raises(ValueError):
        b.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert b.state is R.State.OPEN, "one failed probe must reopen, not retry"


# --- fallback chain -------------------------------------------------------- #
def boom(exc=RuntimeError):
    def f(): raise exc()
    return f


def test_chain_uses_first_healthy_provider():
    res = R.run_chain([("fish", lambda: "audio")], terminal="text_only")
    assert res.provider == "fish" and res.value == "audio"


def test_chain_falls_through_to_self_hosted():
    res = R.run_chain([("fish", boom()), ("self_hosted", lambda: "audio")],
                      terminal="text_only")
    assert res.provider == "self_hosted"
    assert res.attempts[0] == ("fish", "error:RuntimeError")


def test_text_only_is_a_success_not_an_error():
    """The clinical invariant: a valid answer is never discarded."""
    res = R.run_chain([("fish", boom()), ("self_hosted", boom())],
                      terminal="text_only")
    assert res.provider == "text_only"
    assert res.exhausted is False, "text_only is a degraded SUCCESS"
    assert res.value is None


def test_llm_chain_has_no_terminal_and_reports_exhaustion():
    """No safe fallback for understanding — must fail, never invent."""
    res = R.run_chain([("bedrock", boom())], terminal=None)
    assert res.exhausted is True and res.provider == "none"


def test_open_breaker_short_circuits_without_calling():
    b, _ = breaker(failure_threshold=1)
    with pytest.raises(ValueError):
        b.call(lambda: (_ for _ in ()).throw(ValueError()))
    called = []
    res = R.run_chain([("fish", lambda: called.append(1)),
                       ("self_hosted", lambda: "audio")],
                      breakers={"fish": b}, terminal="text_only")
    assert called == [], "an open breaker must not call the provider"
    assert res.provider == "self_hosted"
    assert res.attempts[0] == ("fish", "breaker_open")


# --- bounded queues -------------------------------------------------------- #
def test_partials_drop_oldest():
    q = R.BoundedQueue(4, "drop_oldest")
    for i in range(6):
        assert q.put(i) is True
    assert len(q) == 4 and q.dropped == 2
    assert q.get() == 2, "oldest two were dropped"


def test_audio_pauses_upstream_instead_of_growing():
    q = R.BoundedQueue(8, "pause_upstream")
    for i in range(8):
        assert q.put(i) is True
    assert q.put(99) is False and q.paused is True
    q.get()
    assert q.paused is False


def test_final_results_are_never_silently_dropped():
    q = R.BoundedQueue(2, "block")
    q.put("transcript"); q.put("answer")
    with pytest.raises(R.QueueFull):
        q.put("third")


# --- config ---------------------------------------------------------------- #
def test_config_matches_implementation_defaults():
    c = R.load_config()
    d = c["circuit_breakers"]["defaults"]
    b = R.CircuitBreaker(name="x")
    assert b.failure_threshold == d["failure_threshold"]
    assert b.timeout_threshold == d["timeout_threshold"]
    assert b.window_s == d["window_s"]
    assert b.open_duration_s == d["open_duration_s"]


def test_emergency_detection_can_never_be_shed():
    c = R.load_config()
    assert "emergency_detection" in c["degradation_order"]["never_shed"]
    assert "safety_gate" in c["degradation_order"]["never_shed"]
    shed = [s["shed"] for s in c["degradation_order"]["steps"]]
    assert "safety_gate" not in shed and "emergency_detection" not in shed
