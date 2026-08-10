from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_r5_audit_covers_every_canonical_verifier_and_has_no_open_findings() -> None:
    value = json.loads(
        (ROOT / "platform/evidence/B6-R5-VERIFIER-AUDIT-2026-001.json").read_bytes()
    )
    assert value["status"] == "PASS_WITH_CORRECTIONS"
    assert value["unresolved_r5_findings"] == 0
    assert value["deviations"] == []
    assert len(value["scope"]["files"]) == 19
    assert "scripts/b6_6_stage_a.py" in value["scope"]["files"]
    assert len(value["corrections"]) == 7


def test_corrected_verifiers_do_not_restore_incidental_whole_shape_checks() -> None:
    fargate = (ROOT / "scripts/b6_6_fargate_probe.py").read_text()
    credential = (ROOT / "scripts/b6_6_credential.py").read_text()
    deadline = (ROOT / "scripts/b6_6_deadline.py").read_text()
    endpoints = (ROOT / "scripts/b6_6_probe_endpoints.py").read_text()
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    lbc = (ROOT / "scripts/b6_6_lbc_runtime.py").read_text()
    assert 'container.get("linuxParameters") !=' not in fargate
    assert '"ALL" not in set(capabilities.get("drop", []))' in fargate
    assert 'versions.get(value_sha256) != ["AWSCURRENT"]' not in credential
    assert '"AWSCURRENT" not in set(versions.get(value_sha256, []))' in credential
    assert 'scaling != {"minSize"' not in deadline
    assert "len(allowed) != 1" in endpoints
    assert "set(configured_paths) != set(expected_paths)" in lbc
    assert "jq -e --argjson expected" in operations
