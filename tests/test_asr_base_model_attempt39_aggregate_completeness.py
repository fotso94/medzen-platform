"""Attempt-39 hardening: aggregate completeness derives from CANDIDATES.

Attempt 38 completed ALL of its Meta-only inferences (8,634 for shard 6)
and was then destroyed by this validator still demanding whisper-era
counts (rows*3 unconditioned + a ("whisper","meta_llm") conditioned sum
= 12,552). The expectation now derives from the SAME candidate registry
the workload evaluates, so removing or adding a candidate changes both
sides of the comparison at once and a mismatch of this class cannot
recur silently.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/asr-eval-runtime"))

from medzen_asr_eval.identity import CANDIDATES  # noqa: E402
from scripts.asr_base_model_pilot_live import LiveOperations  # noqa: E402
from scripts.asr_base_model_pilot_runner import OperationRefusal  # noqa: E402


CONDITIONING = json.loads(
    (ROOT / "services/asr-eval-runtime/assets/language-conditioning-v1.json")
    .read_bytes())["languages"]


class _Harness:
    """Drives aggregate_report with a scripted SSM aggregate readback."""

    def __init__(self, tmp_path, rows, aggregate):
        self.ops = LiveOperations.__new__(LiveOperations)
        self.ops.root = ROOT
        body = json.dumps(aggregate).encode()
        self.ops._ssm_read_file_chunked = lambda *a, **k: (body, {"chunks": 1})
        self.ops._state = lambda context: {
            "instance_id": "i-test", "staging_path": "/var/lib/test"}
        workdir = tmp_path
        (workdir / "pilot-selection.json").write_bytes(
            json.dumps({"rows": rows}).encode())

        class Ctx:
            attempt = 39
            bindings = {"input_freeze": {"pilot_rows": len(rows)}}
        Ctx.workdir = workdir
        self.context = Ctx()

    def run(self):
        return LiveOperations.aggregate_report(self.ops, self.context)


def _selection(languages):
    return [{"language": lang} for lang in languages]


def _expected_counts(rows):
    """The Meta-only ground truth, computed independently of the code
    under test: unconditioned passes = candidates with unconditioned=True;
    conditioned = meta_llm-conditionable rows (whisper is NOT evaluated)."""
    unconditioned = sum(1 for c in CANDIDATES.values() if c.unconditioned)
    conditionable = [c for c in CANDIDATES.values() if c.conditioned]
    conditioned = sum(
        1 for row in rows
        if CONDITIONING[row["language"]]["meta_llm"] is not None
    ) * len(conditionable)
    return (len(rows) * unconditioned + conditioned,
            len(rows) * len(conditionable) - conditioned)


def test_current_candidate_set_is_meta_only():
    """The directive this file guards: whisper is not in the evaluated set."""
    assert "whisper" not in " ".join(CANDIDATES).lower()
    assert sum(1 for c in CANDIDATES.values() if c.unconditioned) == 2
    assert sum(1 for c in CANDIDATES.values() if c.conditioned) == 1


def test_meta_only_aggregate_at_exact_counts_passes(tmp_path):
    rows = _selection(["english"] * 3 + ["igbo"] * 2)
    completed, not_applicable = _expected_counts(rows)
    outcome = _Harness(tmp_path, rows, {
        "status": "PASS_AGGREGATE",
        "runtime_rows": len(rows),
        "completed_inferences": completed,
        "not_applicable": not_applicable,
        "aggregate": {"gpu_memory": {}, "groups": {}},
    }).run()
    assert outcome["status"] == "PASS_AGGREGATE_REPORT"
    assert outcome["completed_inferences"] == completed


def test_whisper_era_counts_are_refused_not_expected(tmp_path):
    """The attempt-38 killer, inverted: an aggregate carrying the OLD
    whisper-era expectation must now REFUSE, proving the validator no
    longer wants those numbers."""
    rows = _selection(["english"] * 3 + ["igbo"] * 2)
    whisper_era_completed = len(rows) * 3 + sum(
        int(CONDITIONING[r["language"]][p] is not None)
        for r in rows for p in ("whisper", "meta_llm"))
    with pytest.raises(OperationRefusal) as caught:
        _Harness(tmp_path, rows, {
            "status": "PASS_AGGREGATE",
            "runtime_rows": len(rows),
            "completed_inferences": whisper_era_completed,
            "not_applicable": len(rows) * 2,
            "aggregate": {"gpu_memory": {}, "groups": {}},
        }).run()
    assert caught.value.reason_code == "AGGREGATE_COMPLETENESS_DIFFERS"


def test_shard6_regression_the_destroyed_run_would_now_pass(tmp_path):
    """Attempt 38's exact shape: 2,878 rows, every language meta_llm
    conditionable -> 8,634 completed, 0 not applicable. This aggregate
    was REAL and was refused; it must pass now."""
    selection = json.load(open(
        "/private/tmp/medzen-asr-attempt38-20260816T2350-Jd5Pk1/live/pilot-selection.json"
    )) if Path("/private/tmp/medzen-asr-attempt38-20260816T2350-Jd5Pk1/live/pilot-selection.json").exists() else None
    if selection is None:
        pytest.skip("attempt-38 workdir no longer on this host")
    rows = selection["rows"]
    completed, not_applicable = _expected_counts(rows)
    assert (completed, not_applicable) == (8634, 0), (
        "shard-6 ground truth: the destroyed run completed exactly this")
    outcome = _Harness(tmp_path, rows, {
        "status": "PASS_AGGREGATE",
        "runtime_rows": len(rows),
        "completed_inferences": completed,
        "not_applicable": not_applicable,
        "aggregate": {"gpu_memory": {}, "groups": {}},
    }).run()
    assert outcome["status"] == "PASS_AGGREGATE_REPORT"


def test_short_row_count_still_refuses(tmp_path):
    rows = _selection(["english"] * 4)
    completed, not_applicable = _expected_counts(rows)
    with pytest.raises(OperationRefusal):
        _Harness(tmp_path, rows, {
            "status": "PASS_AGGREGATE",
            "runtime_rows": len(rows),
            "completed_inferences": completed - 1,
            "not_applicable": not_applicable,
            "aggregate": {"gpu_memory": {}, "groups": {}},
        }).run()
