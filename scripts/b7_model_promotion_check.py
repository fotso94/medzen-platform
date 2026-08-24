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
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/model-loader"))
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


def _grade_authority() -> dict[str, dict]:
    """Round 10 (Codex): full authority entries (grade + caveat), never
    a bare grade string, from the committed machine-readable authority."""
    root = Path(__file__).resolve().parents[1]
    authority = json.loads(
        (root / "platform/decisions/HOLDOUT-GRADES-2026-001.json"
         ).read_bytes())
    return dict(authority["grades"])


def _licensed_code_switch_sets() -> dict[str, dict]:
    root = Path(__file__).resolve().parents[1]
    authority = json.loads(
        (root / "platform/decisions/HOLDOUT-GRADES-2026-001.json"
         ).read_bytes())
    return dict(authority.get("licensed_code_switch_sets", {}))


def _anchor_fetch(storage):
    """Round 9 (Codex): git commit timestamps are AUTHOR-controlled and
    prove nothing about chronology. The only anchor authority is S3
    (LastModified set by the storage system) — same implementation the
    runtime uses."""
    from medzen_model_loader.loader_v2 import _s3_anchor_fetch
    return _s3_anchor_fetch(storage)


def _sealed_start_fetch(job):
    from medzen_model_loader.loader_v2 import _sagemaker_sealed_start_fetch
    return _sagemaker_sealed_start_fetch(job)


def _output_object_fetch(s3_uri, version_id):
    # round 13: the sealed job's own versioned output objects
    from medzen_model_loader.loader_v2 import _s3_output_fetch
    return _s3_output_fetch(s3_uri, version_id)


def _output_writer_fetch(s3_uri, version_id):
    # round 15 (Codex finding 1): who WROTE the object — the CloudTrail
    # PutObject principal (data events) + the object's Object-Lock state.
    # Admission-only (needs cloudtrail:LookupEvents + s3:GetObjectRetention).
    from medzen_model_loader.loader_v2 import _s3_output_writer
    return _s3_output_writer(s3_uri, version_id)


def _document_bytes(path):
    # round 13: licence/reservation documents resolve against the
    # COMMITTED repository tree, never an author-supplied directory
    candidate = (ROOT / str(path).lstrip("/")).resolve()
    if ROOT not in candidate.parents or not candidate.is_file():
        return None
    return candidate.read_bytes()


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
    parser.add_argument("--candidate-packet", type=Path, default=None,
                        help="the PREDECLARED candidate packet (round 8: "
                             "REQUIRED — thresholds before sealed "
                             "observation)")
    parser.add_argument("--manifests-dir", type=Path, default=None,
                        help="directory of authoritative "
                             "<language>.holdout-manifest.jsonl sealed "
                             "manifests (round 8: REQUIRED)")
    parser.add_argument("--artifact-tree", default=None,
                        help="the candidate's full 64-hex artifact tree "
                             "digest (round 8: REQUIRED)")
    parser.add_argument("--write-admission-receipt", type=Path,
                        default=None,
                        help="write the attested chronology receipt the "
                             "runtime bundle carries (round 10)")
    parser.add_argument("--anchor-envelope", type=Path, default=None,
                        help="the SEPARATE anchor envelope naming the "
                             "packet sha + S3 storage coordinates "
                             "(round 9: REQUIRED — a packet cannot "
                             "contain its own storage identity)")
    args = parser.parse_args()
    try:
        report = load_gate_report(args.gate_report)
        for flag, value in (("--results-dir", args.results_dir),
                             ("--candidate-packet", args.candidate_packet),
                             ("--manifests-dir", args.manifests_dir),
                             ("--artifact-tree", args.artifact_tree),
                             ("--anchor-envelope", args.anchor_envelope)):
            if value is None:
                raise PromotionCheckRefusal(
                    f"{flag} is required: the promotion gate is COMPLETE "
                    "or it is nothing (Codex serving review round 8)")
        packet_bytes = args.candidate_packet.read_bytes()

        def rows_bytes(language):
            path = args.results_dir / f"{language}.rows.jsonl"
            return path.read_bytes() if path.is_file() else None

        def manifest_bytes(language):
            path = args.manifests_dir / f"{language}.holdout-manifest.jsonl"
            return path.read_bytes() if path.is_file() else None

        from medzen_model_loader.promotion_check import (
            verify_complete_promotion,
            verify_packet_chronology,
        )
        packet = json.loads(packet_bytes)
        envelope = json.loads(args.anchor_envelope.read_bytes())

        def verify_chronology() -> None:
            # the ADMISSION side runs the LIVE AWS verification; the
            # returned material is the attested receipt the runtime
            # verifies offline (round 10: the loader role has no AWS)
            receipt = verify_packet_chronology(
                report, anchor_envelope=envelope,
                packet_bytes=packet_bytes, candidate_packet=packet,
                anchor_fetch=_anchor_fetch,
                sealed_start_fetch=_sealed_start_fetch,
                artifact_tree_sha256=str(args.artifact_tree),
                rows_bytes=rows_bytes,
                output_object_fetch=_output_object_fetch,
                output_writer_fetch=_output_writer_fetch)
            if args.write_admission_receipt is not None:
                args.write_admission_receipt.write_text(
                    json.dumps(receipt, indent=1, sort_keys=True) + "\n")

        states = verify_complete_promotion(
            report,
            protocol=_protocol_record(),
            holdouts_by_language=_authoritative_holdouts_by_language(),
            grade_authority=_grade_authority(),
            licensed_code_switch_sets=_licensed_code_switch_sets(),
            candidate_packet=packet,
            packet_bytes=packet_bytes,
            anchor_envelope=envelope,
            artifact_tree_sha256=str(args.artifact_tree),
            rows_bytes=rows_bytes,
            manifest_bytes=manifest_bytes,
            verify_chronology=verify_chronology,
            document_bytes=_document_bytes,
        )
    except PromotionCheckRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "PASS_PROMOTION_CHECK", "languages": states}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
