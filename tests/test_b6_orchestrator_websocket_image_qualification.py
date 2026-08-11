from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from scripts.check_b6_service_image import (
    ORCHESTRATOR_WEBSOCKET_PATH,
    WEBSOCKET_GUID,
    _validate_websocket_upgrade,
)


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "platform/decisions/B6-WEBSOCKET-RUNTIME-REMEDIATION-2026-001.json"
PARTIAL_SOURCE_DECISION = (
    ROOT
    / "platform/decisions/B6-WEBSOCKET-PARTIAL-SOURCE-REMEDIATION-2026-001.json"
)


def response(key: str, *, status: str = "101 Switching Protocols") -> bytes:
    accept = base64.b64encode(
        hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode("ascii")).digest()
    ).decode("ascii")
    return (
        f"HTTP/1.1 {status}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode("ascii")


def test_orchestrator_runtime_dependency_is_exactly_pinned():
    requirements = (
        ROOT / "services/speech-orchestrator/requirements.txt"
    ).read_text().splitlines()
    assert requirements.count("websockets==17.0.1") == 1
    dockerfile = (ROOT / "services/speech-orchestrator/Dockerfile").read_text()
    assert "import websockets" in dockerfile


def test_real_upgrade_validator_accepts_only_a_valid_rfc6455_response():
    key = "MDEyMzQ1Njc4OWFiY2RlZg=="
    result = _validate_websocket_upgrade(response(key), key)
    assert result == {
        "http_status": 101,
        "path": ORCHESTRATOR_WEBSOCKET_PATH,
        "protocol": "RFC6455",
        "transport": "real_tcp",
    }
    with pytest.raises(RuntimeError, match="WebSocket upgrade refused"):
        _validate_websocket_upgrade(response(key, status="404 Not Found"), key)


def test_qualification_is_a_real_container_conversation_not_testclient():
    checker = (ROOT / "scripts/check_b6_service_image.py").read_text()
    standard = (ROOT / "platform/standards/runtime-image-hardening-v2.md").read_text()
    for required in (
        '"docker", "run"',
        '"--publish", "127.0.0.1::8080"',
        '"fixture_mounts": "read_only_synthetic_only"',
        "/opt/medzen/services/rag-index",
        "/opt/medzen/services/llm-gateway",
        "socket.create_connection",
        "Sec-WebSocket-Key",
        "_real_websocket_handshake",
        "_exact_streamed_conversation",
        "_orchestrator_websocket_smoke",
        '"scripts/b6_6_probe.py"',
        '"websocket"',
        '"final_transcript", "reply_text", "completed"',
        '"probe_app_binding"',
        "_wait_for_partial_source_refusal",
        '"dependency": "streaming_partial_source"',
        '"status": "PASS_FAIL_CLOSED"',
    ):
        assert required in checker
    assert "TestClient" not in checker
    assert "real TCP/RFC 6455 upgrade" in standard
    assert "supersedes `runtime-image-hardening-v1.md` prospectively" in standard


def test_partial_source_is_packaged_hash_bound_and_not_masked_by_host_mount():
    expected = "f5e6c57c3d8a57d80980ee3741723b36ae810e03aea10d2057fa2c30776a90fc"
    fixture = ROOT / "platform/testdata/orchestrator/b6-window-asr-fixture.json"
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == expected
    dockerfile = (ROOT / "services/speech-orchestrator/Dockerfile").read_text()
    checker = (ROOT / "scripts/check_b6_service_image.py").read_text()
    assert (
        "COPY platform/testdata/orchestrator/b6-window-asr-fixture.json "
        "/opt/medzen/platform/testdata/orchestrator/b6-window-asr-fixture.json"
    ) in dockerfile
    assert f"MEDZEN_STREAM_PARTIAL_FIXTURE_SHA256={expected}" in dockerfile
    assert 'ROOT / "platform/testdata", "/opt/medzen/platform/testdata"' not in checker
    assert "packaged streaming partial source is absent" in checker
    assert "packaged streaming partial source hash differs" in checker


def test_partial_source_decision_refuses_pass_with_note_and_preserves_history():
    decision = json.loads(PARTIAL_SOURCE_DECISION.read_bytes())
    predecessor = decision["immutable_predecessor"]
    assert hashlib.sha256((ROOT / predecessor["path"]).read_bytes()).hexdigest() == (
        predecessor["sha256"]
    )
    assert decision["root_cause"]["dependency"] == "streaming_partial_source"
    assert decision["root_cause"]["qualification_gap"].startswith(
        "The previous local image qualifier mounted platform/testdata"
    )
    assert decision["probe_semantics"]["close_4503_is_correct_fail_closed_behavior"]
    assert decision["probe_semantics"]["pass_with_note_permitted_for_streaming_success_proof"] is False
    prohibited = " ".join(
        decision["prohibited_without_separate_exact_packet_approval"]
    )
    assert "Push the successor image to ECR" in prohibited
    assert "Deploy the successor image" in prohibited


def test_owner_decision_preserves_file_proof_and_aws_packet_boundary():
    decision = json.loads(DECISION.read_bytes())
    prior = decision["immutable_prior_evidence"]
    assert hashlib.sha256((ROOT / prior["path"]).read_bytes()).hexdigest() == (
        prior["sha256"]
    )
    assert prior["file_proof_milestone"] == "PASS_STANDS_NO_RERUN_REQUIRED"
    assert decision["prospective_change"]["dependency"] == "websockets==17.0.1"
    assert hashlib.sha256(
        (ROOT / "platform/standards/runtime-image-hardening-v1.md").read_bytes()
    ).hexdigest() == decision["prospective_change"][
        "historical_v1_sha256_unchanged"
    ]
    prohibited = " ".join(decision["prohibited_until_separate_exact_packet_approval"])
    assert "push the successor image to ECR" in prohibited
    assert "Deploy the successor image" in prohibited
    assert decision["successor_window_boundary"]["live_proof_scope"] == [
        "streaming", "cancellation", "failure_drills", "isolation"
    ]


def test_successor_builder_is_local_one_image_and_refuses_mutable_sources():
    builder = (
        ROOT / "scripts/build_b6_orchestrator_websocket_image.sh"
    ).read_text()
    assert "source worktree must be clean" in builder
    assert "source commit is not the checked-out HEAD" in builder
    assert "services/speech-orchestrator/Dockerfile" in builder
    assert "scripts/check_b6_service_image.py" in builder
    assert "docker scout cves" in builder
    assert "medzen-orchestrator" in builder
    for forbidden in (
        "aws ecr",
        "docker login",
        "docker push",
        "medzen-rag-index",
        "medzen-llm-gateway",
        "medzen-speech-tts-gateway",
    ):
        assert forbidden not in builder
