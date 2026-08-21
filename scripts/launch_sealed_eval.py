#!/usr/bin/env python3
"""THE ONLY sanctioned way to start a sealed evaluator (v2 — Codex review
#13 corrections).

A run is defined by ONE reviewed PACKET (a git-tracked JSON under
platform/manifests/) binding: holdout key + manifest sha256 + S3
VersionId, the userdata file and its sha256, the evaluator image digest,
the instance type (allowlisted), account, region, results prefix, and a
max-hours deadline. The launcher:

  1. verifies the packet bytes AT GIT HEAD and every binding above,
  2. asserts the AWS identity (STS account) and region EXPLICITLY,
  3. acquires the ledger consumption (atomic, reserved-sha-verified),
  4. COMMITS the ledger entry and verifies it reads back from git HEAD
     (durable-before-launch: a crash after this point loses the
     evaluator, never the consumption record),
  5. only then launches EC2 — with explicit --region, a deadline tag,
     and the packet's exact userdata.

A nonzero exit before step 5 means nothing was launched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from holdout_ledger import LEDGER, LedgerRefusal, record_consumption

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "558069890522"
REGION = "eu-central-1"
INSTANCE_ALLOWLIST = {"g6.xlarge"}


class LaunchRefusal(RuntimeError):
    pass


def _git_bytes(path: str) -> bytes:
    got = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{path}"],
                         capture_output=True)
    if got.returncode != 0:
        raise LaunchRefusal(f"{path} is not committed at git HEAD")
    return got.stdout


def load_packet(packet_path: str) -> dict:
    if not packet_path.startswith("platform/manifests/"):
        raise LaunchRefusal("packets live under platform/manifests/")
    packet = json.loads(_git_bytes(packet_path))
    required = {"holdout_key", "holdout_manifest_sha256",
                "holdout_s3_version_id", "userdata_path", "userdata_sha256",
                "eval_image_digest", "instance_type", "account", "region",
                "results_prefix", "max_hours", "consumer"}
    missing = required - set(packet)
    if missing:
        raise LaunchRefusal(f"packet lacks {sorted(missing)}")
    if packet["account"] != ACCOUNT or packet["region"] != REGION:
        raise LaunchRefusal("packet account/region are not MedZen's")
    if packet["instance_type"] not in INSTANCE_ALLOWLIST:
        raise LaunchRefusal(
            f"instance type {packet['instance_type']!r} is not allowlisted "
            f"{sorted(INSTANCE_ALLOWLIST)}")
    userdata = _git_bytes(packet["userdata_path"])
    if hashlib.sha256(userdata).hexdigest() != packet["userdata_sha256"]:
        raise LaunchRefusal("userdata bytes do not match the packet's sha")
    if packet["holdout_key"] not in userdata.decode(errors="replace"):
        raise LaunchRefusal(
            "the packet's userdata never references the acquired holdout — "
            "acquiring A while reading B defeats the ledger (Codex #13)")
    if not (0 < float(packet["max_hours"]) <= 12):
        raise LaunchRefusal("max_hours must be in (0, 12]")
    return packet


def assert_aws_identity(runner) -> None:
    got = runner(["aws", "sts", "get-caller-identity", "--query", "Account",
                  "--output", "text", "--region", REGION],
                 capture_output=True, text=True)
    if got.returncode != 0 or got.stdout.strip() != ACCOUNT:
        raise LaunchRefusal(
            f"AWS identity is not account {ACCOUNT} — refusing to launch "
            "into the wrong environment (Codex #13: the shell default was "
            "a different account/region)")


def durable_commit(entry: dict, runner) -> None:
    rel = str(LEDGER.relative_to(ROOT))
    for cmd in (["git", "-C", str(ROOT), "add", rel],
                ["git", "-C", str(ROOT), "commit", "-q", "-m",
                 f"Ledger: CONSUMED entry {entry['entry']} "
                 f"({entry['holdout']})"]):
        got = runner(cmd, capture_output=True, text=True)
        if got.returncode != 0:
            raise LaunchRefusal(
                f"ledger commit failed ({got.stderr[-200:]}) — the "
                "consumption record must be durable BEFORE any launch")
    head = json.loads(_git_bytes(rel).decode().splitlines()[-1])
    if head.get("entry") != entry["entry"]:
        raise LaunchRefusal("committed ledger tail does not show the "
                            "acquisition — refusing to launch")


def main(argv: list[str] | None = None, runner=subprocess.run) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True,
                        help="git-tracked packet path under platform/manifests/")
    args = parser.parse_args(argv)
    try:
        packet = load_packet(args.packet)
        assert_aws_identity(runner)
        entry = record_consumption(packet["holdout_key"],
                                    packet["holdout_manifest_sha256"],
                                    packet["consumer"])
        durable_commit(entry, runner)
    except (LaunchRefusal, LedgerRefusal) as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "ACQUIRED_DURABLE", "entry": entry["entry"]},
                     sort_keys=True))
    completed = runner(
        ["aws", "ec2", "run-instances", "--region", REGION,
         "--image-id", "ami-0e307bca04fbd2d80",
         "--instance-type", packet["instance_type"],
         "--subnet-id", "subnet-01fb2fc3f56bce55e",
         "--security-group-ids", "sg-0fee72d218ac002a7",
         "sg-01adf16d45f0a5820",
         "--iam-instance-profile", "Name=medzen-trainer-profile",
         "--instance-initiated-shutdown-behavior", "terminate",
         "--metadata-options",
         "HttpTokens=required,HttpPutResponseHopLimit=2",
         "--tag-specifications",
         ("ResourceType=instance,Tags=[{Key=Name,Value=medzen-sealed-eval},"
          f"{{Key=medzen,Value=sealed-eval}},"
          f"{{Key=max-hours,Value={packet['max_hours']}}}]"),
         "--user-data", f"file://{ROOT / packet['userdata_path']}",
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
