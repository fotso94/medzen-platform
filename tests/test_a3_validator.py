"""A3 validator must accept a clean manifest and reject each known defect.

If a check is ever weakened, one of these turns red. Run: .venv/bin/python -m pytest tests -q
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_manifest.py"
FIX = ROOT / "tests" / "fixtures"


def run(fixture: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(VALIDATOR), str(FIX / fixture)],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_clean_manifest_passes():
    rc, out = run("good.jsonl")
    assert rc == 0, out
    assert "pass all A3 checks" in out


@pytest.mark.parametrize("fixture,marker", [
    ("bad_speaker_leak.jsonl", "LEAK"),
    ("bad_audio.jsonl",        "sample_rate=44100"),
    ("bad_neardup.jsonl",      "NEAR-DUP across splits"),
    ("bad_provenance.jsonl",   "provenance field consent_id missing"),
])
def test_defect_is_rejected(fixture, marker):
    rc, out = run(fixture)
    assert rc == 1, f"{fixture} should have been rejected\n{out}"
    assert marker in out, f"expected '{marker}' in output\n{out}"


def test_near_dup_survives_disjoint_speakers():
    """The case neither leak check can catch: same content, different speaker
    and session, split across train/test."""
    rc, out = run("bad_neardup.jsonl")
    assert rc == 1
    assert "LEAK" not in out, "leak checks should NOT fire here"
    assert "NEAR-DUP" in out, "near-dup check must be what catches it"


def test_architecture_stays_consistent():
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_architecture.py")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def test_generated_artifacts_are_current():
    """Generated IAM/k8s must match services.yaml — catches hand-edits."""
    p = subprocess.run([sys.executable, str(ROOT / "platform" / "generate.py"), "--check"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
