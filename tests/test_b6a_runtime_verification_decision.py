from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "platform/decisions/B6A-DESIGN-2026-002-runtime-verification-boundary.json"
FAILED = ROOT / "platform/evidence/B6A-PACKET-2026-003A-FAILED-IMAGE-SCAN.json"
PACKET_005 = ROOT / "platform/decisions/B6A-AWS-CHANGE-PACKET-2026-005-ecr-scan-rules.md"


def test_design_decision_preserves_failed_packet_and_authorizes_no_aws():
    decision = json.loads(DECISION.read_text())
    assert decision["status"] == "owner-approved-local-engineering-only"
    assert decision["repository_at_authorization"]["git_commit"] == (
        "1ca36b06da9982db0a7ec66b350476825bc2f3e2"
    )
    preserved = {
        item["path"]: item["sha256"]
        for item in decision["supersedes_by_reference_only"]
    }
    relative = "platform/evidence/B6A-PACKET-2026-003A-FAILED-IMAGE-SCAN.json"
    assert preserved[relative] == hashlib.sha256(FAILED.read_bytes()).hexdigest()
    assert "Any AWS mutation under this design decision" in decision[
        "prohibited_operations"
    ]
    assert decision["image_security_direction"]["waiver"].startswith("No B4")


def test_scan_packet_is_blocked_and_excludes_separately_owned_tts_repo():
    text = PACKET_005.read_text()
    assert "BLOCKED — NOT AUTHORIZED" in text
    assert "1 add / 0 change / 0 destroy" in text
    assert "has **not** been applied" in text
    assert "medzen-tts-gateway` is separately owned and must not be matched" in text
    assert "medzen-*" in text


def test_old_vulnerable_runtime_base_is_removed_from_both_services():
    old = "cab2dbf575e971934a81e4622f5aba17aa7929719bd7e31033a3a83b97fd0464"
    for path in (
        ROOT / "services/model-loader/Dockerfile",
        ROOT / "services/asr-runtime/Dockerfile",
    ):
        assert old not in path.read_text()
