#!/usr/bin/env python3
"""Fail-closed Helm post-renderer for the reviewed AWS LBC child digest."""

from __future__ import annotations

import sys


TAGGED = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
    "medzen-aws-load-balancer-controller:v3.5.0-c2ebdeae779c"
)
DIGEST_PINNED = (
    "558069890522.dkr.ecr.eu-central-1.amazonaws.com/"
    "medzen-aws-load-balancer-controller@"
    "sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f"
)
UPSTREAM = "public.ecr.aws/eks/aws-load-balancer-controller"


def render(raw: str) -> str:
    tagged_occurrences = raw.count(TAGGED)
    controller_deployment = (
        "kind: Deployment" in raw and "aws-load-balancer-controller" in raw
    )
    # Helm may invoke a post-renderer once for the whole stream or once per
    # rendered file. Files unrelated to the controller Deployment are safe
    # pass-through; the Deployment itself must contain exactly one match.
    if not controller_deployment and tagged_occurrences == 0:
        if UPSTREAM in raw or DIGEST_PINNED in raw:
            raise ValueError("unexpected controller image outside its Deployment")
        return raw
    if tagged_occurrences != 1:
        raise ValueError(
            f"expected exactly one controller image, found {tagged_occurrences}"
        )
    if UPSTREAM in raw or DIGEST_PINNED in raw:
        raise ValueError("unexpected upstream or pre-pinned controller image")
    rendered = raw.replace(TAGGED, DIGEST_PINNED, 1)
    if rendered.count(DIGEST_PINNED) != 1 or TAGGED in rendered:
        raise ValueError("controller digest substitution was incomplete")
    return rendered


def main() -> int:
    try:
        sys.stdout.write(render(sys.stdin.read()))
    except ValueError as exc:
        print(f"REFUSING AWS LBC RENDER: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
