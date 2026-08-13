from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "platform/finance/COST-REGISTRY-2026-012.json"
RECONCILIATION = ROOT / (
    "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-002.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def test_revision_12_is_non_destructive_and_arithmetically_exact() -> None:
    value = load(REGISTRY)
    assert value["supersedes"] == {
        "path": "platform/finance/COST-REGISTRY-2026-011.json",
        "sha256": "4a66fec2362c62c29021cd253695c979fb0ea0e20dcf08587bed89e1765bb4b9",
    }
    summary = value["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == 134.4286064216
    assert summary["active_reservations_usd"] == 0
    assert summary["committed_plus_reserved_usd"] == 134.4286064216
    assert summary["guardrail_headroom_after_reservations_usd"] == (
        300 - summary["committed_plus_reserved_usd"]
    )
    assert value["reconciliation"]["sha256"] == sha(RECONCILIATION)


def test_actual_pool_keeps_gross_credit_net_and_no_fabricated_split() -> None:
    value = load(RECONCILIATION)
    actual = value["cost_explorer_actual_observation"]
    assert actual["usage_type"] == "EUC1-BoxUsage:g6.xlarge"
    assert actual["usage_quantity_hours"] == 0.031389
    assert actual["usage_quantity_seconds"] == 113.0004
    assert actual["gross_usage_unblended_usd"] == 0.0315898896
    assert actual["credit_unblended_usd"] == -0.0315898896
    assert actual["net_unblended_usd"] == 0
    assert value["attribution"]["per_attempt_dollar_allocation"] == (
        "NOT_AVAILABLE"
    )
    assert value["attribution"]["credits_used_to_expand_budget_headroom"] is False


def test_receipt_runtime_cross_check_covers_attempts_11_through_16() -> None:
    value = load(RECONCILIATION)["receipt_runtime_cross_check"]
    assert [item["attempt"] for item in value["attempts"]] == list(range(11, 17))
    assert sum(item["observed_seconds"] for item in value["attempts"]) == 1383
    assert value["total_observed_seconds"] == 1383
    assert value["receipt_derived_dollar_estimate_reported_as_actual"] is False
    for item in value["attempts"]:
        path = ROOT / item["evidence_path"]
        assert path.is_file()
        assert sha(path) == item["evidence_sha256"]


def test_all_six_prior_observations_remain_immutable() -> None:
    expected = {
        11: "77146f2eda71f69495b0d5baa65d50a487b9bb73cf5f0a9355e405a3b9f012c8",
        12: "c377b2a4193fb037339026993c9330a1974cf1d03606d5a45a29131ef9051707",
        13: "212f9ebd10b48f7a78d06a76f77a62ad078980eec0c6770e49a645aa49b906bc",
        14: "671ee8a7282dc2beda14446ae96f726cbc581ee765bfc5bc990cdac210addfa2",
        15: "2f009d1fbd5f68e306ddc92489825a10040938b4bc8c906e1f4b2dd7fbb7aeea",
        16: "266063399cfba297d1cc01fa538e3380c24b51bf0df95a6734dd9e7e8d3ec92f",
    }
    for attempt, digest in expected.items():
        path = ROOT / (
            f"platform/evidence/ASR-BASE-MODEL-ATTEMPT-{attempt}-"
            "COST-OBSERVATION-2026-001.json"
        )
        assert sha(path) == digest
