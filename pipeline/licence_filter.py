"""Licence-policy gate for training mixes (design record T3). FAILS CLOSED.

The gb1 audit (GB1-EVAL-ISOLATION-AUDIT-2026-001) inventoried four licence
policies in the training zone. Only some permit training a deployable model
today; `sharealike_review` is parked behind the ShareAlike-on-weights legal
review that Base v5 B9 requires, and anything research/non-commercial never
enters a commercial training mix.

Fail-closed rules, mirroring the trainer's allowed_use gate:

  * every row must carry `license_policy` — absence is a refusal, not a pass;
  * an UNKNOWN policy string is a refusal — new licences are admitted by
    updating the explicit lists in code review, never by default;
  * exclusion happens on the eligible pool BEFORE sampling weights are taken
    (the same lesson as the exclusions gate in train_asr.build_mix).
"""

from __future__ import annotations

from typing import Any, Iterable

TRAIN_CLEAR = frozenset({"cc0", "commercial_ok"})
TRAIN_ATTRIBUTION = frozenset({"cc_by_4_0"})
LEGAL_REVIEW_PENDING = frozenset({"sharealike_review"})
NEVER_TRAIN = frozenset({"research_only", "nc", "non_commercial", "noncommercial"})

KNOWN_POLICIES = TRAIN_CLEAR | TRAIN_ATTRIBUTION | LEGAL_REVIEW_PENDING | NEVER_TRAIN

DEFAULT_ALLOWED = TRAIN_CLEAR | TRAIN_ATTRIBUTION


class LicencePolicyRefusal(RuntimeError):
    pass


def filter_training_rows(
    rows: Iterable[dict[str, Any]],
    *,
    allowed: frozenset[str] = DEFAULT_ALLOWED,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Split rows into (eligible, report). Refuses on absent/unknown policies."""
    if not allowed <= KNOWN_POLICIES:
        raise LicencePolicyRefusal(
            f"allowed set names unknown policies: {sorted(allowed - KNOWN_POLICIES)}"
        )
    if allowed & NEVER_TRAIN:
        raise LicencePolicyRefusal(
            "research/non-commercial policies may never be allowed into training"
        )
    eligible: list[dict[str, Any]] = []
    excluded_counts: dict[str, int] = {}
    for index, row in enumerate(rows):
        policy = row.get("license_policy")
        if not isinstance(policy, str) or not policy:
            raise LicencePolicyRefusal(
                f"row {index} carries no license_policy — there is no default-permit"
            )
        if policy not in KNOWN_POLICIES:
            raise LicencePolicyRefusal(
                f"row {index} carries unknown license_policy '{policy}' — admit new "
                f"policies by updating pipeline/licence_filter.py in review"
            )
        if policy in allowed:
            eligible.append(row)
        else:
            excluded_counts[policy] = excluded_counts.get(policy, 0) + 1
    report = {
        "status": "PASS_LICENCE_POLICY_GATE",
        "allowed_policies": sorted(allowed),
        "eligible_rows": len(eligible),
        "excluded_rows_by_policy": dict(sorted(excluded_counts.items())),
        "attribution_required": bool(
            {row.get("license_policy") for row in eligible} & TRAIN_ATTRIBUTION
        ),
    }
    return eligible, report
