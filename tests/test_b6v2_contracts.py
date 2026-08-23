"""B6v2 round 5 (Codex): the v2 contracts must be ONE coherent, parseable
chain. Round 4 shipped an invalid tts-v2.yaml, a stale schema hash inside
speech-v2.yaml, and a tts schema whose conditional branch still forced
real Fish responses to identify as fake_fish — none of which any test
checked. These tests make every layer self-verifying: the YAML parses,
every schema hash inside it matches the committed file, the registry
constants match the YAML bytes, and a REAL Fish-backed response fixture
validates against the published schema.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/speech-orchestrator"))

from medzen_speech_orchestrator.registry import (  # noqa: E402
    V2_DEPLOYED_CONTRACTS,
)

CONTRACT_FILES = {
    "asr": "speech-v2.yaml",
    "rag": "speech-v2.yaml",
    "llm": "llm-v2.yaml",
    "tts": "tts-v2.yaml",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_v2_contract_parses_and_pins_current_schema_bytes():
    for name in sorted(set(CONTRACT_FILES.values())):
        path = ROOT / "platform/contracts" / name
        document = yaml.safe_load(path.read_text())   # round 5: MUST parse
        assert document["version"] == "medzen.speech.v2"
        schemas = document["schemas"]
        assert schemas, name
        for relative, expected in schemas.items():
            actual = _sha(ROOT / "platform/contracts/schemas" / relative)
            assert actual == expected, (
                f"{name}: pinned hash for {relative} is stale")


def test_registry_constants_match_the_committed_contract_bytes():
    for dependency, (contract_id, pinned) in V2_DEPLOYED_CONTRACTS.items():
        path = ROOT / "platform/contracts" / CONTRACT_FILES[dependency]
        assert _sha(path) == pinned, f"{dependency}: {contract_id} pin is stale"
        assert contract_id.endswith("2026-002")


def _validator(relative: str) -> Draft202012Validator:
    schema = json.loads(
        (ROOT / "platform/contracts/schemas" / relative).read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_a_real_fish_response_satisfies_the_published_tts_contract():
    """Codex reproduced FIVE schema errors on a real Fish response —
    the conditional fish branch still required fake_fish + synthetic
    media. This fixture is exactly what the real path produces."""
    text = "Fata imiti kabiri ku munsi nyuma yo kurya."
    audio = b"REAL-FISH-MP3-BYTES"
    response = {
        "request_id": "44444444-4444-4444-8444-444444444444",
        "language": "kin",
        "text": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "synthesis_key_sha256": "ab" * 32,
        "tts_backend": "fish",
        "provider": "fish",
        "audio_url": ("https://medzen-audio-cache.s3.eu-central-1.amazonaws.com/"
                       "tts/" + "ab" * 32 + ".mp3?X-Amz-Expires=900"),
        "media_type": "audio/mpeg",
        "audio_sha256": hashlib.sha256(audio).hexdigest(),
        "model_versions": {
            "asr": "omniasr_ctc_1b:aabbccddeeff",
            "registry_snapshot": "b6v2-nonprod:" + "cd" * 32,
            "llm": "bedrock:eu.anthropic.claude-sonnet-4-5",
            "rag": "sha256:" + "ef" * 32,
            "tts": "fish:s1",
        },
        "cache_hit": False,
        "provider_attempted": True,
        "degradation_reason": None,
    }
    _validator("tts-v2/response.schema.json").validate(response)


def test_the_synthetic_stub_response_still_validates():
    """The byte-frozen v1-proof behavior remains inside the v2 envelope."""
    text = "synthetic"
    response = {
        "request_id": "44444444-4444-4444-8444-444444444444",
        "language": "en",
        "text": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "synthesis_key_sha256": "ab" * 32,
        "tts_backend": "fish",
        "provider": "fake_fish",
        "audio_url": "medzen+local://tts/" + "ab" * 32,
        "media_type": "audio/vnd.medzen.synthetic",
        "audio_sha256": hashlib.sha256(b"x").hexdigest(),
        "model_versions": {
            "asr": None,
            "registry_snapshot": "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001",
            "llm": None,
            "rag": None,
            "tts": "fake-fish-local-v1",
        },
        "cache_hit": False,
        "provider_attempted": True,
        "degradation_reason": None,
    }
    _validator("tts-v2/response.schema.json").validate(response)


def test_prohibited_identity_combinations_refuse():
    """Round 6 (Codex): provider fish + medzen+local + synthetic media
    VALIDATED — the branches constrained identity and delivery
    independently. They are now mutually exclusive."""
    import pytest
    from jsonschema import ValidationError

    text = "prohibited"
    base = {
        "request_id": "44444444-4444-4444-8444-444444444444",
        "language": "kin",
        "text": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "synthesis_key_sha256": "ab" * 32,
        "tts_backend": "fish",
        "provider": "fish",
        "audio_url": "medzen+local://tts/" + "ab" * 32,   # PROHIBITED for real fish
        "media_type": "audio/vnd.medzen.synthetic",
        "audio_sha256": hashlib.sha256(b"x").hexdigest(),
        "model_versions": {
            "asr": None,
            "registry_snapshot": "b6v2-nonprod:" + "cd" * 32,
            "llm": None,
            "rag": None,
            "tts": "fish:s1",
        },
        "cache_hit": False,
        "provider_attempted": True,
        "degradation_reason": None,
    }
    validator = _validator("tts-v2/response.schema.json")
    with pytest.raises(ValidationError):
        validator.validate(base)
    # and the inverse: the synthetic proof may never mint HTTPS delivery
    inverted = dict(base, provider="fake_fish",
                     audio_url="https://s3.example/tts/x.mp3",
                     media_type="audio/mpeg")
    with pytest.raises(ValidationError):
        validator.validate(inverted)
    # round 7 (Codex, INVALID_PROVIDER_BACKEND_COMBINATION_ACCEPTED):
    # the exact reproduced combination — provider text_only with a fish
    # backend and local synthetic audio — must refuse under the oneOf
    reproduced = dict(base, provider="text_only")
    with pytest.raises(ValidationError):
        validator.validate(reproduced)
    # fish-mode DEGRADATION (text_only fallback) stays legal
    degraded = dict(base, tts_backend="text_only", audio_url=None,
                     media_type=None, audio_sha256=None,
                     degradation_reason="FISH_TIMEOUT")
    degraded["model_versions"] = dict(base["model_versions"], tts=None)
    validator.validate(degraded)
    # and the policy text-only shape stays legal too
    policy = dict(degraded, provider="text_only",
                   degradation_reason="POLICY_TEXT_ONLY",
                   provider_attempted=False)
    validator.validate(policy)
