#!/usr/bin/env python3
"""Model-pipeline gate verification (B7 Phase 3). FAILS CLOSED.

The model workflow may open a registry PR only when a committed gate
report proves it: overall PASS is not enough — every language the PR
would bump must itself be PASS in the report, and languages that failed
stay pinned (the §A4 per-language approval mechanism). No report, an
unparseable report, a BLOCKED report or a FAIL anywhere in the requested
set refuses the run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# B6v2 round 6 (Codex serving review): the report semantics and the
# statistical recomputation now live in ONE shared module —
# medzen_model_loader.promotion_check — consumed BOTH by this repo-side
# checker (git/evidence-record authorities) and by the runtime promotion
# bundle verification (deployment-pinned authorities). Two codebases for
# the same gate is how a fabricated all-PASS bundle promoted.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/model-loader"))
from medzen_model_loader.promotion_check import (  # noqa: E402
    PromotionCheckRefusal,
    promotable_languages,
    recompute_statistics as _shared_recompute,
    require_protocol_evidence as _shared_require,
    validate_report_structure,
)


def load_gate_report(path: Path) -> dict:
    if not path.is_file():
        raise PromotionCheckRefusal(f"gate report absent: {path}")
    try:
        report = json.loads(path.read_bytes())
    except Exception as exc:
        raise PromotionCheckRefusal(f"gate report unparseable: {exc}")
    return validate_report_structure(report)


def _protocol_record() -> dict:
    """Resolved via the committed pointer, with BOTH files read from the
    CAPTURED GIT HEAD (Codex review #22: working-tree bytes accepted
    coordinated pointer+protocol edits; an uncontained path was
    accepted). Path containment: the protocol must live under
    platform/decisions/."""
    import subprocess
    root = Path(__file__).resolve().parents[1]

    def show(rel: str) -> bytes:
        if rel.startswith(("/", "..")) or ":" in rel or "/../" in rel:
            raise PromotionCheckRefusal(
                f"protocol path {rel!r} escapes containment")
        completed = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{rel}"],
            capture_output=True)
        if completed.returncode != 0:
            raise PromotionCheckRefusal(
                f"{rel} is not committed at HEAD — the protocol chain "
                "must be committed bytes, not working-tree edits")
        return completed.stdout

    pointer = json.loads(
        show("platform/decisions/CURRENT-PROMOTION-PROTOCOL.json"))
    rel = str(pointer.get("file") or "")
    if not rel.startswith("platform/decisions/"):
        raise PromotionCheckRefusal(
            f"protocol pointer path {rel!r} escapes platform/decisions/")
    body = show(rel)
    import hashlib
    if hashlib.sha256(body).hexdigest() != pointer["sha256"]:
        raise PromotionCheckRefusal(
            "protocol file does not match the committed pointer hash — "
            "refusing a tampered or half-updated protocol")
    record = json.loads(body)
    if record.get("record") != pointer["record"]:
        raise PromotionCheckRefusal(
            "protocol record id does not match the pointer")
    return record


def _authoritative_holdouts_by_language() -> dict[str, set[str]]:
    """Codex review #11: the checker accepted ANY known sealed sha for ANY
    language. The mapping is now language-specific — a report citing
    swahili's holdout for english refuses.

    Codex review #19: the checker had pinned records -001/-002 and never
    learned about successors, so the obsolete 70-row soreva placeholder
    stayed acceptable while the real-speaker pidgin sets were not. Rule
    now: enumerate EVERY B5-TIER2-HOLDOUTS-*.json in ascending order; for
    each language, only the HIGHEST-numbered record covering it counts.
    A superseded record's holdouts drop out of the mapping entirely."""
    root = Path(__file__).resolve().parents[1] / "platform/evidence"
    mapping: dict[str, set[str]] = {}
    per_language: dict[str, dict] = {}
    for record_path in sorted(root.glob("B5-TIER2-HOLDOUTS-*.json")):
        tier2 = json.loads(record_path.read_bytes())
        for language, pools in tier2["pools"].items():
            per_language[language] = {"pools": pools}
    for language, entry in per_language.items():
        for pool in entry["pools"]:
            mapping.setdefault(language, set()).add(
                pool["tier2-sealed"]["sha256"])
    bindings = json.loads(
        (root / "B5-IMMUTABILITY-BINDINGS-2026-001.json").read_bytes())
    mapping.setdefault("kinyarwanda", set()).add(
        bindings["universal_kinyarwanda_holdout"]["universal-sealed"]["sha256"])
    # the v2 sealed half is QUARANTINED (ledger entry 7) and deliberately
    # NOT in this mapping until an owner-approved release
    return mapping


def require_protocol_evidence(report: dict, requested: list[str]) -> None:
    _shared_require(
        report, requested,
        protocol=_protocol_record(),
        holdouts_by_language=_authoritative_holdouts_by_language(),
    )


def recompute_statistics(report: dict, results_dir: Path,
                          requested: list[str]) -> None:
    def rows_bytes(language: str) -> bytes | None:
        rows_path = results_dir / f"{language}.rows.jsonl"
        if not rows_path.is_file():
            return None
        return rows_path.read_bytes()

    _shared_recompute(
        report, requested,
        mandatory=list(_protocol_record().get("mandatory_languages", [])),
        rows_bytes=rows_bytes,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--languages", default="",
                        help="DEPRECATED subset selector — promotion is "
                             "atomic over the mandatory set; empty means "
                             "exactly that set")
    parser.add_argument("--results-dir", type=Path, default=None,
                        help="directory of per-language hash-bound "
                             "<language>.rows.jsonl result files; REQUIRED "
                             "for promotion (recompute gate)")
    args = parser.parse_args()
    try:
        report = load_gate_report(args.gate_report)
        requested = [x.strip() for x in args.languages.split(",") if x.strip()]
        if not requested:
            requested = list(_protocol_record().get("mandatory_languages", []))
        states = promotable_languages(report, requested)
        require_protocol_evidence(report, requested)
        if args.results_dir is None:
            raise PromotionCheckRefusal(
                "--results-dir is required: promotion statistics must be "
                "recomputed from hash-bound rows (Codex review #9)")
        recompute_statistics(report, args.results_dir, requested)
    except PromotionCheckRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "PASS_PROMOTION_CHECK", "languages": states}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
