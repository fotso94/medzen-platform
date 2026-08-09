from __future__ import annotations

import copy
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-tts-gateway"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_tts_gateway.app import fish_breaker  # noqa: E402
from medzen_speech_tts_gateway.cache import (  # noqa: E402
    CacheRefusal,
    CachedAudio,
    ContentHashCache,
)
from medzen_speech_tts_gateway.gateway import (  # noqa: E402
    TTSGateway,
    TTSRefusal,
    content_sha256,
    synthesis_key,
)
from medzen_speech_tts_gateway.provider import FakeFishProvider  # noqa: E402
from medzen_speech_tts_gateway.shared_resilience import State  # noqa: E402


REQUEST = __import__("json").loads((
    ROOT / "platform/contracts/fixtures/tts-v1/request.json"
).read_bytes())


def service(outcomes=None):
    provider = FakeFishProvider(outcomes)
    gateway = TTSGateway(provider=provider, breaker=fish_breaker())
    return gateway, provider


def test_text_only_is_a_success_and_preserves_every_input_version():
    gateway = TTSGateway(provider=None, breaker=None)
    response = gateway.synthesize(REQUEST)
    assert response["text"] == REQUEST["text"]
    assert response["tts_backend"] == "text_only"
    assert response["audio_url"] is None
    assert response["model_versions"] == REQUEST["model_versions"]
    assert response["degradation_reason"] == "POLICY_TEXT_ONLY"


def test_fish_success_is_content_addressed_and_sets_only_the_tts_version():
    gateway, provider = service()
    response = gateway.synthesize(REQUEST)
    assert response["text"] == REQUEST["text"]
    assert response["tts_backend"] == "fish"
    assert response["audio_url"].endswith(response["synthesis_key_sha256"])
    assert response["content_sha256"] == content_sha256(REQUEST["text"])
    assert response["audio_sha256"] == "3edf395920e394145f002ead33bef9d3ef41ac6f744456db292f08375a8b397f"
    assert response["model_versions"] == {
        **REQUEST["model_versions"], "tts": "fake-fish-local-v1"
    }
    assert provider.calls[0][1] == 2000


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("timeout", "FISH_TIMEOUT"),
        ("unavailable", "FISH_UNAVAILABLE"),
        ("malformed", "FISH_INVALID_RESPONSE"),
    ],
)
def test_fish_failure_is_a_text_preserving_success(outcome, reason):
    gateway, provider = service([outcome])
    response = gateway.synthesize(REQUEST)
    assert len(provider.calls) == 1
    assert response["text"] == REQUEST["text"]
    assert response["tts_backend"] == "text_only"
    assert response["audio_url"] is None
    assert response["model_versions"] == REQUEST["model_versions"]
    assert response["degradation_reason"] == reason


def test_three_timeouts_open_breaker_and_fourth_request_skips_provider():
    gateway, provider = service(["timeout"] * 4)
    for _ in range(3):
        assert gateway.synthesize(REQUEST)["degradation_reason"] == "FISH_TIMEOUT"
    assert gateway.breaker.state is State.OPEN
    fourth = gateway.synthesize(REQUEST)
    assert fourth["degradation_reason"] == "FISH_CIRCUIT_OPEN"
    assert fourth["provider_attempted"] is False
    assert len(provider.calls) == 3


def test_request_identity_is_excluded_from_the_cache_key_and_provider_runs_once():
    gateway, provider = service()
    first = gateway.synthesize(REQUEST)
    repeated = copy.deepcopy(REQUEST)
    repeated["request_id"] = "88888888-8888-4888-8888-888888888888"
    second = gateway.synthesize(repeated)
    assert second["request_id"] == repeated["request_id"]
    assert second["synthesis_key_sha256"] == first["synthesis_key_sha256"]
    assert second["audio_url"] == first["audio_url"]
    assert second["cache_hit"] is True
    assert second["provider_attempted"] is False
    assert len(provider.calls) == 1


def test_concurrent_duplicate_requests_have_one_provider_call():
    gateway, provider = service()
    barrier = threading.Barrier(4)

    def run(index):
        value = copy.deepcopy(REQUEST)
        value["request_id"] = f"99999999-9999-4999-8999-{index:012d}"
        barrier.wait()
        return gateway.synthesize(value)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(run, range(4)))
    assert len(provider.calls) == 1
    assert len({response["synthesis_key_sha256"] for response in responses}) == 1
    assert sorted(response["cache_hit"] for response in responses) == [
        False, True, True, True
    ]


def test_cached_audio_survives_an_open_provider_breaker():
    gateway, provider = service()
    successful = gateway.synthesize(REQUEST)
    for _ in range(gateway.breaker.failure_threshold):
        gateway.breaker.record_failure()
    assert gateway.breaker.state is State.OPEN
    cached = gateway.synthesize(REQUEST)
    assert cached["tts_backend"] == "fish"
    assert cached["audio_url"] == successful["audio_url"]
    assert cached["cache_hit"] is True
    assert len(provider.calls) == 1


def test_content_change_or_language_change_produces_a_different_key():
    gateway, provider = service()
    first = gateway.synthesize(REQUEST)
    changed = copy.deepcopy(REQUEST)
    changed["text"] += " Again."
    changed["request_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    second = gateway.synthesize(changed)
    changed_language = copy.deepcopy(REQUEST)
    changed_language["language"] = "lingala"
    changed_language["request_id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    third = gateway.synthesize(changed_language)
    assert len({
        first["synthesis_key_sha256"],
        second["synthesis_key_sha256"],
        third["synthesis_key_sha256"],
    }) == 3
    assert len(provider.calls) == 3


def test_canonical_synthesis_key_is_stable_and_binds_voice_configuration():
    key = synthesis_key(
        text_sha256="a" * 64,
        language="english",
        provider="fake_fish",
        voice_id="voice-a",
        model_version="v1",
        media_type="audio/test",
    )
    assert key == synthesis_key(
        text_sha256="a" * 64,
        language="english",
        provider="fake_fish",
        voice_id="voice-a",
        model_version="v1",
        media_type="audio/test",
    )
    assert key != synthesis_key(
        text_sha256="a" * 64,
        language="english",
        provider="fake_fish",
        voice_id="voice-b",
        model_version="v1",
        media_type="audio/test",
    )


def test_content_hash_uses_exact_utf8_without_silent_unicode_normalization():
    composed = "Caf\u00e9"
    decomposed = "Cafe\u0301"
    assert composed != decomposed
    assert content_sha256(composed) != content_sha256(decomposed)


@pytest.mark.parametrize(
    "entry",
    [
        CachedAudio("wrong-key", b"audio", "0" * 64, "audio/test", "v1"),
        CachedAudio("expected", b"", "0" * 64, "audio/test", "v1"),
        CachedAudio("expected", b"audio", "0" * 64, "audio/test", "v1"),
    ],
)
def test_cache_refuses_wrong_key_or_empty_audio_without_mutation(entry):
    cache = ContentHashCache()
    with pytest.raises(CacheRefusal):
        cache.get_or_create("expected", lambda: entry)
    assert len(cache) == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.update({"request_id": "not-a-uuid"}),
        lambda value: value.update({"language": "English"}),
        lambda value: value.update({"text": ""}),
        lambda value: value["model_versions"].update({"tts": "already-set"}),
    ],
)
def test_invalid_requests_fail_before_provider_or_cache(mutation):
    gateway, provider = service()
    invalid = copy.deepcopy(REQUEST)
    mutation(invalid)
    with pytest.raises(TTSRefusal) as caught:
        gateway.synthesize(invalid)
    assert caught.value.code == "INVALID_REQUEST"
    assert provider.calls == []
    assert len(gateway.cache) == 0
