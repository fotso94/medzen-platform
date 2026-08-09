from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "platform/decisions/B6-STREAMING-2026-001-local-only.json"
DEBT = ROOT / "platform/technical-debt/TD-B6-2026-001-starlette-multipart.json"
REQUIREMENTS = ROOT / "services/speech-orchestrator/requirements.txt"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_owner_decision_is_local_only_and_preserves_prior_contracts():
    decision = json.loads(DECISION.read_bytes())
    assert decision["status"] == "OWNER_AUTHORIZED_LOCAL_ONLY"
    assert decision["repository_start"]["git_commit"] == (
        "37cb5fadc5815b9833cb640d703bdaee48c87fbb"
    )
    assert decision["contract"]["parent_sha256"] == sha(
        ROOT / "platform/contracts/speech-v1.yaml"
    ) == "e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d"
    assert decision["contract"]["file_mode_parent_sha256"] == sha(
        ROOT / "platform/contracts/orchestrator-file-v1.yaml"
    )
    assert decision["contract"]["streaming_contract_sha256"] == sha(
        ROOT / "platform/contracts/orchestrator-stream-v1.yaml"
    )
    assert decision["preserved_state"]["cpu_desired_capacity"] == 0
    assert decision["preserved_state"]["gpu_desired_capacity"] == 0


def test_reviewer_multipart_finding_is_durable_and_dependency_pins_are_exact():
    debt = json.loads(DEBT.read_bytes())
    assert debt["status"] == "ACCEPTED_NO_ACTION_CURRENT_SLICE"
    assert "Starlette 1.6.x" in debt["finding"]
    assert debt["current_verified_runtime"]["starlette"] == "1.3.1"
    requirements = [
        line.strip() for line in REQUIREMENTS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert requirements == [
        "fastapi==0.141.1",
        "python-multipart==0.0.32",
        "PyYAML==6.0.3",
        "starlette==1.3.1",
        "uvicorn==0.52.0",
    ]
    assert all("==" in requirement for requirement in requirements)
    assert debt["future_container_requirements"] == [
        "Install only from services/speech-orchestrator/requirements.txt",
        "Do not install an unpinned override after the requirements file",
        "Run pip check inside the built image",
        "Run the B6.3 valid multipart request against the built image before acceptance",
    ]


def test_streaming_contract_freezes_exit_limits_and_test_layers():
    contract = yaml.safe_load((
        ROOT / "platform/contracts/orchestrator-stream-v1.yaml"
    ).read_bytes())
    assert contract["backpressure"] == {
        "partial_transcripts": {"maximum": 4, "overflow": "drop_oldest"},
        "audio_chunks": {"maximum": 8, "overflow": "pause_upstream"},
        "final_results": {"maximum": 16, "overflow": "refuse_without_mutation"},
        "final_batch": ["final_transcript", "reply_text", "completed"],
        "rule": "final_batch_is_persisted_in_memory_before_first_send_and_is_never_silently_dropped",
    }
    assert contract["cancellation"]["deadline_ms"] == 250
    assert set(contract["test_strategy"]) == {"unit", "integration", "regression"}


def test_streaming_slice_contains_no_aws_sdk_or_real_silero_download_path():
    paths = [
        ROOT / "services/speech-orchestrator/medzen_speech_orchestrator/streaming.py",
        ROOT / "services/speech-orchestrator/medzen_speech_orchestrator/streaming_app.py",
        ROOT / "services/speech-orchestrator/medzen_speech_orchestrator/vad.py",
    ]
    source = "\n".join(path.read_text().casefold() for path in paths)
    for forbidden in (
        "import boto3",
        "import botocore",
        "torch.hub",
        "silero_vad.load",
        "http://",
        "https://",
    ):
        assert forbidden not in source
