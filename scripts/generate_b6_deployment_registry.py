#!/usr/bin/env python3
"""Generate the content-addressed, non-serving B6 deployment registry fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "registry/deployment/b6-v0-synthetic.json"
OUTPUT = ROOT / "platform/generated/registry-ssm/b6-v0-synthetic.json"
CLASSIFICATION = "B6_6_SYNTHETIC_INTEGRATION_ONLY"


def canonical(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def product() -> bytes:
    source = json.loads(SOURCE.read_bytes())
    if (
        not isinstance(source, dict)
        or set(source) != {"schema_version", "classification", "index", "routes"}
        or source["schema_version"] != 1
        or source["classification"] != CLASSIFICATION
        or not isinstance(source["routes"], dict)
        or not source["routes"]
    ):
        raise SystemExit("deployment registry source is malformed")
    snapshot_sha = hashlib.sha256(canonical(source)).hexdigest()
    root = f"/medzen/registry/test/b6/{snapshot_sha}"
    values = {
        "index": canonical(source["index"]).decode("utf-8"),
        **{
            f"routes/{alias}": canonical(route).decode("utf-8")
            for alias, route in sorted(source["routes"].items())
        },
    }
    manifest = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "snapshot_sha256": snapshot_sha,
        "snapshot_material_sha256": snapshot_sha,
        "parameter_value_sha256": {
            relative: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for relative, value in sorted(values.items())
        },
    }
    parameters = [
        {
            "Name": f"{root}/_manifest",
            "Type": "SecureString",
            "Value": canonical(manifest).decode("utf-8"),
            "Version": 1,
        }
    ]
    parameters.extend(
        {
            "Name": f"{root}/{relative}",
            "Type": "SecureString",
            "Value": value,
            "Version": 1,
        }
        for relative, value in sorted(values.items())
    )
    return canonical({"schema_version": 1, "parameters": parameters}, newline=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = product()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != expected:
            raise SystemExit("generated B6 deployment registry fixture is stale")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
