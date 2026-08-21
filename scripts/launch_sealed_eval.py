#!/usr/bin/env python3
"""Sealed-evaluation acquisition tool — LAUNCH DELIBERATELY NOT IMPLEMENTED.

Codex reviews #13-#14 demonstrated that a generic launcher accepting
arbitrary user-data cannot be made safe by validation bolted on around
it (acquire-A/read-B via comments, git-vs-working-tree TOCTOU, inert
deadline tags, unencrypted default volumes, unused binding fields...).
The launch capability therefore DOES NOT EXIST until the universal-pilot
evaluator is built as a structured composition against
SEALED-EVALUATOR-SPEC-2026-001 (platform/decisions/), which captures
every requirement from those reviews as acceptance criteria.

What this tool DOES provide today:
  acquire — the atomic, reserved-sha-verified ledger consumption with
            durable commit-and-verify (ledger-file-scoped pathspec).
Any attempt to launch refuses loudly. There is no other launch path;
sealed evaluation is ON HOLD and this file is the hold, in code.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from holdout_ledger import LEDGER, LedgerRefusal, record_consumption

ROOT = Path(__file__).resolve().parents[1]


class LaunchRefusal(RuntimeError):
    pass


def durable_commit(entry: dict, runner=subprocess.run) -> None:
    """Commit ONLY the ledger (pathspec-scoped — Codex #14: a bare commit
    swept up whatever was staged), then verify the entry reads back from
    git HEAD. Durability is this clone's git history; single-controller
    operation is the documented operating model until the spec's
    distributed conditional store exists."""
    rel = str(LEDGER.relative_to(ROOT))
    got = runner(["git", "-C", str(ROOT), "commit", "-q",
                  "-m", f"Ledger: CONSUMED entry {entry['entry']} "
                        f"({entry['holdout']})",
                  "--only", "--", rel], capture_output=True, text=True)
    if got.returncode != 0:
        raise LaunchRefusal(
            f"ledger commit failed ({got.stderr[-200:]}) — the consumption "
            "record must be durable before anything else happens")
    shown = runner(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"],
                   capture_output=True, text=True)
    tail = json.loads(shown.stdout.splitlines()[-1])
    if tail.get("entry") != entry["entry"]:
        raise LaunchRefusal("committed ledger tail does not show the "
                            "acquisition")


def main(argv: list[str] | None = None, runner=subprocess.run) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    acq = sub.add_parser("acquire")
    acq.add_argument("--holdout", required=True)
    acq.add_argument("--sha", required=True)
    acq.add_argument("--consumer", required=True)
    sub.add_parser("launch")
    args = parser.parse_args(argv)
    if args.command == "launch":
        print(json.dumps({"status": "REFUSED", "detail":
              "sealed-evaluator launch is NOT IMPLEMENTED — build against "
              "SEALED-EVALUATOR-SPEC-2026-001 first (Codex reviews #13-#14)"}))
        return 1
    try:
        entry = record_consumption(args.holdout, args.sha, args.consumer)
        durable_commit(entry, runner)
    except (LedgerRefusal, LaunchRefusal) as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "ACQUIRED_DURABLE",
                      "entry": entry["entry"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
