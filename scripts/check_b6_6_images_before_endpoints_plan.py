#!/usr/bin/env python3
"""Guard the split controller-first and endpoint-second B6.6 plans."""
from __future__ import annotations

import argparse
import copy
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import check_b6_6_successor_window_plan as successor
from scripts import check_b6_6_window_plan as proven


CONTROLLER = "helm_release.b6_load_balancer_controller[0]"
ENDPOINT_ADDRESSES = proven.ADDRESSES - {CONTROLLER}


def validate_controller(plan: dict[str, Any]) -> None:
    actual = proven.changes(plan)
    if actual != {CONTROLLER: ["create"]}:
        raise ValueError(f"controller delta differs: {actual!r}")
    release = proven._after(plan, CONTROLLER)
    if (
        release.get("name") != "aws-load-balancer-controller"
        or release.get("namespace") != "kube-system"
        or release.get("chart") != "aws-load-balancer-controller"
        or release.get("version") != "3.5.0"
        or release.get("repository") != "https://aws.github.io/eks-charts"
        or release.get("atomic") is not True
        or release.get("wait") is not True
        or release.get("wait_for_jobs") is not True
    ):
        raise ValueError("controller release boundary differs")


def validate_endpoints(plan: dict[str, Any]) -> None:
    actual = proven.changes(plan)
    if actual != {address: ["create"] for address in ENDPOINT_ADDRESSES}:
        raise ValueError(f"endpoint/probe delta differs: {actual!r}")
    compatible = copy.deepcopy(plan)
    release = next(
        item for item in compatible["resource_changes"] if item["address"] == CONTROLLER
    )
    if release["change"]["actions"] != ["no-op"]:
        raise ValueError("controller is not stable before endpoint creation")
    release["change"]["actions"] = ["create"]
    successor.validate_create(compatible)


def validate_endpoint_preview(plan: dict[str, Any]) -> None:
    actual = proven.changes(plan)
    if actual != {address: ["create"] for address in ENDPOINT_ADDRESSES}:
        raise ValueError(f"endpoint preview delta differs: {actual!r}")
    compatible = copy.deepcopy(plan)
    compatible["resource_changes"].append({
        "address": CONTROLLER,
        "change": {"actions": ["create"], "after": {}},
    })
    successor.validate_create(compatible)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("controller", "preview-endpoints", "endpoints", "destroy", "cleanup"),
    )
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = proven.load(args.plan)
        {
            "controller": validate_controller,
            "preview-endpoints": validate_endpoint_preview,
            "endpoints": validate_endpoints,
            "destroy": proven.validate_destroy,
            "cleanup": proven.validate_cleanup,
        }[args.mode](plan)
    except (
        OSError,
        KeyError,
        ValueError,
        StopIteration,
        subprocess.SubprocessError,
    ) as exc:
        print(f"REFUSING B6.6 IMAGES-FIRST {args.mode.upper()}: {exc}", file=sys.stderr)
        return 2
    print(f"PASS_B6_6_IMAGES_FIRST_{args.mode.upper()} changes={len(proven.changes(plan))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
