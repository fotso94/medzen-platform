from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "platform/evidence/B6-LOCAL-ENGINEERING-2026-005-tts.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record() -> dict:
    return json.loads(EVIDENCE.read_bytes())


def test_tts_exit_record_binds_every_named_source():
    evidence = record()
    assert evidence["status"] == "VERIFIED_LOCAL_COMPLETE"
    for relative, expected in evidence["source_bindings"].items():
        assert sha(ROOT / relative) == expected, relative


def test_tts_exit_proves_text_preservation_and_cloud_zero():
    evidence = record()
    assert evidence["outcome"]["exit"] == (
        "LOCAL_TTS_COMPLETE_PENDING_INDEPENDENT_PR_REVIEW"
    )
    assert evidence["outcome"]["text_only_default"] == "PASS_HTTP_200_SUCCESS"
    assert evidence["outcome"]["fake_fish_success"] == "PASS"
    assert evidence["outcome"]["fish_timeout_and_error"] == (
        "PASS_TEXT_PRESERVED_HTTP_200_TEXT_ONLY"
    )
    assert evidence["outcome"]["http_500_cascade_count"] == 0
    assert all(value == 0 for value in evidence["aws_and_governance"].values())
