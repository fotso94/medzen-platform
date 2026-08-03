#!/usr/bin/env python3
"""Validate a completed review and emit a checksum-bound approved decision.

Refuses on: unreviewed entries, invalid classification/action pairs, an
over-limit row marked retain, missing reviewer role or timestamp, bindings that
no longer match the artefacts they were taken from, and self-approval.

The last one matters most. A reviewer classifying rows and then approving their
own classifications is one person, not two, and the approval adds nothing. The
approver must be a different role, recorded as a role rather than an identity.

The output is the input to training: pipeline/train_asr.py --exclusions reads
it, refuses an unapproved list, and refuses to start if any over-limit row is
absent from it.

    python scripts/finalize_label_review.py --approver-role ml-lead
    python scripts/finalize_label_review.py --approver-role ml-lead --upload
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import review_bindings as RB  # noqa: E402

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "platform/decisions/DQ-2026-001-label-review.json"
APPROVED = ROOT / "platform/decisions/DQ-2026-001-label-review.approved.json"

REASON_FOR = {
    "confirmed_data_defect": {"a", "b", "c"},          # mismatch / truncation / coverage
    "valid_but_decoder_incompatible": {"d"},           # correct but over the limit
    "valid_under_limit": {"e"},                        # correct and within the limit
    "uncertain": {"f"},                                # cannot determine
}

VALID = {
    "confirmed_data_defect": "exclude",
    "valid_but_decoder_incompatible": "defer_for_segmentation",
    "valid_under_limit": "retain",
    "uncertain": "defer_pending_review",
}
EXCLUDING_ACTIONS = {"exclude", "defer_for_segmentation", "defer_pending_review"}


def client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approver-role", required=True,
                    help="ROLE of the approver; must differ from every reviewer role")
    ap.add_argument("--attest-independent", action="store_true",
                    help="the approver attests to being a DIFFERENT PERSON from every "
                         "reviewer. This tool compares role strings; it cannot verify "
                         "people, so the claim is recorded as an attestation.")
    ap.add_argument("--no-independent-approval", metavar="REASON",
                    help="record that independent approval was NOT available, with a "
                         "reason. The decision is still produced, but it says so.")
    ap.add_argument("--upload", action="store_true")
    a = ap.parse_args()

    if "@" in a.approver_role:
        raise SystemExit("REFUSING: approver-role must be a role, not an identity")

    draft_raw = DRAFT.read_bytes()
    doc = json.loads(draft_raw)
    problems: list[str] = []

    # ---- RECOMPUTE every binding; never trust the copies in the draft ------
    cli = client()
    b = RB.recompute(cli)
    problems += RB.verify(b, expect={
        "audit_sha256": doc["bindings"]["audit_file_sha256"],
        "complete_sha256": doc["bindings"]["v2_complete_record_sha256"],
        "tokenizer_cache_manifest_sha256":
            doc["bindings"]["tokenizer_cache_manifest_sha256"],
    })

    # ---- the entries must be exactly the audit's 6 + 14, unaltered ---------
    audit = b["_audit"]
    # Derive the trigger from WHICH AUDIT ARRAY a row came from. Reading it off
    # the entry would let an edit reclassify an under-limit row as over-limit and
    # unlock the wrong set of classifications.
    expected = {}
    for r in audit["over_limit_rows"]:
        expected[r["audio_checksum_sha256"]] = {**r, "_trigger": "over_decoder_limit"}
    for r in audit["rate_outlier_rows"]:
        expected[r["audio_checksum_sha256"]] = {**r,
                                                "_trigger": "extreme_token_rate_under_limit"}
    got = {e["audio_checksum_sha256"]: e for e in doc["entries"]}
    if len(doc["entries"]) != len(got):
        problems.append("duplicate checksums among the entries")
    if set(got) != set(expected):
        missing = sorted(set(expected) - set(got))
        extra = sorted(set(got) - set(expected))
        if missing:
            problems.append(f"{len(missing)} audit row(s) absent from the decision")
        if extra:
            problems.append(f"{len(extra)} entr(y/ies) not present in the audit")
    n_over = sum(1 for e in doc["entries"] if e["trigger"] == "over_decoder_limit")
    n_out = len(doc["entries"]) - n_over
    if (n_over, n_out) != (audit["rows_over_limit"], audit["rate_outliers_under_limit"]):
        problems.append(f"entry mix {n_over}+{n_out} != audit "
                        f"{audit['rows_over_limit']}+{audit['rate_outliers_under_limit']}")
    for cs, e in got.items():
        exp = expected.get(cs)
        if not exp:
            continue
        # Every field that drives a review decision is compared, not just the
        # obvious ones: tokens_per_s and z_score are what a reviewer weighs, and
        # trigger decides which classifications are even offered.
        for f in ("label_tokens_effective", "duration_s", "language", "task",
                  "tokens_per_s", "z_score"):
            if e.get(f) != exp.get(f):
                problems.append(f"{cs[:16]}: {f} altered ({e.get(f)!r} != {exp.get(f)!r})")
        if e.get("trigger") != exp["_trigger"]:
            problems.append(f"{cs[:16]}: trigger {e.get('trigger')!r} does not match the "
                            f"audit array it came from ({exp['_trigger']!r})")

    # ---- every entry reviewed, and reviewed coherently ---------------------
    roles: set[str] = set()
    for e in doc["entries"]:
        cs = e["audio_checksum_sha256"][:16]
        cls, act = e["classification"], e["action"]
        if cls is None:
            problems.append(f"{cs}: not reviewed")
            continue
        if cls not in VALID:
            problems.append(f"{cs}: unknown classification {cls!r}")
            continue
        if act != VALID[cls]:
            problems.append(f"{cs}: action {act!r} does not match classification {cls!r} "
                            f"(expected {VALID[cls]!r})")
        # Compatibility BOTH ways, checked against the audit-derived trigger.
        trig = expected.get(e["audio_checksum_sha256"], {}).get("_trigger", e["trigger"])
        if trig == "over_decoder_limit":
            if act == "retain":
                problems.append(f"{cs}: an over-limit row cannot be retained")
            if cls == "valid_under_limit":
                problems.append(f"{cs}: valid_under_limit on an over-limit row")
        else:
            if cls == "valid_but_decoder_incompatible":
                problems.append(f"{cs}: valid_but_decoder_incompatible on a row the "
                                f"decoder accepts ({e.get('label_tokens_effective')} "
                                f"<= {b['_audit']['label_limit']} tokens)")
        if not e.get("reviewer_role"):
            problems.append(f"{cs}: no reviewer_role")
        else:
            roles.add(e["reviewer_role"])
        if not e.get("reviewed_utc"):
            problems.append(f"{cs}: no reviewed_utc")
        if not e.get("reason_code"):
            problems.append(f"{cs}: no reason_code")
        elif cls in REASON_FOR and e["reason_code"] not in REASON_FOR[cls]:
            problems.append(f"{cs}: reason {e['reason_code']!r} is not compatible with "
                            f"{cls} (allowed: {sorted(REASON_FOR[cls])})")
        if not e.get("listened"):
            problems.append(f"{cs}: not marked as listened; classification requires "
                            f"listening, not metrics")

    # ---- separation of duties ---------------------------------------------
    # A different role string is NOT a different person. This tool cannot tell
    # them apart, so it refuses to imply independence that nobody asserted: the
    # approver either attests to being a different person, or records that
    # independent approval was unavailable and why. Silence is not an option.
    if a.approver_role in roles:
        problems.append(f"self-approval: approver role {a.approver_role!r} also reviewed "
                        f"entries. Approval by the same role adds no independent check.")
    if a.attest_independent and a.no_independent_approval:
        problems.append("--attest-independent and --no-independent-approval are "
                        "contradictory")
    if not a.attest_independent and not a.no_independent_approval:
        problems.append(
            "independence not stated. Different role strings do not prove different "
            "people, and this tool cannot verify who is at the keyboard.\n"
            "    Pass --attest-independent if the approver is genuinely a different "
            "authorised person,\n"
            "    or --no-independent-approval '<reason>' to record that it was not "
            "available.")

    if problems:
        print(f"REFUSING — {len(problems)} problem(s):")
        for p in problems[:30]:
            print(f"  {p}")
        return 1

    excl = [e for e in doc["entries"] if e["action"] in EXCLUDING_ACTIONS]
    retained = [e for e in doc["entries"] if e["action"] == "retain"]
    by_class: dict[str, int] = {}
    for e in doc["entries"]:
        by_class[e["classification"]] = by_class.get(e["classification"], 0) + 1

    out = {
        "list_id": "DQ-2026-001-label-review",
        "status": "approved",
        "approved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "approver_role": a.approver_role,
        "reviewer_roles": sorted(roles),
        "independence": {
            "independent_approval": bool(a.attest_independent),
            "basis": ("attested by the approver: a different authorised person from "
                      "every reviewer" if a.attest_independent
                      else a.no_independent_approval),
            "enforcement": ("ROLE STRINGS ONLY. This process compares role names and "
                            "cannot verify that two different people were involved. "
                            "Treat the flag above as a human attestation, not a "
                            "technical control."),
        },
        "bindings": doc["bindings"],
        # The exact draft these classifications came from. Without it, an
        # approved record could be paired with a different draft afterwards.
        "approved_draft_sha256": hashlib.sha256(draft_raw).hexdigest(),
        "bindings_recomputed": {k: v for k, v in b.items() if not k.startswith("_")},
        "summary": {"entries": len(doc["entries"]), "excluded": len(excl),
                    "retained": len(retained), "by_classification": by_class},
        "content_policy": ("checksums only; no transcript, speaker, session, path or "
                           "content excerpt appears in this file"),
        "exclusions": [
            {"audio_checksum_sha256": e["audio_checksum_sha256"],
             "language": e["language"], "task": e["task"],
             "category": e["classification"], "action": e["action"],
             "reason_code": e["reason_code"],
             "reviewer_role": e["reviewer_role"], "reviewed_utc": e["reviewed_utc"],
             "defect": e["classification"] == "confirmed_data_defect"}
            for e in excl
        ],
        "retained": [
            {"audio_checksum_sha256": e["audio_checksum_sha256"],
             "classification": e["classification"], "reason_code": e["reason_code"]}
            for e in retained
        ],
        "note": ("valid_but_decoder_incompatible rows are NOT defects. They are correct "
                 "data excluded from this run only, pending token-aware, "
                 "alignment-preserving segmentation at ingest."),
    }
    APPROVED.write_text(json.dumps(out, indent=2) + "\n")
    print(f"approved: {APPROVED}")
    print(f"  entries {len(doc['entries'])} | excluded {len(excl)} | retained {len(retained)}")
    for k, v in sorted(by_class.items()):
        print(f"    {k:<32} {v}")
    print(f"  reviewer roles: {sorted(roles)}   approver: {a.approver_role}")
    if a.upload:
        k = f"evidence/decisions/{out['list_id']}-{out['approved_utc'].replace(':', '')}.json"
        cli.put_object(Bucket=BUCKET, Key=k,
                       Body=(json.dumps(out, indent=2) + "\n").encode())
        print(f"  stored s3://{BUCKET}/{k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
