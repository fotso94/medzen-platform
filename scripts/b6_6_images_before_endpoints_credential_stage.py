#!/usr/bin/env python3
"""Adapt the proven credential stage to packet 2026-018 state and bindings."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import b6_6_successor_credential_stage as proven
from scripts.b6_6_images_before_endpoints_bindings import validate


CURRENT_VERSION = "201f9790-72c4-45f7-a05b-967551532aef"
LEGACY_VERSIONS = (
    "daacb67e-fcd1-41e1-bf62-47a3f18c8d0b",
    "d09d567e-9bde-482a-b95a-3cab990a1006",
    "f78c8aa8-2765-4788-9928-dd1ba7c406bf",
)
PACKET_EXPIRES_UTC = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)


def expected_before(*, pending: bool) -> dict[str, list[str]]:
    del pending
    return {CURRENT_VERSION: ["AWSCURRENT"], **{item: [] for item in LEGACY_VERSIONS}}


def persist(
    stage: str,
    status: str,
    receipt: Path,
    authorization: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    value = {
        "record": "B6_2026_018_CREDENTIAL_STAGE_0",
        "stage": stage,
        "status": status,
        "recorded_utc": proven.proven.now(),
        "authorization_id": authorization["id"],
        **payload,
        "plaintext_recorded": False,
    }
    proven.proven.persist(receipt, value)
    return value


def require_receipt(directory: Path, stage: str) -> dict[str, Any]:
    value = proven.json.loads((directory / f"{stage}.json").read_bytes())
    if value.get("stage") != stage or value.get("authorization_id") != "B6-AWS-AUTH-2026-018":
        raise proven.RestorationRefusal(f"durable {stage} receipt differs")
    return value


def configure() -> None:
    proven.CURRENT_VERSION = CURRENT_VERSION
    proven.LEGACY_VERSIONS = LEGACY_VERSIONS
    proven.PACKET_EXPIRES_UTC = PACKET_EXPIRES_UTC
    proven.expected_before = expected_before
    proven._persist = persist
    proven.require_receipt = require_receipt
    proven.validate_bindings = validate


def main() -> int:
    configure()
    return proven.main()


if __name__ == "__main__":
    raise SystemExit(main())
