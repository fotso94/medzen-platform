"""B6v2 round 10 (Codex): the provider-call budget must be atomic AND
owner-bound — same-id races, minted ids and caller-raised caps all
refuse."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "b6v2_qualification_harness",
    ROOT / "scripts/b6v2_qualification_harness.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def _clean(qualification_id: str) -> None:
    directory = ROOT / "platform/evidence/receipts" / qualification_id
    if directory.is_dir():
        for lock in directory.glob("*.lock"):
            lock.unlink()
        for extra in directory.iterdir():
            if extra.is_file():
                extra.unlink()
        directory.rmdir()


def test_unknown_qualification_id_refuses():
    with pytest.raises(SystemExit, match="not\\s+in the owner-approved"):
        harness.CallLedger("MINTED-BUDGET-9999", "bedrock")


def test_unknown_leg_refuses():
    with pytest.raises(SystemExit, match="no owner-approved budget"):
        harness.CallLedger("B6V2-QUAL-2026-002", "nonexistent-leg")


def test_cap_comes_from_the_committed_packet_and_is_atomic():
    qualification_id = "B6V2-QUAL-2026-002"
    _clean(qualification_id)
    try:
        ledger = harness.CallLedger(qualification_id, "bedrock")
        assert ledger.max_calls == 1, (
            "the cap is the OWNER-approved committed value")

        def attempt(_):
            try:
                return harness.CallLedger(
                    qualification_id, "bedrock").reserve()
            except SystemExit:
                return None

        with concurrent.futures.ThreadPoolExecutor(20) as pool:
            outcomes = list(pool.map(attempt, range(20)))
        winners = [slot for slot in outcomes if slot is not None]
        assert winners == [1], (
            f"EXACTLY one reservation may win a cap of 1, got {winners}")
        # and a rerun after the winner refuses too
        with pytest.raises(SystemExit, match="cap is enforced"):
            harness.CallLedger(qualification_id, "bedrock").reserve()
    finally:
        _clean(qualification_id)
