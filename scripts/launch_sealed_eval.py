#!/usr/bin/env python3
"""THE ONLY sanctioned way to start a sealed evaluator (Codex review #12:
the gate existed but nothing called it).

Ordering contract, enforced here and tested: (1) ledger acquisition —
atomic, reserved-sha-verified — happens FIRST; (2) only then is any
selection object touched or any EC2 evaluator launched. A nonzero exit
means nothing was launched and nothing was read."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from holdout_ledger import LedgerRefusal, record_consumption


def main(argv: list[str] | None = None,
         runner=subprocess.run) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--consumer", required=True)
    parser.add_argument("--userdata", type=Path, required=True)
    parser.add_argument("--instance-type", default="g6.xlarge")
    args = parser.parse_args(argv)

    # STEP 1 — acquire. Refusal aborts before ANY external action.
    try:
        entry = record_consumption(args.holdout, args.sha, args.consumer)
    except LedgerRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "ACQUIRED", "entry": entry["entry"]},
                     sort_keys=True))

    # STEP 2 — only now touch AWS.
    completed = runner(
        ["aws", "ec2", "run-instances",
         "--image-id", "ami-0e307bca04fbd2d80",
         "--instance-type", args.instance_type,
         "--subnet-id", "subnet-01fb2fc3f56bce55e",
         "--security-group-ids", "sg-0fee72d218ac002a7",
         "sg-01adf16d45f0a5820",
         "--iam-instance-profile", "Name=medzen-trainer-profile",
         "--instance-initiated-shutdown-behavior", "terminate",
         "--metadata-options",
         "HttpTokens=required,HttpPutResponseHopLimit=2",
         "--user-data", f"file://{args.userdata}",
         "--query", "Instances[0].InstanceId", "--output", "text"],
        capture_output=True, text=True)
    if completed.returncode != 0:
        print(json.dumps({"status": "LAUNCH_FAILED",
                          "detail": completed.stderr[-400:]}))
        return 2
    print(json.dumps({"status": "LAUNCHED",
                      "instance": completed.stdout.strip()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
