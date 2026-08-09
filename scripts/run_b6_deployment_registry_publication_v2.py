#!/usr/bin/env python3
"""Packet 2026-007A runner with an explicit deployed-registry policy."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import run_b6_deployment_registry_publication as v1
from medzen_speech_orchestrator.registry import (
    DEPLOYED_CLASSIFICATION,
    RegistryRouter as _RegistryRouter,
)


def deployed_router(store: Any, root: str) -> _RegistryRouter:
    """Never allow the local-fixture classification for an AWS snapshot."""

    return _RegistryRouter(
        store,
        root,
        expected_classification=DEPLOYED_CLASSIFICATION,
    )


# Preserve every reviewed v1 publication and rollback control while replacing
# only the classification policy selected at the final read-back gate.
v1.RegistryRouter = deployed_router


def main() -> int:
    for flag in ("--authorization", "--receipt"):
        try:
            value = sys.argv[sys.argv.index(flag) + 1]
        except (ValueError, IndexError):
            print(f"REFUSING: {flag} requires an absolute path", file=sys.stderr)
            return 2
        if not Path(value).is_absolute():
            print(f"REFUSING: {flag} requires an absolute path", file=sys.stderr)
            return 2
    return v1.main()


if __name__ == "__main__":
    raise SystemExit(main())
