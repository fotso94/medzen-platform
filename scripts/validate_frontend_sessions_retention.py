#!/usr/bin/env python3
"""Phase 4 retention-policy validator for opted-in frontend recordings.

Owner policy (2026-09-02):
  * raw recordings live under  s3://medzen-speech/raw/_incoming/frontend-sessions/
    and are deleted automatically after 90 days;
  * reviewed, de-identified, correction-paired recordings live under
    s3://medzen-speech/curated/frontend-sessions-reviewed/ for up to 12 months
    and are deleted automatically when that period expires;
  * every opted-in submission records the consent version and retention
    metadata (meta.json);
  * nothing is collected without explicit opt-in.

This validator checks (1) the bucket lifecycle rules exist with exactly those
prefixes/days and are Enabled, (2) every captured meta.json carries the consent
version, the consent flag true, the retention block whose expiry equals
capture time + policy days, and NO direct identifiers (ip / user agent), and
(3) reports counts per day. Exit 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

BUCKET = "medzen-speech"
RAW_PREFIX = "raw/_incoming/frontend-sessions/"
REVIEWED_PREFIX = "curated/frontend-sessions-reviewed/"
POLICY = {"raw_days": 90, "reviewed_days": 365}
FORBIDDEN_META_KEYS = {"ip", "source_ip", "user_agent", "ua", "email", "name"}
CONSENT_VERSION = "2026-09-02-v1"


def check_lifecycle(s3) -> list[str]:
    problems = []
    rules = {r["ID"]: r for r in s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"]}
    want = {
        "frontend-sessions-raw-90d": (RAW_PREFIX, POLICY["raw_days"]),
        "frontend-sessions-reviewed-365d": (REVIEWED_PREFIX, POLICY["reviewed_days"]),
    }
    for rid, (prefix, days) in want.items():
        r = rules.get(rid)
        if r is None:
            problems.append(f"lifecycle rule {rid} missing"); continue
        if r.get("Status") != "Enabled":
            problems.append(f"{rid}: not Enabled")
        if (r.get("Filter") or {}).get("Prefix") != prefix:
            problems.append(f"{rid}: prefix {r.get('Filter')} != {prefix}")
        if (r.get("Expiration") or {}).get("Days") != days:
            problems.append(f"{rid}: expiration {r.get('Expiration')} != {days} days")
        if (r.get("NoncurrentVersionExpiration") or {}).get("NoncurrentDays") is None:
            problems.append(f"{rid}: noncurrent versions would survive deletion")
    return problems


def check_objects(s3, prefix: str, days: int) -> tuple[list[str], int]:
    problems, count = [], 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("/meta.json"):
                continue
            count += 1
            meta = json.loads(s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read())
            key = obj["Key"]
            if meta.get("consent", {}).get("granted") is not True:
                problems.append(f"{key}: stored without explicit consent")
            if meta.get("consent", {}).get("version") != CONSENT_VERSION:
                problems.append(f"{key}: consent version {meta.get('consent', {}).get('version')!r}")
            ret = meta.get("retention") or {}
            if ret.get("days") != days:
                problems.append(f"{key}: retention days {ret.get('days')} != {days}")
            try:
                captured = dt.datetime.fromisoformat(str(meta["captured_at"]).replace("Z", "+00:00"))
                expires = dt.datetime.fromisoformat(str(ret["expires_at"]).replace("Z", "+00:00"))
                if abs((expires - captured) - dt.timedelta(days=days)) > dt.timedelta(minutes=1):
                    problems.append(f"{key}: expires_at is not captured_at + {days}d")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{key}: retention timestamps unreadable ({exc})")
            leaked = FORBIDDEN_META_KEYS & set(map(str.lower, meta.keys()))
            if leaked:
                problems.append(f"{key}: direct identifiers present {sorted(leaked)}")
    return problems, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name="eu-central-1")
    s3 = session.client("s3")
    problems = check_lifecycle(s3)
    raw_problems, raw_n = check_objects(s3, RAW_PREFIX, POLICY["raw_days"])
    rev_problems, rev_n = check_objects(s3, REVIEWED_PREFIX, POLICY["reviewed_days"])
    problems += raw_problems + rev_problems
    print(json.dumps({
        "lifecycle_ok": not check_lifecycle(s3),
        "raw_sessions": raw_n, "reviewed_sessions": rev_n,
        "policy": POLICY, "consent_version": CONSENT_VERSION,
        "problems": problems,
        "status": "PASS" if not problems else "FAIL",
    }, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
