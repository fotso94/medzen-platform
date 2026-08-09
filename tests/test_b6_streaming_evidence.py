from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    ROOT / "platform/evidence/B6-LOCAL-ENGINEERING-2026-004-streaming.json"
)
EVIDENCE_COMMIT = "c9147a3998db3bf15e053298bf80141e33b60a09"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def historical_sha(relative: str) -> str:
    value = subprocess.run(
        ["git", "show", f"{EVIDENCE_COMMIT}:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(value).hexdigest()


def record() -> dict:
    return json.loads(EVIDENCE.read_bytes())


def test_streaming_exit_record_binds_every_named_source():
    evidence = record()
    assert evidence["status"] == "VERIFIED_LOCAL_COMPLETE"
    for relative, expected in evidence["source_bindings"].items():
        assert historical_sha(relative) == expected, relative


def test_streaming_exit_is_local_only_and_preserves_cloud_zero():
    evidence = record()
    assert evidence["outcome"]["exit"] == (
        "LOCAL_STREAMING_COMPLETE_PENDING_INDEPENDENT_PR_REVIEW"
    )
    assert evidence["outcome"]["queue_limits"] == {
        "partial_transcripts": 4,
        "audio_output_chunks": 8,
        "final_results": 16,
    }
    assert evidence["outcome"]["final_result_preservation"] == (
        "PASS_PERSISTED_BEFORE_FIRST_SEND"
    )
    assert evidence["outcome"]["cancellation_and_barge_in"] == (
        "PASS_WITHIN_250_MS"
    )
    assert all(value == 0 for value in evidence["aws_and_governance"].values())
