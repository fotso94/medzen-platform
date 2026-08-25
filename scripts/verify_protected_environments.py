#!/usr/bin/env python3
"""Verify the Arm-2 protected GitHub environments exist AND enforce owner
approval BEFORE any Terraform apply or repo-variable set (Codex review #27
finding 1/6).

GitHub silently creates a referenced-but-missing environment WITHOUT protection
rules the first time a workflow names it, which would bypass owner approval. The
activation runbook therefore REQUIRES this check to pass first: both
`trainer-image-publish` and `arm2-calibration` must already exist with at least
one required reviewer.

The owner runs it with their own GitHub auth:
    gh api repos/fotso94/medzen-platform/environments/arm2-calibration \
        > /tmp/arm2-calibration.json
    gh api repos/fotso94/medzen-platform/environments/trainer-image-publish \
        > /tmp/trainer-image-publish.json
    python -m scripts.verify_protected_environments \
        --environment arm2-calibration=/tmp/arm2-calibration.json \
        --environment trainer-image-publish=/tmp/trainer-image-publish.json

The parsing/enforcement core (`required_reviewer_count`, `check_environment`) is
pure and unit-tested; only the CLI reads files.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

REQUIRED_ENVIRONMENTS = ("trainer-image-publish", "arm2-calibration")
# environments a per-workflow preflight may verify with --only-supplied, beyond
# the two-environment activation gate above (Codex round 35 scope 5: the
# nomination-mint preflight verifies ITS environment without changing the
# owner's pre-apply gate)
KNOWN_ENVIRONMENTS = REQUIRED_ENVIRONMENTS + (
    "arm2-nomination-mint-producer", "arm2-nomination-mint-sealed",
    "arm2-nomination-mint-mint")
# the OWNER whose approval every activation environment must require (Codex
# review #28 finding 1: accepting any reviewer, or a mismatched environment
# response, let the existing arm-launch-approval JSON pass as both required
# environments). Bind BOTH the login and the numeric id.
OWNER_LOGIN = "fotso94"
OWNER_ID = 16901658


def required_reviewer_count(environment: dict[str, Any]) -> int:
    """The number of required reviewers the environment enforces (users +
    teams), across the modern `protection_rules` shape."""
    total = 0
    for rule in environment.get("protection_rules") or []:
        if rule.get("type") == "required_reviewers":
            total += len(rule.get("reviewers") or [])
    return total


def _owner_is_required_reviewer(environment: dict[str, Any]) -> bool:
    """True iff the OWNER (by login OR numeric id) is a required reviewer."""
    for rule in environment.get("protection_rules") or []:
        if rule.get("type") != "required_reviewers":
            continue
        for entry in rule.get("reviewers") or []:
            reviewer = entry.get("reviewer") or {}
            if str(reviewer.get("login", "")).lower() == OWNER_LOGIN \
                    or reviewer.get("id") == OWNER_ID:
                return True
    return False


def check_environment(name: str, environment: dict[str, Any] | None) -> list[str]:
    """Return failure strings for one environment — empty means it exists, is
    the RIGHT environment, and requires the OWNER's approval."""
    if environment is None:
        return [f"environment {name!r} does not exist — create it with the "
                "OWNER as a required reviewer before activation"]
    # a 404 body from the GitHub API (or our sentinel) is 'missing'
    if environment.get("__missing__") or environment.get("message") == "Not Found":
        return [f"environment {name!r} does not exist (GitHub returned Not "
                "Found) — create it with the OWNER as a required reviewer"]
    # Codex #28 finding 1: the response MUST be for the requested environment —
    # supplying arm-launch-approval's JSON for arm2-calibration must FAIL
    actual = str(environment.get("name", ""))
    if actual != name:
        return [f"environment JSON is for {actual!r}, not the requested "
                f"{name!r} — wrong-environment response rejected"]
    if required_reviewer_count(environment) < 1:
        return [f"environment {name!r} has NO required reviewers — owner "
                "approval is not enforced"]
    # Codex #28 finding 1: ANY reviewer is not enough — the OWNER specifically
    if not _owner_is_required_reviewer(environment):
        return [f"environment {name!r} does not require the OWNER "
                f"({OWNER_LOGIN}/id {OWNER_ID}) as a reviewer"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment", action="append", default=[], metavar="NAME=PATH",
        help="an environment name and the path to its GitHub `environments/"
             "<name>` JSON; repeat for each")
    parser.add_argument(
        "--only-supplied", action="store_true",
        help="check only the environments supplied (a per-workflow preflight "
             "verifies its ONE environment); default requires BOTH activation "
             "environments (the owner's pre-apply gate)")
    args = parser.parse_args(argv)

    supplied: dict[str, dict[str, Any]] = {}
    for item in args.environment:
        name, _, path = item.partition("=")
        if not name or not path:
            raise SystemExit(f"--environment {item!r} must be NAME=PATH")
        from pathlib import Path
        supplied[name] = json.loads(Path(path).read_bytes())

    names = sorted(supplied) if args.only_supplied else REQUIRED_ENVIRONMENTS
    if args.only_supplied and not names:
        raise SystemExit("--only-supplied requires at least one --environment")
    failures: list[str] = []
    for name in names:
        if name not in KNOWN_ENVIRONMENTS:
            failures.append(f"{name!r} is not an activation environment")
            continue
        failures.extend(check_environment(name, supplied.get(name)))

    report = {"verdict": "PASS" if not failures else "FAIL",
              "checked": list(names),
              "failures": failures}
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
