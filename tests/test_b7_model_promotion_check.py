"""B7 model pipeline: the gate-report check refuses everything but proven PASS."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.b7_model_promotion_check import (
    PromotionCheckRefusal,
    load_gate_report,
    promotable_languages,
)


def _report(tmp_path, languages):
    path = tmp_path / "gate-report.json"
    path.write_text(json.dumps({
        "schema_version": "medzen-b5-gate-report-v1",
        "languages": {k: {"state": v} for k, v in languages.items()},
        "gate_state_counts": {},
    }))
    return path


def test_all_pass_languages_promote(tmp_path):
    report = load_gate_report(_report(tmp_path, {"hausa": "PASS", "igbo": "PASS"}))
    assert promotable_languages(report, ["hausa", "igbo"]) == {"hausa": "PASS", "igbo": "PASS"}


@pytest.mark.parametrize("state", ["FAIL", "NOT_EVALUATED", "DEFERRED", "NOT_APPLICABLE"])
def test_any_non_pass_refuses_the_whole_set(tmp_path, state):
    report = load_gate_report(_report(tmp_path, {"hausa": "PASS", "igbo": state}))
    with pytest.raises(PromotionCheckRefusal, match=f"igbo: state {state}"):
        promotable_languages(report, ["hausa", "igbo"])


def test_language_missing_from_report_refuses(tmp_path):
    report = load_gate_report(_report(tmp_path, {"hausa": "PASS"}))
    with pytest.raises(PromotionCheckRefusal, match="not in the gate report"):
        promotable_languages(report, ["hausa", "wolof"])


def test_absent_and_malformed_reports_refuse(tmp_path):
    with pytest.raises(PromotionCheckRefusal, match="absent"):
        load_gate_report(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(PromotionCheckRefusal, match="unparseable"):
        load_gate_report(bad)
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    with pytest.raises(PromotionCheckRefusal, match="lacks"):
        load_gate_report(empty)


def test_cli_exit_codes(tmp_path):
    # Since PROMOTION-PROTOCOL-2026-001 (Codex review #7), a bare-PASS
    # report REFUSES: pre-protocol reports cannot promote. The full-chain
    # passing case lives in test_arch_2026_001.py.
    path = _report(tmp_path, {"hausa": "PASS"})
    root = Path(__file__).resolve().parents[1]
    ok = subprocess.run([sys.executable, "scripts/b7_model_promotion_check.py",
                         "--gate-report", str(path), "--languages", "hausa"],
                        capture_output=True, cwd=root)
    assert ok.returncode == 1
    assert b"PROMOTION-PROTOCOL-2026-001" in ok.stdout
    bad = subprocess.run([sys.executable, "scripts/b7_model_promotion_check.py",
                          "--gate-report", str(path), "--languages", "hausa,wolof"],
                         capture_output=True, cwd=root)
    assert bad.returncode == 1
