#!/usr/bin/env python3
"""Render the reviewed B6.6 workload or ingress slice deterministically."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "platform/k8s/b6-6/integration-window.yaml"


def render(mode: str) -> str:
    documents = [item for item in yaml.safe_load_all(MANIFEST.read_text()) if item]
    selected = [
        item for item in documents
        if (item.get("kind") == "Ingress") == (mode == "ingress")
    ]
    if mode == "ingress" and (
        len(selected) != 1
        or selected[0].get("metadata", {}).get("name") != "speech-orchestrator-b6-window"
    ):
        raise ValueError("exact B6.6 ingress slice is absent")
    if mode == "pre-endpoint" and (
        len(selected) != len(documents) - 1
        or any(item.get("kind") == "Ingress" for item in selected)
    ):
        raise ValueError("pre-endpoint manifest slice differs")
    return "".join("---\n" + yaml.safe_dump(item, sort_keys=False) for item in selected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("pre-endpoint", "ingress"))
    args = parser.parse_args()
    try:
        sys.stdout.write(render(args.mode))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"REFUSING: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
