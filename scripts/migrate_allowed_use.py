#!/usr/bin/env python3
"""Produce a NEW manifest version granting asr_train to licence-approved configs.

Authorised by platform/decisions/LIC-2026-001-waxalnlp-tts-asr-train.json, which
records that the upstream licences impose no field-of-use restriction and that
the adapter's task-derived allowed_use was an internal convention.

WHAT IS PROVEN, PRECISELY
-------------------------
Field-value equivalence for every field except allowed_use -- NOT byte
equivalence. The output is written as canonical JSON (sorted keys), so the bytes
of an unchanged row differ from its v1 bytes even though every value is
identical. Claiming byte equivalence would be false; the check compares parsed
values, field by field, and additionally pins the fields whose change would make
the migration unsafe (checksums, paths, split, duration).

WHAT IS REFUSED
---------------
The v1 manifests are frozen and never written to. Beyond that, the migration
refuses to run at all unless: the decision is approved, exactly the expected
number of configs and rows change, every changed row is split == "train",
source and destination versions differ, and NOTHING already exists at the
destination -- v2 is immutable once written, so a partial rerun must not
overwrite it.

ORDER OF OPERATIONS
-------------------
Every output is generated and validated in memory before the first byte is
uploaded, and the completion record is written LAST. A loader must refuse v2
unless that record exists and every manifest hash in it matches, so a migration
interrupted halfway can never be mistaken for a finished one.

    python scripts/migrate_allowed_use.py                    # dry run + diff
    python scripts/migrate_allowed_use.py --apply            # write v2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
DECISION = "platform/decisions/LIC-2026-001-waxalnlp-tts-asr-train.json"

GRANT = "asr_train"
# Never granted: these corpora are text-disjoint and same-speaker, so they
# cannot evidence speaker-generalising ASR performance.
NEVER_GRANT = "asr_eval"
GRANT_SPLIT = "train"

EXPECT_CONFIGS = 9
EXPECT_CHANGED_ROWS = 2305

# Fields whose change would make this a data migration rather than a metadata one
PINNED_FIELDS = ("audio_checksum_sha256", "raw_checksum_sha256", "audio_filepath",
                 "raw_filepath", "split", "duration_s", "sample_rate", "channels",
                 "text_normalized", "text_verbatim", "speaker_id", "session_id")

ROOT = Path(__file__).resolve().parent.parent


def client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def load_decision() -> dict:
    d = json.loads((ROOT / DECISION).read_text())
    if d.get("status") != "approved":
        raise SystemExit(f"REFUSING: decision {d.get('decision_id')} is not approved")
    return d


def list_manifests(cli, version: str) -> list[str]:
    keys, tok = [], {"Bucket": BUCKET, "Prefix": "curated/"}
    while True:
        r = cli.list_objects_v2(**tok)
        keys += [o["Key"] for o in r.get("Contents", [])
                 if o["Key"].endswith(f"/{version}/manifest.jsonl")]
        if not r.get("IsTruncated"):
            return sorted(keys)
        tok["ContinuationToken"] = r["NextContinuationToken"]


def exists(cli, key: str) -> bool:
    try:
        cli.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def completion_key(version: str) -> str:
    return f"curated/_versions/{version}/COMPLETE.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-version", default="v1")
    ap.add_argument("--to-version", default="v2")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="/tmp/allowed_use_migration.json")
    a = ap.parse_args()

    if a.from_version == a.to_version:
        raise SystemExit(f"REFUSING: from-version == to-version ({a.from_version}); "
                         "a migration must write a NEW version")

    cli = client()
    decision = load_decision()
    approved = {c["config"]: c for c in decision["configs_authorized"]}
    if len(approved) != EXPECT_CONFIGS:
        raise SystemExit(f"REFUSING: decision authorises {len(approved)} configs, "
                         f"expected exactly {EXPECT_CONFIGS}")

    print(f"decision : {decision['decision_id']} ({decision['status']})")
    print(f"configs  : {len(approved)} authorised to gain {GRANT}")
    print(f"migration: {a.from_version} -> {a.to_version}   "
          f"{'APPLY' if a.apply else 'DRY RUN'}")
    print(f"grant    : {GRANT} on split=={GRANT_SPLIT} rows only; "
          f"{NEVER_GRANT} never added\n")

    # ---------------- phase 1: generate and validate EVERYTHING --------------
    planned: dict[str, dict] = {}
    problems: list[str] = []
    tot = {"rows": 0, "changed": 0, "unchanged": 0, "configs_changed": 0}

    for key in list_manifests(cli, a.from_version):
        _, lang, task, cfg, _ver, _ = key.split("/")
        src = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        rows = [json.loads(l) for l in src.decode().splitlines() if l.strip()]
        eligible = cfg in approved

        out_lines, changed = [], 0
        for rec in rows:
            new = dict(rec)
            had_asr_eval = NEVER_GRANT in rec.get("allowed_use", [])
            if eligible and rec.get("split") == GRANT_SPLIT \
                    and GRANT not in rec.get("allowed_use", []):
                new["allowed_use"] = sorted({*rec.get("allowed_use", []), GRANT})
                changed += 1
                if rec.get("split") != GRANT_SPLIT:
                    problems.append(f"{cfg}: changed a non-train row")
            # asr_eval must never appear where it did not already
            if NEVER_GRANT in new.get("allowed_use", []) and not had_asr_eval:
                problems.append(f"{cfg}: {NEVER_GRANT} was newly added — forbidden")
            # field-value equivalence for everything except allowed_use
            for f in PINNED_FIELDS:
                if rec.get(f) != new.get(f):
                    problems.append(f"{cfg}: pinned field {f} changed")
            if {k: v for k, v in rec.items() if k != "allowed_use"} != \
               {k: v for k, v in new.items() if k != "allowed_use"}:
                problems.append(f"{cfg}: a field other than allowed_use changed")
            out_lines.append(json.dumps(new, sort_keys=True))

        body = ("\n".join(out_lines) + "\n").encode()
        dst = key.replace(f"/{a.from_version}/", f"/{a.to_version}/")
        lineage_key = dst.replace("manifest.jsonl", "LINEAGE.json")
        lineage = {
            "manifest": f"s3://{BUCKET}/{dst}",
            "manifest_sha256": hashlib.sha256(body).hexdigest(),
            "derived_from": f"s3://{BUCKET}/{key}",
            "derived_from_sha256": hashlib.sha256(src).hexdigest(),
            "decision_id": decision["decision_id"],
            "rows": len(rows),
            "rows_granted_asr_train": changed,
            "change": (f"allowed_use gains {GRANT} on split=={GRANT_SPLIT} rows"
                       if changed else "none; carried forward unchanged"),
            "equivalence": "field-value equivalent to the source except allowed_use "
                           "(bytes differ: output is canonical JSON with sorted keys)",
        }
        planned[cfg] = {"src_key": key, "dst_key": dst, "lineage_key": lineage_key,
                        "body": body, "lineage": lineage, "changed": changed,
                        "rows": len(rows), "eligible": eligible,
                        "label": f"{lang}/{task}/{cfg}"}
        tot["rows"] += len(rows)
        tot["changed"] += changed
        tot["unchanged"] += len(rows) - changed
        if changed:
            tot["configs_changed"] += 1
        print(f"  [{'GRANT' if eligible else '  -  '}] {lang}/{task}/{cfg:<9} "
              f"{len(rows):>4} rows  +{GRANT}: {changed:>4}")

    # ---------------- phase 2: refuse unless the totals are exactly right ----
    if tot["changed"] != EXPECT_CHANGED_ROWS:
        problems.append(f"changed {tot['changed']} rows, expected exactly "
                        f"{EXPECT_CHANGED_ROWS}")
    if tot["configs_changed"] != EXPECT_CONFIGS:
        problems.append(f"{tot['configs_changed']} configs changed, expected exactly "
                        f"{EXPECT_CONFIGS}")

    report = {
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "decision_id": decision["decision_id"],
        "decision_sha256": hashlib.sha256((ROOT / DECISION).read_bytes()).hexdigest(),
        "from_version": a.from_version, "to_version": a.to_version,
        "applied": False,
        "grant": GRANT, "grant_split": GRANT_SPLIT, "never_granted": NEVER_GRANT,
        "equivalence_claim": ("field-value equivalence for every field except "
                              "allowed_use; NOT byte equivalence, because the output "
                              "is canonical JSON with sorted keys"),
        "expectations": {"configs": EXPECT_CONFIGS, "changed_rows": EXPECT_CHANGED_ROWS},
        "totals": tot,
        "corpora": {p["label"]: {k: v for k, v in p["lineage"].items()}
                    for p in planned.values()},
        "problems": problems,
    }
    report["ok"] = not problems
    Path(a.out).write_text(json.dumps(report, indent=2) + "\n")

    print(f"\ntotals: {tot['rows']} rows | {tot['changed']} granted {GRANT} "
          f"across {tot['configs_changed']} configs | {tot['unchanged']} unchanged")
    print(f"expected: {EXPECT_CHANGED_ROWS} rows across {EXPECT_CONFIGS} configs")

    if problems:
        print(f"\n{len(problems)} PROBLEM(S) — refusing:")
        for p in problems[:20]:
            print(f"  {p}")
        return 1

    if not a.apply:
        print(f"\nreport: {a.out}")
        print("DRY RUN — nothing written. Re-run with --apply after review.")
        return 0

    # ---------------- phase 3: destination must be untouched ----------------
    collisions = [k for p in planned.values()
                  for k in (p["dst_key"], p["lineage_key"]) if exists(cli, k)]
    if exists(cli, completion_key(a.to_version)):
        collisions.append(completion_key(a.to_version))
    if collisions:
        print(f"\nREFUSING: {len(collisions)} destination object(s) already exist. "
              f"{a.to_version} is immutable once written.")
        for k in collisions[:10]:
            print(f"  {k}")
        return 1

    # ---------------- phase 4: upload, completion record LAST ---------------
    for p in planned.values():
        cli.put_object(Bucket=BUCKET, Key=p["dst_key"], Body=p["body"])
        cli.put_object(Bucket=BUCKET, Key=p["lineage_key"],
                       Body=(json.dumps(p["lineage"], indent=2) + "\n").encode())
        print(f"  wrote {p['dst_key']}")

    complete = {
        "version": a.to_version,
        "from_version": a.from_version,
        "decision_id": decision["decision_id"],
        "decision_sha256": report["decision_sha256"],
        "completed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totals": tot,
        "manifests": {p["label"]: {"key": p["dst_key"],
                                   "sha256": p["lineage"]["manifest_sha256"],
                                   "rows": p["rows"]}
                      for p in planned.values()},
        "loader_contract": ("A loader MUST refuse this version unless this record "
                            "exists and every manifest hash listed here matches the "
                            "object it names."),
    }
    cli.put_object(Bucket=BUCKET, Key=completion_key(a.to_version),
                   Body=(json.dumps(complete, indent=2) + "\n").encode())
    print(f"  wrote {completion_key(a.to_version)}  (completion record, written LAST)")
    report["applied"] = True
    Path(a.out).write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
