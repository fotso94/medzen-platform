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


# --------------------------------------------------------------------------- #
# A4 — registry
# --------------------------------------------------------------------------- #
import shutil

import yaml

REG = ROOT / "registry" / "languages"


def run_registry() -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_registry.py")],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_registry_is_valid():
    rc, out = run_registry()
    assert rc == 0, out


def test_all_languages_start_unapproved():
    """Nothing is trained yet — a language claiming otherwise is a bug."""
    for f in REG.glob("*.yaml"):
        d = yaml.safe_load(f.read_text())
        assert d["status"] == "declared", f"{f.stem} claims status {d['status']}"
        assert d["tts"]["approved"] is False, f"{f.stem} claims an approved voice"
        assert d["asr"]["decode_strategy"]["mode"] == "pending_experiment", \
            f"{f.stem} has a decode strategy but no experiment has run"


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d.update(status="production"),
     "requires an ASR artifact"),
    (lambda d: (d.update(status="gated"),
                d["asr"].update(artifact="s3://x/", approved_version="v1")),
     "decode_strategy is still pending_experiment"),
    (lambda d: (d.update(status="gated"),
                d["asr"].update(artifact="s3://x/", approved_version="v1"),
                d["asr"]["decode_strategy"].update(mode="en_token")),
     "no chosen_by_run"),
])
def test_readiness_ladder_blocks_premature_promotion(mutate, expect, tmp_path):
    src = REG / "pidgin.yaml"
    backup = tmp_path / "pidgin.yaml"
    shutil.copy(src, backup)
    try:
        d = yaml.safe_load(src.read_text())
        mutate(d)
        src.write_text(yaml.dump(d, sort_keys=False))
        rc, out = run_registry()
        assert rc == 1, f"promotion should have been blocked\n{out}"
        assert expect in out, f"expected '{expect}'\n{out}"
    finally:
        shutil.copy(backup, src)


def test_manifest_language_check_uses_registry():
    p = subprocess.run([sys.executable, str(VALIDATOR), str(FIX / "good.jsonl"),
                        "--registry", str(REG)], capture_output=True, text=True)
    assert p.returncode == 0
    assert "check 5" not in p.stdout, "language check should no longer be skipped"
