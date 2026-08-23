#!/usr/bin/env python3
"""Generate the machine-readable sealed-holdout GRADE authority.

B6v2 round 9 (Codex): the repo-side checker defaulted every recognized
holdout to promotion_grade — contradicting PROMOTION-PROTOCOL-2026-004's
holdout_grades, under which ONLY kinyarwanda's universal sealed set is
promotion-grade, real-speaker tier2 pools (french aaf, pidgin
av-heldout) are CONDITIONAL with disclosed caveats, and every
placeholder-speaker FLEURS/soreva pool is development-grade only.

The rule is derived from the records' own metadata, never asserted:
  - the kinyarwanda universal-sealed identity  -> promotion_grade
  - pool method 'speaker_disjoint...' (real)   -> conditional
  - pool method containing 'placeholder'       -> development_grade_only
Supersession mirrors the checker: per language, the HIGHEST-numbered
tier2 record wins. Output: platform/decisions/HOLDOUT-GRADES-2026-001.json
(tests/test_b6v2_serving_language_and_loader.py refuses drift).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "platform/decisions/HOLDOUT-GRADES-2026-001.json"


def derive() -> dict[str, dict[str, str]]:
    per_language: dict[str, list] = {}
    for record_path in sorted(
        (ROOT / "platform/evidence").glob("B5-TIER2-HOLDOUTS-*.json")
    ):
        record = json.loads(record_path.read_bytes())
        for language, pools in record["pools"].items():
            per_language[language] = pools
    grades: dict[str, dict[str, str]] = {}
    for language, pools in per_language.items():
        for pool in pools:
            sha = pool["tier2-sealed"]["sha256"]
            method = str(pool.get("method", ""))
            if "placeholder" in method:
                grade = "development_grade_only"
            elif method.startswith("speaker_disjoint"):
                grade = "conditional"
            else:
                raise SystemExit(
                    f"{language}: pool method {method!r} matches no grade "
                    "rule — extend the rule under review, never guess")
            entry = {"language": language, "grade": grade,
                      "pool": str(pool.get("pool", ""))}
            if grade == "conditional":
                # round 10 (Codex): the caveat acknowledgement must match
                # the AUTHORITY's disclosed caveat — derived from the
                # record's own overlap data, never a free-form string
                overlap_text = None
                for key, value in pool.items():
                    if key.startswith("sealed_vs_") and "text" in str(value):
                        overlap = value if isinstance(value, dict) else {}
                        overlap_text = overlap.get("text")
                if overlap_text is None:
                    raise SystemExit(
                        f"{language}: conditional pool discloses no text "
                        "overlap — extend the caveat rule under review")
                entry["caveat"] = (
                    f"disclosed_text_overlap_rows:{overlap_text}")
            grades[sha] = entry
    bindings = json.loads(
        (ROOT / "platform/evidence/"
         "B5-IMMUTABILITY-BINDINGS-2026-001.json").read_bytes())
    universal = bindings["universal_kinyarwanda_holdout"]["universal-sealed"]
    grades[universal["sha256"]] = {
        "language": "kinyarwanda", "grade": "promotion_grade",
        "pool": "cv17-test-v1-universal-sealed"}
    return grades


def licensed_code_switch_sets() -> dict[str, dict]:
    """Round 10 (Codex): code-switch evidence must resolve to a
    LICENSED, reserved, speaker-disjoint holdout authority. No such set
    exists yet — the acquisition is on the owner critical path
    (PROMOTION-PROTOCOL-2026-004 gates_phased) — so this registry is
    EMPTY and production promotion is structurally impossible until a
    licensed set is registered here under review."""
    return {}


def main() -> int:
    document = {
        "record": "HOLDOUT-GRADES-2026-001",
        "authority": "PROMOTION-PROTOCOL-2026-004 holdout_grades, derived "
                      "from the tier2 records' own pool metadata by "
                      "scripts/generate_holdout_grades.py",
        "grades": derive(),
        "licensed_code_switch_sets": licensed_code_switch_sets(),
    }
    OUTPUT.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for entry in document["grades"].values():
        counts[entry["grade"]] = counts.get(entry["grade"], 0) + 1
    print(f"wrote {OUTPUT.name}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
