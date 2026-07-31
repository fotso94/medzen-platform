#!/usr/bin/env python3
"""Publish the ADOPTION record that approves a corpus version for training.

Adoption is a decision taken AFTER a migration finishes, so it cannot live
inside the record the migration wrote. This is a separate object, and it binds:

  * the exact RAW BYTES of COMPLETE.json -- not a re-serialisation, which would
    describe Python's json encoder rather than the object in the bucket;
  * the sha256 of the deferral policy in force, so approval granted for one set
    of deferred rows cannot license a different set.

Immutability, stated honestly: the bucket has versioning enabled but NOT Object
Lock, so nothing at the bucket level prevents a later overwrite. What this
script guarantees is that it will not itself overwrite an existing record --
the write is conditional (If-None-Match: *), so S3 itself rejects it if the key
already exists, closing the gap between checking and writing. The VersionId it
prints names bytes that cannot be altered in place. That is weaker than WORM
and must not be described as WORM.

    python scripts/publish_adoption.py --version v2 \
        --approved-by-role platform-owner \
        --policy platform/decisions/DQ-2026-002-policy-deferral.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from botocore.exceptions import ClientError, ParamValidationError  # noqa: E402

from pipeline import review_bindings as RB  # noqa: E402

BUCKET = "medzen-speech"
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "platform/evidence"


def client():
    import boto3
    return boto3.Session(profile_name="medzen", region_name="eu-central-1").client("s3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v2")
    ap.add_argument("--approved-by-role", required=True)
    ap.add_argument("--policy", required=True,
                    help="path to the deferral policy this adoption is granted with")
    ap.add_argument(
        "--adoption-key", default=None,
        help="direct child key under curated/_versions/<version>/. Use a new "
             "experiment-scoped key instead of replacing an earlier adoption")
    ap.add_argument("--dataset-fingerprint", default=None,
                    help="required 64-hex fingerprint for a corrected adoption")
    ap.add_argument("--eligible-rows", type=int, default=None,
                    help="eligible rows after the bound policy is applied")
    ap.add_argument("--upload", action="store_true",
                    help="without this the record is printed and not published")
    a = ap.parse_args()
    if "@" in a.approved_by_role:
        raise SystemExit("REFUSING: approved-by-role must be a role, not an identity")

    cli = client()
    comp_key = f"curated/_versions/{a.version}/COMPLETE.json"
    adopt_key = (
        a.adoption_key
        or f"curated/_versions/{a.version}/ADOPTION.json")
    want_prefix = f"curated/_versions/{a.version}/"
    if not adopt_key.startswith(want_prefix) or "/" in adopt_key[len(want_prefix):]:
        raise SystemExit(
            f"REFUSING: --adoption-key must be a direct child of {want_prefix}")
    if (a.dataset_fingerprint is None) != (a.eligible_rows is None):
        raise SystemExit(
            "REFUSING: --dataset-fingerprint and --eligible-rows must be "
            "supplied together")
    if a.dataset_fingerprint is not None and (
            len(a.dataset_fingerprint) != 64
            or any(c not in "0123456789abcdef"
                   for c in a.dataset_fingerprint)):
        raise SystemExit(
            "REFUSING: --dataset-fingerprint must be 64 lowercase hex")

    # ---- never overwrite an existing approval ------------------------------
    # Only a genuine 404 means "absent". AccessDenied, a throttle or a DNS
    # failure are NOT evidence of absence, and treating them as such would let a
    # transient error authorise overwriting an existing approval.
    try:
        cli.head_object(Bucket=BUCKET, Key=adopt_key)
        raise SystemExit(
            f"REFUSING: s3://{BUCKET}/{adopt_key} already exists. An adoption is "
            "a one-time decision; replacing it would rewrite the approval record "
            "for a version that may already have been trained from.")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code not in ("404", "NoSuchKey", "NotFound") and status != 404:
            raise SystemExit(
                f"REFUSING: cannot determine whether {adopt_key} exists "
                f"({code or status}). An error is not an absence.")

    comp_raw = cli.get_object(Bucket=BUCKET, Key=comp_key)["Body"].read()
    comp = json.loads(comp_raw)
    comp_sha = hashlib.sha256(comp_raw).hexdigest()

    policy_raw = Path(a.policy).read_bytes()
    policy = json.loads(policy_raw)
    policy_sha = hashlib.sha256(policy_raw).hexdigest()

    # ---- the policy must be the conservative deferral, not a review --------
    if policy.get("decision_type") != "policy_deferral":
        raise SystemExit(f"REFUSING: {a.policy} is {policy.get('decision_type')!r}, "
                         "expected 'policy_deferral'")
    if policy.get("human_review_performed") is not False:
        raise SystemExit("REFUSING: policy does not record human_review_performed=false")
    if policy["bindings"]["v2_complete_raw_sha256"] != comp_sha:
        raise SystemExit(
            f"REFUSING: the policy was bound to COMPLETE raw sha256 "
            f"{policy['bindings']['v2_complete_raw_sha256'][:16]}, the bucket now "
            f"holds {comp_sha[:16]}")

    # ---- the corpus must still hash as the policy recorded -----------------
    b = RB.recompute(cli)
    problems = [p for p in RB.verify(b) if not p.startswith("uncommitted changes")]
    stray = [p for p in b["repo_dirty_paths"]
             if p not in {"platform/decisions/DQ-2026-001-label-review.json"}]
    if stray:
        problems.append("uncommitted changes: " + ", ".join(stray[:8]))
    if b["complete_sha256"] != comp_sha:
        problems.append("completion record changed between reads")
    if problems:
        print(f"REFUSING — {len(problems)} problem(s):")
        for p in problems:
            print(f"  {p}")
        return 1

    doc = {
        "record": "ADOPTION",
        "version": a.version,
        "status": "approved",
        "approved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "approved_by_role": a.approved_by_role,
        "statement": (
            f"Corpus version {a.version} is approved as a training input for the "
            "b4-whisper-large-v3-lora Option B experiment only, subject to the deferral "
            "policy bound below. This approves the CORPUS; it is not a review of "
            f"the {policy['counts']['total']} deferred rows, which remain "
            "unreviewed."),
        # What COMPLETE.json does and does not mean, recorded next to the thing
        # that supplies the missing half.
        "completion_vs_adoption": (
            "COMPLETE.json attests only that the migration finished writing. It "
            "was written before this decision existed and carries no approval. "
            "This record is the approval."),
        "complete_key": comp_key,
        "complete_raw_sha256": comp_sha,
        "complete_manifests_total": len(comp.get("manifests") or {}),
        "deferral_policy_id": policy["list_id"],
        "deferral_policy_sha256": policy_sha,
        "deferral_policy_human_review_performed": policy["human_review_performed"],
        "deferred_rows": policy["counts"]["total"],
        "deferred_checksums_sha256": policy["bindings"]["deferred_checksums_sha256"],
        "dataset_fingerprint": a.dataset_fingerprint,
        "eligible_rows": a.eligible_rows,
        "scope": {
            "experiment": "b4-whisper-large-v3-lora",
            "artifacts": "candidates/ only",
            "promotion_permitted": False,
            "distribution_permitted": False,
            "eval_permitted": False,
            "reuse_requires": ("a new adoption record; this one does not extend to "
                               "other experiments or to promotion"),
        },
        "human_review_performed": False,
        "independent_human_approval_claimed": False,
        "bindings": {
            "manifests": {k: v["actual"] for k, v in b["manifests"].items()},
            "manifests_total": b["manifests_total"],
            "tokenizer_revision": b["tokenizer_revision"],
            "tokenizer_cache_manifest_sha256": b["tokenizer_cache_manifest_sha256"],
            "audit_sha256": b["audit_sha256"],
            "repo_git_commit": b["repo_git_commit"],
        },
        "immutability": (
            "bucket versioning is ENABLED; S3 Object Lock is NOT configured. The "
            "VersionId of this object names bytes that cannot be altered in "
            "place, but the bucket does not prevent a later overwrite. This is "
            "not WORM."),
        "content_policy": "checksums and hashes only; no content of any kind",
    }
    body = (json.dumps(doc, indent=2) + "\n").encode()

    print(json.dumps(doc, indent=2))
    print(f"\nADOPTION_SHA256={hashlib.sha256(body).hexdigest()}")
    if not a.upload:
        print("\n(dry run — pass --upload to publish)")
        return 0

    # Conditional create. The head_object above can go stale between the check
    # and the write; If-None-Match makes S3 the arbiter, so a concurrent
    # publisher loses instead of silently overwriting an approval.
    try:
        put = cli.put_object(Bucket=BUCKET, Key=adopt_key, Body=body,
                             ContentType="application/json", IfNoneMatch="*")
    except ParamValidationError:
        raise SystemExit(
            "REFUSING: this botocore does not support conditional writes "
            "(If-None-Match). Publishing unconditionally would reintroduce the "
            "overwrite race; upgrade botocore instead.")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("PreconditionFailed", "ConditionalRequestConflict"):
            raise SystemExit(
                f"REFUSING: {adopt_key} was created concurrently; this run did "
                "not publish it. Inspect the existing record before retrying.")
        raise
    vid = put.get("VersionId")
    back = cli.get_object(Bucket=BUCKET, Key=adopt_key)["Body"].read()
    if back != body:
        raise SystemExit("REFUSING: read-back differs from what was written")
    print(f"\npublished s3://{BUCKET}/{adopt_key}")
    print(f"  VersionId {vid}")
    print(f"  bound COMPLETE raw {comp_sha[:16]} | policy {policy_sha[:16]}")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    ev = EVIDENCE / f"adoption-{a.version}.json"
    ev.write_text(json.dumps({
        "key": adopt_key, "version_id": vid,
        "adoption_sha256": hashlib.sha256(body).hexdigest(),
        "complete_raw_sha256": comp_sha, "deferral_policy_sha256": policy_sha,
        "published_utc": doc["approved_utc"],
        "object_lock": "not configured; versioning only",
    }, indent=2) + "\n")
    print(f"  evidence {ev.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
