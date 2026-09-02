#!/usr/bin/env python3
"""Reviewer CLI for opted-in frontend recordings (Phase 4).

  list                      show raw sessions awaiting review (hypothesis vs correction)
  approve <request_id>...   copy audio + de-identified meta + review.json into
                            curated/frontend-sessions-reviewed/<day>/<rid>/ (365-day
                            retention by bucket lifecycle) — text = reviewer text,
                            or the user's correction, or the confirmed hypothesis
  reject  <request_id>...   record a rejection in the raw folder (nothing is copied)

Approval is refused when: consent is missing/other version, or the only
available text is an unconfirmed ASR hypothesis with no correction.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

BUCKET = "medzen-speech"
RAW = "raw/_incoming/frontend-sessions/"
REVIEWED = "curated/frontend-sessions-reviewed/"
CONSENT_VERSION = "2026-09-02-v1"
REVIEWED_DAYS = 365
KMS = "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57"


def _s3(profile):
    import boto3
    return boto3.Session(profile_name=profile, region_name="eu-central-1").client("s3")


def _read_json(s3, key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def _sessions(s3):
    folders = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=RAW):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/meta.json"):
                folders[key[: -len("meta.json")]] = None
    out = []
    for folder in sorted(folders):
        meta = _read_json(s3, folder + "meta.json")
        feedback = _read_json(s3, folder + "feedback.json")
        review = _read_json(s3, folder + "review.json")
        out.append({"folder": folder, "meta": meta, "feedback": feedback, "review": review})
    return out


def _decide_text(meta, feedback, override):
    if override:
        return override, "reviewer"
    fb = feedback or {}
    if fb.get("correction"):
        return str(fb["correction"]).strip(), "corrected"
    if fb.get("transcript_ok") is True:
        return str(meta.get("asr_hypothesis") or "").strip(), "confirmed"
    return None, None


def cmd_list(s3):
    for s in _sessions(s3):
        m, f = s["meta"] or {}, s["feedback"] or {}
        rid = m.get("request_id", "?")
        state = "REVIEWED" if s["review"] else "pending"
        text, kind = _decide_text(m, f, None)
        print(f"{rid}  {m.get('language','?'):4s} {state:8s} consent={(m.get('consent') or {}).get('version')} "
              f"transcript_ok={f.get('transcript_ok')} useful={f.get('answer_useful')}")
        print(f"    heard:      {str(m.get('asr_hypothesis',''))[:90]!r}")
        print(f"    correction: {str(f.get('correction',''))[:90]!r}   -> {kind or 'NOT ADMISSIBLE (no confirmation/correction)'}")


def cmd_approve(s3, rids, reviewer, text_override):
    for s in _sessions(s3):
        m = s["meta"] or {}
        if m.get("request_id") not in rids:
            continue
        consent = m.get("consent") or {}
        if consent.get("granted") is not True or consent.get("version") != CONSENT_VERSION:
            print(f"REFUSED {m.get('request_id')}: consent missing or not {CONSENT_VERSION}"); continue
        text, kind = _decide_text(m, s["feedback"], text_override)
        if not text:
            print(f"REFUSED {m.get('request_id')}: no correction and no confirmation - an unconfirmed hypothesis is never admitted"); continue
        now = dt.datetime.now(dt.timezone.utc)
        day = now.strftime("%Y-%m-%d")
        dst = f"{REVIEWED}{day}/{m['request_id']}/"
        audio = s3.get_object(Bucket=BUCKET, Key=s["folder"] + "audio.wav")["Body"].read()
        # de-identified copy: only what the adapter needs; no ip/ua ever existed
        meta_out = {
            "schema_version": 1, "request_id": m["request_id"], "language": m.get("language"),
            "session_pseudonym": m.get("session_pseudonym"), "asr_hypothesis": m.get("asr_hypothesis"),
            "model_versions": m.get("model_versions"), "audio": m.get("audio"),
            "captured_at": now.isoformat().replace("+00:00", "Z"),
            "consent": {"granted": True, "version": CONSENT_VERSION, "consented_at": consent.get("consented_at")},
            "retention": {"policy_id": "frontend-sessions-reviewed-365d", "days": REVIEWED_DAYS,
                          "expires_at": (now + dt.timedelta(days=REVIEWED_DAYS)).isoformat().replace("+00:00", "Z")},
            "raw_folder": s["folder"], "reviewed_at": now.isoformat().replace("+00:00", "Z"),
        }
        review = {"approved": True, "kind": kind, "text": text, "reviewer": reviewer,
                  "reviewed_at": meta_out["reviewed_at"]}
        for key, body, ctype in ((dst + "audio.wav", audio, "audio/wav"),
                                 (dst + "meta.json", json.dumps(meta_out, indent=1).encode(), "application/json"),
                                 (dst + "review.json", json.dumps(review, indent=1).encode(), "application/json")):
            s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=ctype,
                          ServerSideEncryption="aws:kms", SSEKMSKeyId=KMS)
        s3.put_object(Bucket=BUCKET, Key=s["folder"] + "review.json",
                      Body=json.dumps(review, indent=1).encode(), ContentType="application/json",
                      ServerSideEncryption="aws:kms", SSEKMSKeyId=KMS)
        print(f"APPROVED {m['request_id']} ({kind}) -> s3://{BUCKET}/{dst}")


def cmd_reject(s3, rids, reviewer, reason):
    for s in _sessions(s3):
        m = s["meta"] or {}
        if m.get("request_id") in rids:
            now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            s3.put_object(Bucket=BUCKET, Key=s["folder"] + "review.json",
                          Body=json.dumps({"approved": False, "reviewer": reviewer, "reason": reason,
                                           "reviewed_at": now}, indent=1).encode(),
                          ContentType="application/json", ServerSideEncryption="aws:kms", SSEKMSKeyId=KMS)
            print(f"REJECTED {m['request_id']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["list", "approve", "reject"])
    p.add_argument("request_ids", nargs="*")
    p.add_argument("--profile", default=None)
    p.add_argument("--reviewer", default="owner")
    p.add_argument("--text", default=None, help="reviewer-supplied final text (approve)")
    p.add_argument("--reason", default="", help="rejection reason")
    a = p.parse_args()
    s3 = _s3(a.profile)
    if a.command == "list":
        cmd_list(s3)
    elif a.command == "approve":
        cmd_approve(s3, set(a.request_ids), a.reviewer, a.text)
    else:
        cmd_reject(s3, set(a.request_ids), a.reviewer, a.reason)


if __name__ == "__main__":
    sys.exit(main())
