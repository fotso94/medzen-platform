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


def test_qualification_is_a_real_container_handshake_not_testclient():
    checker = (ROOT / "scripts/check_b6_service_image.py").read_text()
    standard = (ROOT / "platform/standards/runtime-image-hardening-v2.md").read_text()
    for required in (
        '"docker", "run"',
        '"--publish", "127.0.0.1::8080"',
        '"fixture_mounts": "read_only_synthetic_only"',
        "/opt/medzen/services/rag-index",
        "socket.create_connection",
        "Sec-WebSocket-Key",
        "_real_websocket_handshake",
        "_orchestrator_websocket_smoke",
    ):
        assert required in checker
    assert "TestClient" not in checker
    assert "real TCP/RFC 6455 upgrade" in standard
    assert "supersedes `runtime-image-hardening-v1.md` prospectively" in standard


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
