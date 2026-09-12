"""The near-silence filter in the Cameroon pilot splits builder must drop a clip
of true digital silence.

The original guard read `if (row.get("rms") or 1.0) < 1e-4`. In Python 0.0 is
falsey, so a clip measured at exactly 0.0 rms took the 1.0 default and passed
the silence filter; so did a clip with no rms measurement at all. Only a clip
strictly between 0 and 1e-4 was caught, which is the narrowest possible reading
of "near-silent" and excludes the one case that is unambiguously silent.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "build_cameroon_pilot_splits", ROOT / "scripts/build_cameroon_pilot_splits.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
audited = _mod.audited


def _row(path: str, **kw) -> dict:
    row = {"path": path, "client_id": "spk", "text": "t", "real_s": 1.0}
    row.update(kw)
    return row


def test_exactly_zero_rms_is_dropped():
    kept = audited([_row("silent.mp3", rms=0.0)], {})
    assert kept == [], "a clip of true digital silence must not survive the filter"


def test_near_silent_rms_is_dropped():
    assert audited([_row("quiet.mp3", rms=5e-5)], {}) == []


def test_audible_rms_is_kept():
    rows = [_row("ok.mp3", rms=0.05)]
    assert audited(rows, {}) == rows


def test_rms_at_the_threshold_is_kept():
    rows = [_row("edge.mp3", rms=_mod.SILENCE_RMS)]
    assert audited(rows, {}) == rows


def test_missing_rms_is_refused_not_assumed_loud():
    with pytest.raises(ValueError, match="no rms measurement"):
        audited([_row("unmeasured.mp3")], {})


def test_zero_rms_is_dropped_even_when_the_audit_marks_it_clean():
    audit = {"silent.mp3": {"max_sat_run": 0, "tail_ratio": 0.0, "head_ratio": 0.0}}
    assert audited([_row("silent.mp3", rms=0.0)], audit) == []


def test_the_old_or_idiom_would_have_kept_silence():
    """Pins the defect itself, so a regression to `or 1.0` fails here loudly."""
    row = _row("silent.mp3", rms=0.0)
    assert (row.get("rms") or 1.0) == 1.0
    assert audited([row], {}) == []
