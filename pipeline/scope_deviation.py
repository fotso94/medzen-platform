"""Executable binding for the owner-approved simplified B4 exit.

The deviation is deliberately code-visible: unavailable-data requirements are
reported as NOT_EVALUATED or DEFERRED, never silently omitted or marked pass.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DECISION = ROOT / "platform/decisions/B4-SCOPE-2026-002-simplified-exit.json"
GATE_MATRIX = (
    ROOT / "platform/decisions/A5-2026-001-simplified-b4-gate-matrix.json"
)
EXPECTED_TRAINING = (
    "hausa", "igbo", "lingala", "luganda", "oromo",
    "pidgin", "swahili", "yoruba",
)
EXPECTED_SELECTION = ("lingala", "luganda", "oromo")
EXPECTED_DEFERRED = ("acholi", "akan", "amharic", "ewe", "fula", "shona")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load() -> tuple[dict, str, dict, str]:
    decision = json.loads(DECISION.read_bytes())
    gates = json.loads(GATE_MATRIX.read_bytes())
    problems: list[str] = []
    if decision.get("record") != "B4-OWNER-APPROVED-SCOPE-DEVIATION":
        problems.append("scope-deviation record type differs")
    if (decision.get("id"), decision.get("revision"), decision.get("status")) \
            != ("B4-SCOPE-2026-002", 1, "approved"):
        problems.append("scope-deviation identity/revision/status differs")
    scope = decision.get("language_scope") or {}
    if tuple(scope.get("active_training") or ()) != EXPECTED_TRAINING:
        problems.append("active training scope is not the approved eight")
    if tuple(scope.get("checkpoint_selection") or ()) != EXPECTED_SELECTION:
        problems.append("checkpoint-selection scope differs")
    if tuple(scope.get("deferred") or ()) != EXPECTED_DEFERRED:
        problems.append("deferred scope differs")
    if "lingala" not in scope.get("active_training", []):
        problems.append("Lingala was silently removed")
    if decision.get("data_scope_deviations", {}).get(
            "code_switch", {}).get("decision") != "NOT_EVALUATED":
        problems.append("code-switch is not explicitly NOT_EVALUATED")
    replay = decision.get("data_scope_deviations", {}).get(
        "english_french_replay", {})
    if replay.get("decision") != "NOT_EVALUATED":
        problems.append("English/French replay is not NOT_EVALUATED")
    if "not protected" not in str(replay.get("consequence", "")).lower():
        problems.append("English/French forgetting consequence is absent")
    hard = decision.get("hard_controls") or {}
    for name in ("model_registration_permitted", "promotion_permitted",
                 "b5_permitted", "eks_permitted",
                 "silent_gate_drop_permitted",
                 "checkpoint_500_from_the_failed_campaign_reusable"):
        if hard.get(name) is not False:
            problems.append(f"{name} must be false")
    budget = decision.get("budget") or {}
    if budget.get("aggregate_ceiling_usd") != 100.0:
        problems.append("aggregate budget ceiling is not $100")
    if budget.get("aggregate_committed_at_authorization_usd") != 16.8738:
        problems.append("historical aggregate spend is not $16.8738")

    if gates.get("record") != "A5-B4-GATE-DISPOSITION":
        problems.append("A5 matrix record type differs")
    rows = gates.get("gates") or []
    if len(rows) != 25:
        problems.append(f"A5 matrix names {len(rows)} gates, expected 25")
    allowed = {"EVALUATED_ACTIVE_SLICES", "NOT_EVALUATED", "DEFERRED"}
    for index, row in enumerate(rows):
        if row.get("status") not in allowed:
            problems.append(
                f"A5 row {index} has unrecognised status {row.get('status')!r}")
        if row.get("status") != "EVALUATED_ACTIVE_SLICES" and not row.get("reason"):
            problems.append(f"A5 row {index} defers without a reason")
    if problems:
        raise SystemExit(
            "REFUSING: simplified B4 scope deviation is unusable -\n  "
            + "\n  ".join(problems))
    return decision, _sha(DECISION), gates, _sha(GATE_MATRIX)


DECISION_DOC, DECISION_SHA256, A5_GATES, A5_GATES_SHA256 = load()


def gate_disposition() -> list[dict]:
    """A copy suitable for immutable campaign records."""
    return [dict(row) for row in A5_GATES["gates"]]
