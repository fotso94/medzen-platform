from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "platform/decisions/B6A-BUDGET-2026-001-aggregate-300.json"
NEW = ROOT / "platform/decisions/B6A-BUDGET-2026-002-conservative-reconciliation.json"


def test_historical_budget_record_is_unchanged_and_bound():
    old_hash = hashlib.sha256(OLD.read_bytes()).hexdigest()
    decision = json.loads(NEW.read_text())
    assert old_hash == "d50334d650d90fc7623172bff9a051040a95192736b247e3cad3f3d235e8d91d"
    assert decision["supersedes_prospectively"]["sha256"] == old_hash
    assert decision["supersedes_prospectively"]["historical_record_edited"] is False


def test_budget_math_and_authorization_boundary():
    decision = json.loads(NEW.read_text())
    budget = decision["budget_usd"]
    assert budget["committed_total"] == pytest.approx(
        budget["historical_b4_committed"]
        + budget["b6a_eks_upgrade_conservative_committed"]
    )
    assert budget["committed_plus_reserved"] == pytest.approx(
        budget["committed_total"] + budget["packet_2026_003_reserved"]
    )
    assert budget["remaining_after_reservation"] == pytest.approx(
        budget["aggregate_ceiling"] - budget["committed_plus_reserved"]
    )
    assert decision["reservation_state"]["unresolved_prior_reservations"] == 0
    assert decision["reservation_state"]["packet_2026_003"] == "RESERVED_NOT_AUTHORIZED"
    assert decision["controls"]["reservation_is_aws_authorization"] is False
