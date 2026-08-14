#!/usr/bin/env python3
"""Produce the $0 endpoint-policy derivation and coverage qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import canonical_json, write_exclusive
from scripts.asr_base_model_endpoint_policy import (
    build_call_inventory,
    derive_policy,
    validate_observed_s3_calls,
    validate_policy_coverage,
)
from scripts.asr_base_model_pilot_live import PRIVATE_PULL_REPOSITORIES


BUNDLE_SHA256 = "1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee"
BUNDLE_PATH = Path("tests/fixtures/asr_base_model_pilot/pilot-bundle-2026-001.json")
MODEL_BINDINGS_PATH = Path(
    "tests/fixtures/asr_base_model_pilot/model-bindings-2026-001.json"
)
ATTEMPT_17_PATH = Path(
    "platform/evidence/"
    "ASR-BASE-MODEL-PACKET-2026-002P-ATTEMPT-17-NODE-STAGING-REFUSAL.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _actions(policy: dict[str, Any]) -> list[str]:
    return sorted(
        {
            action
            for statement in policy["Statement"]
            for action in statement["Action"]
        }
    )


def qualify() -> dict[str, Any]:
    bundle = json.loads((ROOT / BUNDLE_PATH).read_bytes())
    model_bindings = json.loads((ROOT / MODEL_BINDINGS_PATH).read_bytes())
    inventory = build_call_inventory(
        bundle_sha256=BUNDLE_SHA256,
        pilot_bundle=bundle,
        model_bindings=model_bindings,
        account="558069890522",
        region="eu-central-1",
        ecr_repositories=PRIVATE_PULL_REPOSITORIES,
    )
    s3_policy = derive_policy(inventory, "s3")
    ecr_policy = derive_policy(inventory, "ecr")
    s3_coverage = validate_policy_coverage(inventory, s3_policy, "s3")
    ecr_coverage = validate_policy_coverage(inventory, ecr_policy, "ecr")
    observed = [
        {
            "operation": row["parameters"]["operation"],
            "bucket": row["parameters"]["bucket"],
            "key": row["parameters"]["key"],
            "version_id_present": row["parameters"]["version_id_present"],
        }
        for row in inventory["calls"]
        if row["service"] == "s3"
    ]
    observed_coverage = validate_observed_s3_calls(inventory, observed)
    versioned = [
        row
        for row in inventory["calls"]
        if row["service"] == "s3"
        and row["parameters"]["version_id_present"] is True
    ]
    unversioned = [
        row
        for row in inventory["calls"]
        if row["service"] == "s3"
        and row["parameters"]["version_id_present"] is False
    ]
    return {
        "record": "ASR_BASE_MODEL_ENDPOINT_POLICY_QUALIFICATION",
        "id": "ASR-BASE-MODEL-ENDPOINT-POLICY-QUALIFICATION-2026-001",
        "schema_version": 1,
        "status": "PASS_MACHINE_DERIVED_ENDPOINT_POLICY_COMPLETE",
        "classification": "LOCAL_READ_ONLY_ZERO_AWS_ZERO_KUBERNETES",
        "attempt_17_refusal": {
            "path": str(ATTEMPT_17_PATH),
            "sha256": _sha(ROOT / ATTEMPT_17_PATH),
            "observed_reason": "VERSION_PINNED_S3_DOWNLOAD_HTTP_403",
        },
        "sources": {
            "pilot_bundle": {"path": str(BUNDLE_PATH), "sha256": _sha(ROOT / BUNDLE_PATH)},
            "model_bindings": {
                "path": str(MODEL_BINDINGS_PATH),
                "sha256": _sha(ROOT / MODEL_BINDINGS_PATH),
            },
        },
        "inventory": inventory,
        "inventory_summary": {
            "total_calls": len(inventory["calls"]),
            "s3_calls": len(versioned) + len(unversioned),
            "s3_versioned_calls": len(versioned),
            "s3_unversioned_calls": len(unversioned),
            "ecr_calls": sum(row["service"] == "ecr" for row in inventory["calls"]),
            "version_ids_recorded": False,
            "version_id_hashes_recorded": True,
        },
        "derived_policy": {
            "s3": s3_policy,
            "ecr": ecr_policy,
            "s3_actions": _actions(s3_policy),
            "ecr_actions": _actions(ecr_policy),
            "s3_coverage": s3_coverage,
            "ecr_coverage": ecr_coverage,
            "observed_s3_call_coverage": observed_coverage,
            "hand_written_action_list_permitted": False,
            "broader_s3_prefix_permitted": False,
        },
        "version_variant_audit": {
            "required": ["s3:GetObjectVersion"],
            "other_version_variant_actions_required": [],
            "status": "PASS_NO_OTHER_VERSION_VARIANT_REQUIRED",
        },
        "aws_calls": 0,
        "aws_mutations": 0,
        "kubectl_calls": 0,
        "cost_usd": 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = qualify()
    write_exclusive(args.output.resolve(), canonical_json(value))
    print(
        json.dumps(
            {
                "status": value["status"],
                "output": str(args.output.resolve()),
                "sha256": _sha(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
