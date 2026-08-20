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


def require_available(holdout_key: str, path: Path = LEDGER) -> None:
    for entry in verify_chain(path):
        if entry.get("holdout") == holdout_key and entry["event"] == "CONSUMED":
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
