from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "platform/decisions/B6-TTS-2026-001-local-only.json"
SERVICE = ROOT / "services/speech-tts-gateway"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_owner_decision_binds_the_additive_contract_and_local_boundary():
    decision = json.loads(DECISION.read_bytes())
    assert decision["status"] == "OWNER_AUTHORIZED_LOCAL_ONLY"
    assert decision["repository_start"]["git_commit"] == (
        "bfa5db505fd54950bc39ef2c8487d59dcc19f3c7"
    )
    assert decision["contract"]["parent_sha256"] == sha(
        ROOT / "platform/contracts/speech-v1.yaml"
    ) == "e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d"
    assert decision["contract"]["tts_contract_sha256"] == sha(
        ROOT / "platform/contracts/tts-v1.yaml"
    )
    assert decision["service_boundary"]["owned_path"] == (
        "services/speech-tts-gateway"
    )
    assert decision["service_boundary"]["implementation_status_sha256"] == sha(
        ROOT / decision["service_boundary"]["implementation_status_path"]
    )
    assert decision["service_boundary"][
        "external_service_reuse_or_mutation_permitted"
    ] is False


def test_additive_status_and_architecture_now_point_to_the_owned_service():
    services = yaml.safe_load((ROOT / "platform/services.yaml").read_bytes())
    tts = services["services"]["tts-gateway"]
    assert tts["image_repo"] == "medzen-speech-tts-gateway"
    assert tts["existing_code"] is None
    # pin updated for B6v2 round 3 (Codex): secret KMS mapping (fish key
    # is CMK-encrypted — verified live) + prefix-scoped s3:ListBucket for
    # the audio cache's 404-miss semantics
    assert sha(ROOT / "platform/services.yaml") == (
        "e057e3ef99771e6dfa282af3235ca1ba6a67ea1a88a503b71224ad560b22b858"
    )
    status = yaml.safe_load((
        ROOT / "platform/service-implementation-status/v1.yaml"
    ).read_bytes())
    local = status["services"]["tts-gateway"]
    assert local["repository_code_path"] == "services/speech-tts-gateway"
    assert local["fake_fish"] == "implemented_local_no_network"
    assert local["external_gateway_reused_or_modified"] is False
    architecture = (ROOT / "ARCHITECTURE.md").read_text()
    assert "Local mocked B6.5 implementation exists" in architecture


def test_service_dependencies_are_exactly_pinned():
    requirements = [
        line.strip()
        for line in (SERVICE / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert requirements == [
        "fastapi==0.141.1",
        "PyYAML==6.0.3",
        "starlette==1.3.1",
        "uvicorn==0.52.0",
        # real Fish provider (owner order 2026-08-20): lazy-imported at the
        # client factory, pinned like everything else
        "requests==2.32.5",
        "boto3==1.40.16",
    ]
    assert all("==" in requirement for requirement in requirements)


def test_real_provider_stays_lazy_and_leak_free():
    """The mock-only era ended by owner order 2026-08-20. Surviving rules:
    boto3/requests import LAZILY (module import needs neither), the default
    mode stays text_only, 401/403 bodies are never echoed (key fragments),
    and the API key is never logged."""
    python = "\n".join(
        path.read_text()
        for path in sorted(SERVICE.rglob("*.py"))
    )
    import re
    module_level_imports = re.findall(r"^import (boto3|requests)$", python, re.M)
    assert not module_level_imports, (
        f"AWS/network SDKs must import lazily, found top-level: {module_level_imports}")
    assert "never echo body" in python or "rejected the API key" in python
    assert 'os.environ.get("MEDZEN_SPEECH_TTS_PROVIDER", "text_only")' in python


def test_b6_4_evidence_and_cloud_zero_are_preserved():
    decision = json.loads(DECISION.read_bytes())
    assert sha(
        ROOT / "platform/evidence/B6-LOCAL-ENGINEERING-2026-004-streaming.json"
    ) == "9609e16c626f2b85d4ef92229fd72165ddbfe12bde506a30b90d4ed69c363a55"
    preserved = decision["preserved_state"]
    assert preserved["b5_gate_outcome"] == "BLOCKED_UNCHANGED"
    assert preserved["deferred_language_approved_versions"] == "NULL_UNCHANGED"
    assert preserved["cpu_desired_capacity"] == 0
    assert preserved["gpu_desired_capacity"] == 0
    assert preserved["ssm_registry_parameters"] == 0
