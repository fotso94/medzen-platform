from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-tts-gateway"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_tts_gateway.app import fish_breaker  # noqa: E402
from medzen_speech_tts_gateway.gateway import TTSGateway  # noqa: E402
from medzen_speech_tts_gateway.provider import FakeFishProvider  # noqa: E402


CONTRACT = ROOT / "platform/contracts/tts-v1.yaml"
SCHEMAS = ROOT / "platform/contracts/schemas/tts-v1"
FIXTURES = ROOT / "platform/contracts/fixtures/tts-v1"
REQUEST = json.loads((FIXTURES / "request.json").read_bytes())


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_bytes())


def validate(schema: str, value: dict) -> None:
    Draft202012Validator(
        json.loads((SCHEMAS / schema).read_bytes()),
        format_checker=FormatChecker(),
    ).validate(value)


def test_tts_contract_is_additive_and_parent_contract_is_unchanged():
    contract = yaml.safe_load(CONTRACT.read_bytes())
    parent = ROOT / "platform/contracts/speech-v1.yaml"
    assert contract["parent_contract"]["sha256"] == hashlib.sha256(
        parent.read_bytes()
    ).hexdigest() == "e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d"
    assert contract["scope"]["service"] == "medzen-speech-tts-gateway"
    assert contract["scope"]["default_backend"] == "text_only"
    assert contract["scope"]["real_fish_access"] == "prohibited"


def test_request_and_all_success_or_degraded_fixtures_are_schema_valid():
    validate("request.schema.json", REQUEST)
    for name in (
        "text-only-response.json",
        "fish-response.json",
        "fish-timeout-response.json",
    ):
        validate("response.schema.json", load(name))


def test_text_only_and_fake_fish_outputs_match_the_golden_fixtures():
    text_only = TTSGateway(provider=None, breaker=None)
    assert text_only.synthesize(REQUEST) == load("text-only-response.json")
    fish = TTSGateway(provider=FakeFishProvider(), breaker=fish_breaker())
    assert fish.synthesize(REQUEST) == load("fish-response.json")


def test_fake_fish_timeout_matches_the_text_preserving_golden_fixture():
    gateway = TTSGateway(
        provider=FakeFishProvider(["timeout"]), breaker=fish_breaker()
    )
    assert gateway.synthesize(REQUEST) == load("fish-timeout-response.json")
