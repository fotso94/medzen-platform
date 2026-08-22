from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORD = (
    ROOT / "platform/evidence/B6-LOCAL-ENGINEERING-2026-001-contract-and-rag.json"
)


def value() -> dict:
    return json.loads(RECORD.read_bytes())


def test_local_engineering_record_binds_every_named_source():
    # CURRENT bindings live in the APPEND-ONLY successor record; the
    # -001 record stays byte-frozen as the closed v1 proof (Codex B6
    # review round 2: publish successors, never amend history)
    successor = json.loads((ROOT / "platform/evidence/"
        "B6-LOCAL-ENGINEERING-2026-008-b6v2.json").read_bytes())
    for relative, expected in successor["source_bindings"].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert actual == expected, relative


def test_contract_fixture_and_schema_counts_are_exact():
    adoption = value()["contract_adoption"]
    assert len(list((ROOT / "platform/contracts/schemas/speech-v1").glob("*.json"))) == (
        adoption["schema_count"]
    )
    assert len(list((ROOT / "platform/contracts/fixtures/speech-v1").glob("*.json"))) == (
        adoption["golden_fixture_count"]
    )


def test_local_rag_exit_does_not_claim_clinical_or_cloud_readiness():
    record = value()
    rag = record["b6_1_local_rag"]
    assert rag["outcome"] == "LOCAL_EXIT_COMPLETE"
    assert rag["clinical_content_approved"] is False
    assert rag["external_reachability"] is False
    assert all(amount == 0 for amount in record["aws_and_governance"].values())
