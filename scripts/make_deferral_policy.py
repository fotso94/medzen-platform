#!/usr/bin/env python3
"""Emit a POLICY deferral record for the 20 flagged rows. Not a review.

The distinction this file exists to preserve: nobody looked at these rows. Six
exceed the decoder limit and fourteen are statistical token-rate outliers, and
what any of them actually contain is unknown. A conservative policy defers all
twenty from one experiment; it does not decide they are defective, because that
decision requires a human listening to audio and none has.

So every entry here is classified `unreviewed_anomaly_deferred_by_policy` with
`defect: false`, and the record carries `human_review_performed: false` at the
top level where it cannot be missed. The scope is one experiment. Promotion,
distribution and reuse of these rows stay blocked until a human reviews them.

Refuses to run if DQ-2026-001 has been classified -- if real review exists, it
must be finalised through the reviewer/approver path, not restated as policy.

    python scripts/make_deferral_policy.py --authorized-by-role platform-owner
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

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "platform/decisions/DQ-2026-001-label-review.json"
OUT = ROOT / "platform/decisions/DQ-2026-002-policy-deferral.json"

LIST_ID = "DQ-2026-002-policy-deferral"
CLASSIFICATION = "unreviewed_anomaly_deferred_by_policy"
ACTION = "defer_pending_review"
REASON = "policy_deferral_no_human_review"
EXPERIMENT = "b4-whisper-large-v3-lora"

# Fields copied from the audit. Checksums and metrics only -- no transcript, no
# path, no speaker or session identifier, nothing derived from content.
CARRY = ("audio_checksum_sha256", "language", "task", "duration_s",
         "label_tokens_effective", "tokens_per_s", "z_score")


def client():
    import boto3
    return boto3.Session(profile_name="medzen", region_name="eu-central-1").client("s3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--authorized-by-role", required=True,
                    help="ROLE authorising the policy (not an identity)")
    a = ap.parse_args()
    if "@" in a.authorized_by_role:
        raise SystemExit("REFUSING: authorized-by-role must be a role, not an identity")

    # ---- the draft must still be unreviewed --------------------------------
    draft_raw = DRAFT.read_bytes()
    draft = json.loads(draft_raw)
    classified = [e for e in draft["entries"] if e.get("classification") is not None]
    if classified:
        raise SystemExit(
            f"REFUSING: {len(classified)} row(s) in {DRAFT.name} carry a human "
            "classification. Real review must be finalised through "
            "scripts/finalize_label_review.py, not restated as policy.")
    if draft.get("status") != "draft":
        raise SystemExit(f"REFUSING: {DRAFT.name} status is {draft.get('status')!r}, "
                         "expected 'draft'")

    # ---- recompute every binding from its artefact -------------------------
    cli = client()
    b = RB.recompute(cli)
    problems = [p for p in RB.verify(b)
                if not p.startswith("uncommitted changes outside the review draft")]
    # This script writes exactly one file; nothing else may differ, or the
    # bindings would describe a tree that was never committed.
    allowed_dirty = {str(OUT.relative_to(ROOT)), str(DRAFT.relative_to(ROOT))}
    stray = [p for p in b["repo_dirty_paths"] if p not in allowed_dirty]
    if stray:
        problems.append("uncommitted changes: " + ", ".join(stray[:8])
                        + " — commit code and evidence before writing policy")
    if problems:
        print(f"REFUSING — {len(problems)} problem(s):")
        for p in problems:
            print(f"  {p}")
        return 1

    audit = b["_audit"]

    # ---- the exact 20, with the trigger taken from the audit array ---------
    entries = []
    for r in audit["over_limit_rows"]:
        entries.append({**{k: r[k] for k in CARRY},
                        "trigger": "over_decoder_limit"})
    for r in audit["rate_outlier_rows"]:
        entries.append({**{k: r[k] for k in CARRY},
                        "trigger": "extreme_token_rate_under_limit"})

    n_over = sum(1 for e in entries if e["trigger"] == "over_decoder_limit")
    n_rate = len(entries) - n_over
    if (n_over, n_rate) != (audit["rows_over_limit"], audit["rate_outliers_under_limit"]):
        raise SystemExit(f"REFUSING: derived {n_over}+{n_rate}, audit records "
                         f"{audit['rows_over_limit']}+{audit['rate_outliers_under_limit']}")
    if (n_over, n_rate) != (6, 14):
        raise SystemExit(f"REFUSING: expected 6 over-limit + 14 rate outliers, "
                         f"got {n_over}+{n_rate}")
    checksums = [e["audio_checksum_sha256"] for e in entries]
    if len(set(checksums)) != 20:
        raise SystemExit(f"REFUSING: {len(checksums)} entries but "
                         f"{len(set(checksums))} unique checksums")
    for e in entries:
        over = e["label_tokens_effective"] > audit["label_limit"]
        if over != (e["trigger"] == "over_decoder_limit"):
            raise SystemExit(f"REFUSING: {e['audio_checksum_sha256'][:16]} trigger "
                             f"{e['trigger']} disagrees with its token count")
        e.update({"classification": CLASSIFICATION, "action": ACTION,
                  "defect": False, "reason_code": REASON,
                  "human_reviewed": False})

    doc = {
        "list_id": LIST_ID,
        "status": "approved",
        "decision_type": "policy_deferral",
        # The whole point of this record. Read it before anything else here.
        "human_review_performed": False,
        "authorized_by_role": a.authorized_by_role,
        "authorized_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "statement": (
            "All 20 flagged rows are deferred from this experiment by policy. No "
            "human has listened to any of them, so none is classified as "
            "defective, valid, or decoder-incompatible -- only as unreviewed. "
            "Deferral is a decision about this run, not about the data."),
        "scope": {
            "experiment": EXPERIMENT,
            "applies_to": "candidate training runs of this experiment only",
            "artifacts": "candidates/ only",
            "promotion_permitted": False,
            "distribution_permitted": False,
            "reuse_requires": ("human review of each row before these rows are "
                               "trained on, promoted, distributed or reused in "
                               "any other experiment"),
            "expires_with": EXPERIMENT,
        },
        "relates_to": {
            "review_draft": "DQ-2026-001-label-review",
            "review_draft_status": draft.get("status"),
            "review_draft_sha256": hashlib.sha256(draft_raw).hexdigest(),
            "review_draft_classified_entries": 0,
            "note": ("the human review remains open and unclassified; this policy "
                     "does not close it and must not be cited as if it had"),
        },
        "counts": {"total": len(entries), "over_decoder_limit": n_over,
                   "extreme_token_rate_under_limit": n_rate,
                   "defects": 0, "excluded_as_defective": 0},
        "bindings": {
            "audit_path": b["audit_path"],
            "audit_sha256": b["audit_sha256"],
            "audit_verifier_git_commit": b["audit_verifier_git_commit"],
            "audit_verifier_file_sha256": b["audit_verifier_file_sha256"],
            "audit_label_limit": audit["label_limit"],
            "audit_scope": b["scope"],
            "eligible_source_pool_rows": audit["eligible_source_pool_rows"],
            # RAW bytes of COMPLETE.json, not a re-serialisation: a dict that
            # round-trips through json.dumps is a different byte string, so a
            # hash of the re-serialised form proves nothing about the object.
            "v2_complete_key": b["complete_key"],
            "v2_complete_raw_sha256": b["complete_sha256"],
            "manifests": {k: v["actual"] for k, v in b["manifests"].items()},
            "manifests_total": b["manifests_total"],
            "tokenizer_repo": audit["tokenizer"]["repo"],
            "tokenizer_revision": b["tokenizer_revision"],
            "tokenizer_cache_manifest_sha256": b["tokenizer_cache_manifest_sha256"],
            "deferred_checksums_sha256": hashlib.sha256(
                "\n".join(sorted(checksums)).encode()).hexdigest(),
            "policy_repo_git_commit": b["repo_git_commit"],
        },
        "content_policy": ("checksums and numeric metrics only; no transcript, "
                           "speaker, session, filepath or content excerpt appears "
                           "in this file"),
        "exclusions": sorted(entries, key=lambda e: e["audio_checksum_sha256"]),
    }

    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    raw = OUT.read_bytes()
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  entries {len(entries)}  ({n_over} over-limit + {n_rate} rate outliers)")
    print(f"  human_review_performed=False  defects=0  action={ACTION}")
    print(f"  bound to audit {b['audit_sha256'][:16]} | "
          f"COMPLETE raw {b['complete_sha256'][:16]} | "
          f"{b['manifests_total']} manifests | tok {b['tokenizer_revision'][:12]}")
    print(f"POLICY_SHA256={hashlib.sha256(raw).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
