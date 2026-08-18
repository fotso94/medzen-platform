"""Driver v2 surgery functions: every transform fails closed on drift.

The repo-mutating steps are exercised in supervised runs (the same
discipline as v1); these tests pin the PURE functions where the
hand-transcription error class lived.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.asr_suite_launch_driver import (  # noqa: E402
    BOUNDARY_FILES,
    bump_boundary_text,
    executor_module_hashes,
    next_auth_document,
    next_bindings_document,
    next_registry_document,
)
from scripts.asr_suite_shard_driver import DriverRefusal  # noqa: E402


# --------------------------------------------------------------------------- #
# boundary bump
# --------------------------------------------------------------------------- #
def test_bump_rewrites_exactly_one_site():
    text = "if attempt not in set(range(1, 45)):\n    raise ValueError\n"
    out = bump_boundary_text(text, 45)
    assert "range(1, 46)" in out and "range(1, 45)" not in out


def test_bump_is_idempotent_when_already_admitted():
    text = "if attempt not in set(range(1, 46)):\n"
    assert bump_boundary_text(text, 45) == text


def test_bump_refuses_jumps():
    with pytest.raises(DriverRefusal, match="one at a time"):
        bump_boundary_text("range(1, 45)", 47)


def test_bump_refuses_wrong_site_count():
    with pytest.raises(DriverRefusal, match="boundary site"):
        bump_boundary_text("range(1, 45) range(1, 45)", 45)
    with pytest.raises(DriverRefusal, match="boundary site"):
        bump_boundary_text("no boundary here", 45)


def test_live_boundary_files_each_carry_exactly_one_site():
    import re
    pattern = re.compile(r"range\(1, \d+\)")
    bounds = set()
    for rel in BOUNDARY_FILES:
        matches = pattern.findall((ROOT / rel).read_text())
        assert len(matches) == 1, f"{rel} carries {len(matches)} sites"
        bounds.add(matches[0])
    assert len(bounds) == 1, f"boundary files disagree: {bounds}"


# --------------------------------------------------------------------------- #
# registry copy-forward
# --------------------------------------------------------------------------- #
def test_registry_successor_id_and_supersedes():
    doc = next_registry_document(
        {"id": "COST-REGISTRY-2026-045", "ceiling_usd": 800},
        attempt=45, suffix="003C", now_utc="2026-08-18T00:00:00Z")
    assert doc["id"] == "COST-REGISTRY-2026-046"
    assert doc["supersedes"] == "COST-REGISTRY-2026-045"
    assert doc["ceiling_usd"] == 800  # untouched fields carry forward
    assert doc["attempt_reservation"]["attempt"] == 45


def test_registry_refuses_underivable_id():
    with pytest.raises(DriverRefusal, match="successor id"):
        next_registry_document({"id": "WEIRD"}, 45, "X", "2026-08-18T00:00:00Z")


# --------------------------------------------------------------------------- #
# bindings copy-forward
# --------------------------------------------------------------------------- #
def test_bindings_surgery_touches_only_existing_sections():
    previous = {"attempts": {"authorized_numbers": [44]}, "pilot_bundle": {}}
    doc = next_bindings_document(
        previous, {"attempts": {"authorized_numbers": [45]}})
    assert doc["attempts"]["authorized_numbers"] == [45]
    assert previous["attempts"]["authorized_numbers"] == [44]  # no aliasing
    with pytest.raises(DriverRefusal, match="unknown section"):
        next_bindings_document(previous, {"brand_new_section": {}})


def test_executor_hashes_match_current_files():
    bindings = json.loads(sorted(
        (ROOT / "platform/manifests").glob(
            "ASR-BASE-MODEL-PILOT-BINDINGS-2026-*.json"))[-1].read_bytes())
    recomputed = executor_module_hashes(bindings["executor_modules"])
    assert set(recomputed) == set(bindings["executor_modules"])


# --------------------------------------------------------------------------- #
# AUTH copy-forward
# --------------------------------------------------------------------------- #
def _auth_previous() -> dict:
    return {"id": "ASR-BASE-MODEL-AWS-AUTH-2026-003B-S1",
            "status": "owner-approved", "attempt": 44}


def test_auth_rebinds_commit_packet_and_dry_run():
    doc = next_auth_document(
        _auth_previous(), attempt=45, head_commit="a" * 40,
        packet_path="platform/decisions/P.md", packet_sha="b" * 64,
        auth_id="ASR-BASE-MODEL-AWS-AUTH-2026-003C",
        auth_path="platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-003C.json",
        dry_run_id="DRY-003C", now_utc="2026-08-18T00:00:00Z")
    assert doc["supersedes"] == "ASR-BASE-MODEL-AWS-AUTH-2026-003B-S1"
    assert doc["reviewed_repository_commit"] == "a" * 40
    assert doc["packet"] == {"path": "platform/decisions/P.md",
                             "sha256": "b" * 64}
    assert doc["attempt"] == 45


def test_auth_refuses_unapproved_ancestor():
    previous = dict(_auth_previous(), status="draft")
    with pytest.raises(DriverRefusal, match="not owner-approved"):
        next_auth_document(
            previous, attempt=45, head_commit="a" * 40,
            packet_path="p", packet_sha="s", auth_id="i", auth_path="p2",
            dry_run_id="d", now_utc="2026-08-18T00:00:00Z")
