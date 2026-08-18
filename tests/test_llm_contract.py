from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/llm-gateway"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_llm_gateway.policy import PolicyRefusal, PolicyStore  # noqa: E402


CONTRACT = ROOT / "platform/contracts/llm-v1.yaml"
SCHEMAS = ROOT / "platform/contracts/schemas/llm-v1"
FIXTURES = ROOT / "platform/contracts/fixtures/llm-v1"
SPEECH_CONTRACT = ROOT / "platform/contracts/speech-v1.yaml"


def load_json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def validate(schema: str, fixture: str) -> None:
    Draft202012Validator(
        load_json(SCHEMAS / schema), format_checker=FormatChecker()
    ).validate(load_json(FIXTURES / fixture))


def test_llm_contract_is_an_additive_local_extension_of_adopted_speech_v1():
    contract = yaml.safe_load(CONTRACT.read_bytes())
    assert contract["status"] == "b6_2_local_mock_contract"
    assert contract["parent_contract"]["sha256"] == hashlib.sha256(
        SPEECH_CONTRACT.read_bytes()
    ).hexdigest()
    assert contract["scope"]["mode"] == "local_mock_only"
    assert contract["scope"]["clinical_content"] == "prohibited"


def test_llm_golden_request_and_response_validate():
    validate("request.schema.json", "request.json")
    validate("response.schema.json", "response.json")


def test_real_bedrock_boundary_requires_a_capped_versioned_packet():
    scope = yaml.safe_load(CONTRACT.read_bytes())["scope"]
    assert scope["real_bedrock_invocation"] == (
        "requires_separate_versioned_aws_packet"
    )
    assert set(scope["real_bedrock_packet_limits_required"]) == {
        "maximum_requests",
        "maximum_input_tokens",
        "maximum_output_tokens",
        "maximum_cost_usd",
    }


def test_every_generated_language_resolves_exactly_one_policy():
    store = PolicyStore(
        ROOT / "registry/languages", ROOT / "registry/llm-policies/v1.yaml"
    )
    loaded = store.validate_all()
    # 17 original scope languages + kinyarwanda/pulaar/yemba, declared by
    # the B4-B5-SCOPE-2026-004 campaign activation
    assert len(loaded) == 20
    assert set(loaded) == set(store.aliases())
    for alias, policy in loaded.items():
        assert policy.language == alias
        assert policy.policy_id == f"{alias}-medzen-v1"
        assert policy.citations_required is True
        assert policy.timeout_ms == 30000


def test_missing_or_cross_language_policy_reference_fails_closed(tmp_path: Path):
    registry = tmp_path / "languages"
    shutil.copytree(ROOT / "registry/languages", registry)
    source = tmp_path / "policies.yaml"
    shutil.copy2(ROOT / "registry/llm-policies/v1.yaml", source)
    lingala = registry / "lingala.yaml"
    text = lingala.read_text().replace(
        "policy: lingala-medzen-v1", "policy: english-medzen-v1"
    )
    lingala.write_text(text)
    store = PolicyStore(registry, source)
    with pytest.raises(PolicyRefusal, match="another language"):
        store.load("lingala")


def test_local_gateway_has_no_aws_sdk_or_real_provider_adapter():
    requirements = {
        line.split("==", 1)[0].strip().casefold()
        for line in (SERVICE_ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    sources = "\n".join(
        path.read_text().casefold()
        for path in (SERVICE_ROOT / "medzen_llm_gateway").glob("*.py")
    )
    assert "boto3" not in requirements
    assert "botocore" not in requirements
    assert "import boto3" not in sources
    assert "import botocore" not in sources
    assert "bedrock-runtime" not in sources
