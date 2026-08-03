#!/usr/bin/env python3
"""Publish the git registry to SSM Parameter Store — git is authored, SSM is read.

Runtime services read SSM (with a TTL cache) so a language change takes effect
without a redeploy. Git remains the reviewed source: nothing reaches SSM that
did not pass validate_registry.py in a merged PR.

DRY RUN BY DEFAULT. --apply writes, and refuses to run on an invalid registry.

    python scripts/publish_registry.py                # show the diff
    python scripts/publish_registry.py --apply        # write (needs approval)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "registry" / "languages"
GATE_DIR = ROOT / "registry" / "gates"
PREFIX = "/medzen/registry"
REGION = "eu-central-1"
PROFILE = "medzen"


def public_view(lang: dict) -> dict:
    """What GET /v1/languages may expose. Provider voice ids are internal —
    a caller must never learn a reference_id."""
    return {
        "alias": lang["alias"],
        "iso_code": lang["iso_code"],
        "display_name": lang.get("display_name", lang["alias"]),
        "status": lang["status"],
        "available": lang["status"] == "production",
        "tts_backend": lang["tts"]["provider"] if lang["tts"]["approved"] else None,
        "fallback_language": lang.get("fallback_language"),
    }


def params() -> dict[str, str]:
    """Build the full SSM key/value set."""
    out: dict[str, str] = {}
    langs = {}
    for f in sorted(LANG_DIR.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        langs[d["alias"]] = d
        out[f"{PREFIX}/languages/{d['alias']}"] = json.dumps(d, sort_keys=True)
    for f in sorted(GATE_DIR.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        out[f"{PREFIX}/gates/{f.stem}"] = json.dumps(d, sort_keys=True)
    # index the orchestrator reads to build /v1/languages
    out[f"{PREFIX}/index"] = json.dumps(
        {"languages": [public_view(v) for v in sorted(langs.values(),
                                                      key=lambda x: x["alias"])]},
        sort_keys=True)
    return out


def aws(*a: str) -> tuple[int, str]:
    p = subprocess.run(["aws", "--profile", PROFILE, "--region", REGION, *a],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def current(name: str) -> str | None:
    rc, out = aws("ssm", "get-parameter", "--name", name,
                  "--query", "Parameter.Value", "--output", "text")
    return out if rc == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to SSM")
    a = ap.parse_args()

    # never publish an invalid registry
    rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_registry.py")],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        print("registry is INVALID — refusing to publish\n")
        print(rc.stdout)
        return 1
    print("registry valid\n")

    desired = params()
    creates, updates, unchanged = [], [], []
    for name, val in desired.items():
        cur = current(name)
        if cur is None:
            creates.append(name)
        elif cur != val:
            updates.append(name)
        else:
            unchanged.append(name)

    print(f"{'APPLY' if a.apply else 'DRY RUN'} — {PREFIX} in {REGION} "
          f"(profile {PROFILE})\n")
    for n in creates:
        print(f"  CREATE   {n}")
    for n in updates:
        print(f"  UPDATE   {n}")
    for n in unchanged:
        print(f"  ok       {n}")

    if not a.apply:
        print(f"\n{len(creates)} to create, {len(updates)} to update. "
              f"Re-run with --apply to write.")
        return 0

    for name in creates + updates:
        rc, out = aws("ssm", "put-parameter", "--name", name, "--type", "String",
                      "--overwrite", "--value", desired[name])
        if rc != 0:
            print(f"  FAILED {name}: {out[:160]}")
            return 1
        print(f"  wrote  {name}")
    print(f"\npublished {len(creates) + len(updates)} parameter(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
