#!/usr/bin/env python3
"""Executable sealed-holdout consumption gate (Codex review #9 rec 8: the
ledger was descriptive, not enforcing).

Chain rule: every entry embeds prev_sha256 = sha256 of the previous line's
exact bytes — rewriting history breaks the chain. Consumption rule: a
holdout with a CONSUMED entry can never be consumed again; gate runners
call require_available() BEFORE evaluating and record_consumption()
BEFORE reading results."""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

LEDGER = (Path(__file__).resolve().parents[1]
          / "platform/evidence/HOLDOUT-CONSUMPTION-LEDGER.jsonl")


class LedgerRefusal(RuntimeError):
    pass


def _lines(path: Path = LEDGER) -> list[str]:
    return [l for l in path.read_text().splitlines() if l.strip()]


def verify_chain(path: Path = LEDGER) -> list[dict]:
    lines = _lines(path)
    entries = []
    for index, line in enumerate(lines):
        entry = json.loads(line)
        if entry["entry"] != index + 1:
            raise LedgerRefusal(f"entry numbering broken at line {index + 1}")
        if index > 0:
            want = hashlib.sha256(lines[index - 1].encode()).hexdigest()
            if entry.get("prev_sha256") != want:
                raise LedgerRefusal(
                    f"hash chain broken at entry {entry['entry']} — the "
                    "ledger history was rewritten")
        entries.append(entry)
    return entries


VOID_REQUIRED_FIELDS = {
    "voids_entry", "holdout", "holdout_sha256", "evaluator_instance_ids",
    "userdata_sha256", "results_prefix_object_count", "log_version_ids",
    "no_inference_attestation", "approved_by",
}


def _valid_voids(entries: list[dict]) -> set[int]:
    """Codex review #11 (bypass reproduced): a void is honored ONLY when
    it is a strict, schema-complete adjudication that (a) names an
    EXISTING, PRECEDING CONSUMED entry, (b) matches that entry's holdout
    AND sha, (c) attests zero inference and zero result objects with the
    supporting AWS identities, (d) carries owner/reviewer approval, and
    (e) is the only void for that entry."""
    by_number = {e["entry"]: e for e in entries}
    seen_targets: set[int] = set()
    valid: set[int] = set()
    for e in entries:
        if e["event"] != "CONSUMPTION_VOIDED":
            continue
        if not VOID_REQUIRED_FIELDS <= set(e):
            continue
        target = by_number.get(e["voids_entry"])
        if (target is None or target["event"] != "CONSUMED"
                or target["entry"] >= e["entry"]
                or target.get("holdout") != e.get("holdout")
                or target.get("sha256") != e.get("holdout_sha256")):
            continue
        if e.get("results_prefix_object_count") != 0:
            continue
        if "no model inference started and no results were produced" not in                 str(e.get("no_inference_attestation", "")):
            continue
        if not str(e.get("approved_by", "")).strip().startswith("owner"):
            continue
        if e["voids_entry"] in seen_targets:
            valid.discard(e["voids_entry"])
            continue
        seen_targets.add(e["voids_entry"])
        valid.add(e["voids_entry"])
    return valid


def require_available(holdout_key: str, path: Path = LEDGER) -> None:
    """A CONSUMED entry spends the seal unless a strict-schema adjudication
    voids it (_valid_voids). A QUARANTINED entry blocks the holdout until
    a later RELEASED entry (same schema discipline) lifts it."""
    entries = verify_chain(path)
    voided = _valid_voids(entries)
    quarantined: set[str] = set()
    for e in entries:
        if e["event"] == "QUARANTINED":
            quarantined.add(e.get("holdout"))
        if e["event"] == "RELEASED" and e.get("holdout") in quarantined:
            if str(e.get("approved_by", "")).strip().startswith("owner"):
                quarantined.discard(e.get("holdout"))
    if holdout_key in quarantined:
        raise LedgerRefusal(
            f"{holdout_key} is QUARANTINED — release requires an "
            "owner-approved RELEASED entry")
    for entry in entries:
        if (entry.get("holdout") == holdout_key
                and entry["event"] == "CONSUMED"
                and entry["entry"] not in voided):
            raise LedgerRefusal(
                f"{holdout_key} was already CONSUMED (entry "
                f"{entry['entry']}) — the seal is spent")


def record_consumption(holdout_key: str, sha256: str, consumed_by: str,
                        path: Path = LEDGER) -> dict:
    require_available(holdout_key, path)
    lines = _lines(path)
    entry = {
        "entry": len(lines) + 1,
        "utc": datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": "CONSUMED",
        "holdout": holdout_key,
        "sha256": sha256,
        "consumed_by": consumed_by,
        "prev_sha256": hashlib.sha256(lines[-1].encode()).hexdigest(),
    }
    with path.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry
