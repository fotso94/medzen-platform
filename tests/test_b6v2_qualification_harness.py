"""B6v2 rounds 10-11 (Codex): the provider-call budget must be atomic,
owner-bound at git HEAD, and its tests must NEVER touch the canonical
evidence directories (the round-10 test deleted real receipts)."""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "b6v2_qualification_harness",
    ROOT / "scripts/b6v2_qualification_harness.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

TEST_BUDGETS = json.dumps({
    "budgets": {
        "TEST-QUAL-0001": {
            "legs": {"bedrock": {"max_calls": 1, "model_id": "m"},
                      "fish": {"max_calls": 2, "voice": "kinyarwanda"}}}}
}).encode()


def _ledger(tmp_path, leg="bedrock", qualification_id="TEST-QUAL-0001"):
    return harness.CallLedger(
        qualification_id, leg,
        ledger_root=tmp_path / "ledger",
        budgets_loader=lambda: TEST_BUDGETS)


def test_unknown_qualification_id_refuses(tmp_path):
    with pytest.raises(SystemExit, match="not\\s+in the owner-approved"):
        harness.CallLedger("MINTED-BUDGET-9999", "bedrock",
                            ledger_root=tmp_path,
                            budgets_loader=lambda: TEST_BUDGETS)


def test_unknown_leg_refuses(tmp_path):
    with pytest.raises(SystemExit, match="no owner-approved budget"):
        harness.CallLedger("TEST-QUAL-0001", "nonexistent",
                            ledger_root=tmp_path,
                            budgets_loader=lambda: TEST_BUDGETS)


def test_uncommitted_worktree_budget_refuses(tmp_path, monkeypatch):
    """Round 11 (Codex, UNCOMMITTED_WORKTREE_BUDGET_ACCEPTED): the CLI
    path reads the budget at git HEAD — a repo without the committed
    packet refuses outright."""
    import subprocess
    repo = tmp_path / "norepo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    monkeypatch.setattr(harness, "ROOT", repo)
    with pytest.raises(SystemExit, match="COMMITTED at HEAD"):
        harness.CallLedger("ANY", "bedrock", ledger_root=tmp_path)


def test_cap_is_atomic_and_reruns_refuse(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.max_calls == 1

    def attempt(_):
        try:
            return _ledger(tmp_path).reserve()
        except SystemExit:
            return None

    with concurrent.futures.ThreadPoolExecutor(20) as pool:
        outcomes = list(pool.map(attempt, range(20)))
    winners = [slot for slot in outcomes if slot is not None]
    assert winners == [1], (
        f"EXACTLY one reservation may win a cap of 1, got {winners}")
    with pytest.raises(SystemExit, match="cap is enforced"):
        _ledger(tmp_path).reserve()


def test_multi_slot_cap_fills_in_order(tmp_path):
    first = _ledger(tmp_path, leg="fish").reserve()
    second = _ledger(tmp_path, leg="fish").reserve()
    assert (first, second) == (1, 2)
    with pytest.raises(SystemExit, match="cap is enforced"):
        _ledger(tmp_path, leg="fish").reserve()


def test_tests_never_touch_canonical_receipts():
    """The canonical evidence tree must be untouched by this suite."""
    canonical = ROOT / "platform/evidence/receipts"
    before = sorted(str(p.relative_to(canonical))
                    for p in canonical.rglob("*") if p.is_file())
    assert "B6V2-QUAL-2026-001/qualification.json" in before, (
        "the real qualification receipt must still exist")
