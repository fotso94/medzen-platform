#!/usr/bin/env python3
"""Verify GENUINE owner enforcement before Arm-2 nomination-mint activation
(Codex round 37 #1).

CODEOWNERS is ADVISORY unless a branch-protection rule / ruleset actually
requires CODEOWNER review on the default branch — and unless CODEOWNERS
protects ITSELF and every trust-bearing path. Without that, a repository writer
can rewrite an approval record, a ledger and the packet together, defeating the
whole authentication root. The activation runbook REQUIRES this check to pass
first; the owner runs it with their own GitHub auth:

    gh api repos/fotso94/medzen-platform/branches/master/protection > /tmp/prot.json
    python -m scripts.verify_branch_protection \
        --protection /tmp/prot.json --codeowners .github/CODEOWNERS

The parsing/enforcement core is pure and unit-tested; only the CLI reads files.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# every trust-bearing path CODEOWNERS must cover (owner review required to
# change any of them). Paths are matched as CODEOWNERS pattern prefixes.
REQUIRED_CODEOWNER_PATHS = (
    "/.github/CODEOWNERS",                     # CODEOWNERS must protect itself
    "/platform/evidence/ARM2-TRAINING-INDEX-ADMISSION-LEDGER.jsonl",
    "/platform/evidence/ARM2-SEALED-EXCLUSION-LEDGER.jsonl",
    "/platform/decisions/",                    # approval / partition / review records + packet
    "/scripts/mint_arm2_nomination_split.py",
    "/scripts/build_arm2_training_identity_index.py",
    "/scripts/build_arm2_sealed_identity_index.py",
    "/scripts/build_arm2_exposure_index.py",
    "/scripts/verify_branch_protection.py",
    "/scripts/verify_protected_environments.py",
    "/scripts/requirements/arm2-launch.txt",   # the hash-locked dependency closure
    "/.github/workflows/arm2-nomination-mint.yml",
    "/.github/workflows/arm2-nomination-mint-producer-exec.yml",
    "/.github/workflows/arm2-nomination-mint-sealed-exec.yml",
    "/.github/workflows/arm2-nomination-mint-mint-exec.yml",
    "/infra/arm2_nomination_mint_role.tf",
    "/platform/iam/medzen-arm2-nomination-mint-role.json",
    "/platform/iam/medzen-arm2-training-index-role.json",
    "/platform/iam/medzen-arm2-sealed-identity-role.json",
    "/platform/manifests/B5-UNIVERSAL-ARM2-EXPOSURE-INDEX-2026-001.json",
    "/platform/manifests/B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json",
    "/platform/manifests/B5-ARM1-LINGALA-SENTINEL-2026-001.json",
    "/platform/manifests/dev-sentinels/",     # the 60-row dev sentinels (candidate identities)
)

# the exact owner handle every trust-bearing path must be owned by (Codex round
# 38 #4: "the exact owner identity")
OWNER_HANDLE = "@fotso94"


def require_code_owner_review(protection: dict[str, Any] | None) -> list[str]:
    """Failures unless the default-branch protection REQUIRES code-owner review
    with >= 1 approver. Accepts the classic branch-protection shape."""
    if not isinstance(protection, dict) or protection.get("__missing__"):
        return ["default branch is NOT protected (GitHub returned no protection)"]
    if protection.get("message") == "Branch not protected":
        return ["default branch is NOT protected (GitHub: 'Branch not protected')"]
    prr = protection.get("required_pull_request_reviews")
    if not isinstance(prr, dict):
        return ["branch protection does not require pull-request reviews"]
    failures: list[str] = []
    if not prr.get("require_code_owner_reviews"):
        failures.append("branch protection does not require CODEOWNER reviews "
                        "— CODEOWNERS is advisory without it")
    if int(prr.get("required_approving_review_count") or 0) < 1:
        failures.append("branch protection requires 0 approving reviews")
    # Codex round 38 #4: a weak protection (admin bypass, force-push, deletion,
    # stale reviews, no last-push approval, bypass allowances) is not enough.
    if not (protection.get("enforce_admins") or {}).get("enabled"):
        failures.append("branch protection does not enforce admins (admins can "
                        "bypass CODEOWNER review)")
    if (protection.get("allow_force_pushes") or {}).get("enabled"):
        failures.append("branch protection allows force pushes")
    if (protection.get("allow_deletions") or {}).get("enabled"):
        failures.append("branch protection allows branch deletion")
    if not prr.get("dismiss_stale_reviews"):
        failures.append("branch protection does not dismiss stale reviews")
    if not prr.get("require_last_push_approval"):
        failures.append("branch protection does not require approval of the "
                        "latest push")
    if prr.get("bypass_pull_request_allowances") not in (None, {}, [], ) \
            and any((prr.get("bypass_pull_request_allowances") or {}).get(k)
                    for k in ("users", "teams", "apps")):
        failures.append("branch protection grants pull-request bypass allowances")
    return failures


def _codeowners_patterns(codeowners_text: str) -> list[tuple[str, list[str]]]:
    patterns = []
    for line in codeowners_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        owners = [o for o in parts[1:] if o.startswith("@")]
        if len(parts) >= 2 and owners:
            patterns.append((parts[0], owners))
    return patterns


def codeowners_covers(codeowners_text: str,
                      required: tuple[str, ...] = REQUIRED_CODEOWNER_PATHS,
                      owner: str = OWNER_HANDLE) -> list[str]:
    """Failures for any required path not covered by a pattern owned by EXACTLY
    the owner handle. A directory pattern (trailing '/') covers paths beneath
    it. Codex round 38 #4: the exact owner identity, not merely 'some @'."""
    patterns = _codeowners_patterns(codeowners_text)
    failures = []
    for path in required:
        owners = None
        for pat, pat_owners in patterns:
            if pat == path or (pat.endswith("/") and path.startswith(pat)):
                owners = pat_owners
                break
        if owners is None:
            failures.append(f"CODEOWNERS does not require owner review on {path}")
        elif owner not in owners:
            failures.append(f"CODEOWNERS path {path} is not owned by {owner}")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protection", metavar="PATH",
                    help="branch protection JSON (gh api .../branches/<b>/protection)")
    ap.add_argument("--codeowners", default=".github/CODEOWNERS")
    args = ap.parse_args(argv)
    failures: list[str] = []
    if args.protection:
        try:
            prot = json.loads(Path(args.protection).read_bytes())
        except (ValueError, OSError):
            prot = {"__missing__": True}
        failures.extend(require_code_owner_review(prot))
    else:
        failures.append("no --protection JSON supplied — cannot confirm the "
                        "default branch requires CODEOWNER review")
    failures.extend(codeowners_covers(Path(args.codeowners).read_text()))
    report = {"verdict": "PASS" if not failures else "FAIL",
              "failures": failures}
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
