"""T6 — the suite-baseline gate for omniASR campaign candidates.

The full-evaluation suite's merged per-language table is the ONLY
uncontaminated comparison surface (frozen eval pool, evaluated before any
fine-tuning existed). A fine-tuned candidate re-runs the same eval
machinery on the same pool; this gate then demands, per campaign
language, ALL of:

  asr.suite_improvement_wer   candidate WER strictly better than the
                              suite baseline for that language
  asr.absolute_wer            the frozen A5 registry ceiling
  asr.absolute_cer            the A5 tonal rule (null threshold = the
                              registry's declaration that CER does not
                              gate; enforced via the reviewed
                              _absolute_cer_gate)

Absence never passes: a language missing from either surface refuses.
The B4-era evaluator in b5_gates.py stays frozen; this module composes
its reviewed primitives (resolve_thresholds, _compare,
_absolute_cer_gate) for the omniASR world.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.b5_gates import (
    FailClosedError,
    GateState,
    _absolute_cer_gate,
    _compare,
    _finite_number,
    _gate,
    resolve_thresholds,
)


def _strict_improvement(gate_id: str, candidate_wer: Any, baseline_wer: Any) -> dict:
    """Candidate must be STRICTLY better than the suite baseline. Built in
    the reviewed _gate row shape; _compare deliberately refuses unknown
    operators, and equality is not improvement."""
    if candidate_wer is None:
        return _gate(gate_id, GateState.NOT_EVALUATED,
                     "Required measurement is absent; absence never becomes PASS.",
                     required=True,
                     threshold={"operator": "<", "value": baseline_wer}, evidence=[])
    try:
        measured = _finite_number(candidate_wer, gate_id)
        limit = _finite_number(baseline_wer, f"{gate_id} baseline")
    except FailClosedError as exc:
        return _gate(gate_id, GateState.FAIL, str(exc), required=True,
                     measurement=candidate_wer,
                     threshold={"operator": "<", "value": baseline_wer}, evidence=[])
    passed = measured < limit
    return _gate(gate_id, GateState.PASS if passed else GateState.FAIL,
                 f"Candidate {measured:.6f} {'improves on' if passed else 'does not improve on'} "
                 f"the suite baseline {limit:.6f}.",
                 required=True, measurement=measured,
                 threshold={"operator": "<", "value": limit}, evidence=[])

ROOT = Path(__file__).resolve().parents[1]
BASELINE_CANDIDATE = "omniASR_CTC_1B_v2"
BASELINE_MODE = "unconditioned"


def load_bound_report(path: Path, expected_sha256: str) -> dict[str, Any]:
    body = path.read_bytes()
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected_sha256:
        raise FailClosedError(
            f"{path.name} hashes to {actual[:16]}, binding says "
            f"{expected_sha256[:16]} — the comparison surface moved")
    return json.loads(body)


def _per_language(report: dict[str, Any], candidate: str, mode: str) -> dict[str, dict]:
    table = {}
    for key, entry in report["metrics"]["per_language"].items():
        this_candidate, this_mode, language = key.split("|", 2)
        if this_candidate == candidate and this_mode == mode:
            table[language] = entry
    return table


def evaluate_candidate(
    *,
    suite_report: dict[str, Any],
    candidate_report: dict[str, Any],
    candidate_name: str,
    campaign_languages: list[str],
    candidate_mode: str = BASELINE_MODE,
    root: Path = ROOT,
) -> dict[str, Any]:
    if suite_report.get("coverage", {}).get("status") != "PASS_GAP_FREE_COVERAGE":
        raise FailClosedError("suite baseline report is not gap-free — no honest surface")
    if not campaign_languages:
        raise FailClosedError("empty campaign language list gates nothing")
    baseline = _per_language(suite_report, BASELINE_CANDIDATE, BASELINE_MODE)
    candidate = _per_language(candidate_report, candidate_name, candidate_mode)
    languages: dict[str, Any] = {}
    overall_pass = True
    for alias in sorted(set(campaign_languages)):
        gates: list[dict[str, Any]] = []
        base = baseline.get(alias)
        cand = candidate.get(alias)
        if base is None or base.get("wer") is None:
            raise FailClosedError(
                f"{alias}: no suite baseline row — the language was not in "
                "the evaluated pool; it cannot be gated and must not train "
                "into approved/")
        if cand is None or cand.get("wer") is None:
            gates.append({"gate": "asr.suite_improvement_wer",
                          "state": "NOT_EVALUATED", "required": True,
                          "reason": "candidate produced no row for this "
                                    "language; absence never becomes PASS"})
        else:
            improvement = _strict_improvement(
                "asr.suite_improvement_wer", cand["wer"], base["wer"])
            improvement["baseline_wer"] = base["wer"]
            gates.append(improvement)
            thresholds = resolve_thresholds(alias, root=root)["values"]["asr"]
            gates.append(_compare("asr.absolute_wer", cand["wer"],
                                  thresholds["absolute_wer_max"], "<=", []))
            cer_gate = _absolute_cer_gate("asr.absolute_cer", cand.get("cer"),
                                          thresholds["absolute_cer_max"], [])
            if cer_gate is not None:
                gates.append(cer_gate)
        language_pass = all(g["state"] == "PASS" for g in gates) and bool(gates)
        overall_pass = overall_pass and language_pass
        languages[alias] = {"pass": language_pass, "gates": gates}
    return {
        "record": "B5_SUITE_BASELINE_GATE_REPORT",
        "candidate": candidate_name,
        "candidate_mode": candidate_mode,
        "baseline_surface": f"{BASELINE_CANDIDATE}|{BASELINE_MODE}",
        "campaign_languages": sorted(set(campaign_languages)),
        "languages": languages,
        "status": "PASS_SUITE_BASELINE_GATES" if overall_pass
        else "FAIL_SUITE_BASELINE_GATES",
    }
