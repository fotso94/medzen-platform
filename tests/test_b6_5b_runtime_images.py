from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICES = (
    "rag-index",
    "llm-gateway",
    "speech-orchestrator",
    "speech-tts-gateway",
)
PINNED_BASE = (
    "python:3.12-alpine3.22@sha256:"
    "a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322"
)


def test_every_new_service_has_the_same_hardened_runtime_boundary():
    for service in SERVICES:
        dockerfile = (ROOT / "services" / service / "Dockerfile").read_text()
        assert dockerfile.count(PINNED_BASE) == 2
        assert "FROM scratch AS runtime" in dockerfile
        assert "FROM " + PINNED_BASE + " AS builder" in dockerfile
        assert "USER 10001:10001" in dockerfile
        assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
        assert "/lib/apk" in dockerfile
        assert "/usr/local/bin/pip*" in dockerfile
        assert "/usr/local/lib/python3.12/ensurepip" in dockerfile
        assert "COPY --from=builder /opt/site-packages" in dockerfile
        runtime = dockerfile.split("FROM scratch AS runtime", 1)[1]
        for forbidden in ("RUN apk", "RUN apt", "RUN pip", "requirements.txt"):
            assert forbidden not in runtime


def test_requirements_are_exactly_pinned_and_no_mutable_ranges_are_used():
    pin = re.compile(r"^[A-Za-z0-9_.-]+==[^=<>~!]+$")
    for service in SERVICES:
        lines = (
            ROOT / "services" / service / "requirements.txt"
        ).read_text().splitlines()
        requirements = [line for line in lines if line and not line.startswith("#")]
        assert requirements
        assert all(pin.fullmatch(line) for line in requirements)
    deployed = (
        ROOT / "services/speech-orchestrator/requirements.deployed.txt"
    ).read_text().splitlines()
    deployed_requirements = [
        line for line in deployed if line and not line.startswith("#")
    ]
    assert deployed_requirements[0] == "-r requirements.txt"
    assert all(pin.fullmatch(line) for line in deployed_requirements[1:])


def test_docker_context_excludes_historical_artifacts_and_infrastructure_state():
    ignore = (ROOT / ".dockerignore").read_text()
    assert ignore.startswith("**\n")
    assert "!services/**" in ignore
    assert "!platform/testdata/rag-index/**" in ignore
    assert "!registry/languages/**" in ignore
    assert "!infra" not in ignore
    assert "!artifacts" not in ignore
