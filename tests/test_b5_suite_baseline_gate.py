"""T6 tests: the suite-baseline gate refuses dishonest comparisons."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.b5_gates import FailClosedError  # noqa: E402
from pipeline.b5_suite_baseline_gate import (  # noqa: E402
    evaluate_candidate,
    load_bound_report,
)

BASE = "omniASR_CTC_1B_v2|unconditioned"
CAND = "medzen_ctc_ft_v1|unconditioned"


@pytest.fixture()
def registry_root(tmp_path):
    """Minimal registry mirroring the live layout for one language."""
    defaults = tmp_path / "registry/gates/_defaults.yaml"
    defaults.parent.mkdir(parents=True)
    defaults.write_text(
        "version: v1-initial\n"
        "asr:\n"
        "  relative_wer_gain_min: 0.15\n"
        "  absolute_wer_max: 0.5\n"
        "  absolute_cer_max: null\n")
    gate = tmp_path / "registry/gates/tonal-v1.yaml"
    gate.write_text(
        "inherits: _defaults.yaml\n"
        "language: tonal\n"
        "version: v1-initial\n"
        "rationale: test\n"
        "overrides:\n"
        "  asr:\n"
        "    absolute_cer_max: 0.10\n")
    lang = tmp_path / "registry/languages/tonal.yaml"
    lang.parent.mkdir(parents=True)
    lang.write_text("alias: tonal\nthresholds_ref: gates/tonal-v1.yaml\n")
    return tmp_path


def _report(status: str, rows: dict) -> dict:
    return {"coverage": {"status": status}, "metrics": {"per_language": rows}}


def suite(wer=0.5, cer=0.2):
    return _report("PASS_GAP_FREE_COVERAGE", {f"{BASE}|tonal": {"wer": wer, "cer": cer}})


def cand(wer, cer):
    return _report("PASS_GAP_FREE_COVERAGE", {f"{CAND}|tonal": {"wer": wer, "cer": cer}})


def test_improving_candidate_under_ceilings_passes(registry_root):
    result = evaluate_candidate(
        suite_report=suite(0.5), candidate_report=cand(0.30, 0.08),
        candidate_name="medzen_ctc_ft_v1", campaign_languages=["tonal"],
        root=registry_root)
    assert result["status"] == "PASS_SUITE_BASELINE_GATES"
    gate_names = [g["gate"] for g in result["languages"]["tonal"]["gates"]]
    assert gate_names == ["asr.suite_improvement_wer", "asr.absolute_wer",
                          "asr.absolute_cer"]


def test_no_improvement_fails_even_under_ceilings(registry_root):
    result = evaluate_candidate(
        suite_report=suite(0.3), candidate_report=cand(0.3, 0.05),
        candidate_name="medzen_ctc_ft_v1", campaign_languages=["tonal"],
        root=registry_root)
    assert result["status"] == "FAIL_SUITE_BASELINE_GATES"


def test_tonal_cer_ceiling_fails_a_fast_but_sloppy_candidate(registry_root):
    result = evaluate_candidate(
        suite_report=suite(0.5), candidate_report=cand(0.2, 0.15),
        candidate_name="medzen_ctc_ft_v1", campaign_languages=["tonal"],
        root=registry_root)
    assert result["status"] == "FAIL_SUITE_BASELINE_GATES"
    states = {g["gate"]: g["state"] for g in result["languages"]["tonal"]["gates"]}
    assert states["asr.absolute_cer"] == "FAIL"


def test_missing_candidate_row_is_not_evaluated_never_pass(registry_root):
    result = evaluate_candidate(
        suite_report=suite(), candidate_report=_report("PASS_GAP_FREE_COVERAGE", {}),
        candidate_name="medzen_ctc_ft_v1", campaign_languages=["tonal"],
        root=registry_root)
    assert result["status"] == "FAIL_SUITE_BASELINE_GATES"
    assert result["languages"]["tonal"]["gates"][0]["state"] == "NOT_EVALUATED"


def test_language_absent_from_suite_refuses_outright(registry_root):
    with pytest.raises(FailClosedError, match="not in"):
        evaluate_candidate(
            suite_report=suite(), candidate_report=cand(0.2, 0.05),
            candidate_name="medzen_ctc_ft_v1",
            campaign_languages=["ghost"], root=registry_root)


def test_incomplete_suite_refuses(registry_root):
    bad = _report("COVERAGE_INCOMPLETE", {})
    with pytest.raises(FailClosedError, match="gap-free"):
        evaluate_candidate(
            suite_report=bad, candidate_report=cand(0.2, 0.05),
            candidate_name="medzen_ctc_ft_v1",
            campaign_languages=["tonal"], root=registry_root)


def test_bound_report_refuses_drift(tmp_path):
    path = tmp_path / "report.json"
    body = json.dumps({"x": 1}).encode()
    path.write_bytes(body)
    good = hashlib.sha256(body).hexdigest()
    assert load_bound_report(path, good) == {"x": 1}
    with pytest.raises(FailClosedError, match="moved"):
        load_bound_report(path, "0" * 64)
