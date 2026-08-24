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


def required_reviewer_count(environment: dict[str, Any]) -> int:
    """The number of required reviewers the environment enforces (users +
    teams), across the modern `protection_rules` shape."""
    total = 0
    for rule in environment.get("protection_rules") or []:
        if rule.get("type") == "required_reviewers":
            total += len(rule.get("reviewers") or [])
    return total


def check_environment(name: str, environment: dict[str, Any] | None) -> list[str]:
    """Return failure strings for one environment — empty means protected."""
    if environment is None:
        return [f"environment {name!r} does not exist — create it with the "
                "OWNER as a required reviewer before activation"]
    # a 404 body from the GitHub API (or our sentinel) is 'missing'
    if environment.get("__missing__") or environment.get("message") == "Not Found":
        return [f"environment {name!r} does not exist (GitHub returned Not "
                "Found) — create it with the OWNER as a required reviewer"]
    count = required_reviewer_count(environment)
    if count < 1:
        return [f"environment {name!r} has NO required reviewers — owner "
                "approval is not enforced"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment", action="append", default=[], metavar="NAME=PATH",
        help="an environment name and the path to its `gh api environments/"
             "<name>` JSON; repeat for each")
    args = parser.parse_args(argv)

    supplied: dict[str, dict[str, Any]] = {}
    for item in args.environment:
        name, _, path = item.partition("=")
        if not name or not path:
            raise SystemExit(f"--environment {item!r} must be NAME=PATH")
        from pathlib import Path
        supplied[name] = json.loads(Path(path).read_bytes())

    failures: list[str] = []
    for name in REQUIRED_ENVIRONMENTS:
        failures.extend(check_environment(name, supplied.get(name)))

    report = {"verdict": "PASS" if not failures else "FAIL",
              "checked": list(REQUIRED_ENVIRONMENTS),
              "failures": failures}
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
