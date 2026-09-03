from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/llm-gateway"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_llm_gateway.app import create_app  # noqa: E402
from medzen_llm_gateway.gateway import GatewayRefusal, LLMGateway  # noqa: E402
from medzen_llm_gateway.policy import PolicyStore  # noqa: E402
from medzen_llm_gateway.provider import FakeBedrockProvider  # noqa: E402
from medzen_llm_gateway.shared_resilience import CircuitBreaker, State  # noqa: E402


REQUEST = json.loads((
    ROOT / "platform/contracts/fixtures/llm-v2/request.json"
).read_bytes())
RESPONSE_SCHEMA = json.loads((
    ROOT / "platform/contracts/schemas/llm-v2/response.schema.json"
).read_bytes())


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


def gateway(outcomes=None, *, clock=None, failure_threshold=5,
            timeout_threshold=3, open_duration_s=20):
    policies = PolicyStore(
        ROOT / "registry/languages", ROOT / "registry/llm-policies/v1.yaml"
    )
    provider = FakeBedrockProvider(outcomes)
    breaker = CircuitBreaker(
        name="bedrock",
        failure_threshold=failure_threshold,
        timeout_threshold=timeout_threshold,
        open_duration_s=open_duration_s,
        _clock=clock or __import__("time").monotonic,
    )
    return LLMGateway(policies, provider, breaker), provider


def test_fake_provider_success_binds_policy_rag_and_all_model_versions():
    service, provider = gateway()
    response = service.complete(REQUEST)
    Draft202012Validator(
        RESPONSE_SCHEMA, format_checker=FormatChecker()
    ).validate(response)
    assert response["policy"]["id"] == "english-medzen-v1"
    assert response["reply"]["citations"] == REQUEST["rag"]["citations"]
    assert response["model_versions"]["rag"] == REQUEST["model_versions"]["rag"]
    assert response["model_versions"]["llm"] == "fake-bedrock-local-v1"
    assert provider.calls[0][1] == 30000
    assert provider.calls[0][0].maximum_output_tokens == 1500


def test_requested_language_policy_controls_provider_response_language():
    service, provider = gateway()
    request = json.loads(json.dumps(REQUEST))
    request["language"] = "lingala"
    response = service.complete(request)
    assert response["policy"]["id"] == "lingala-medzen-v1"
    assert response["reply"]["text"].startswith("[Lingala synthetic]")
    assert provider.calls[0][0].response_language == "Lingala"


def test_missing_citations_refuses_before_provider_call():
    service, provider = gateway()
    request = json.loads(json.dumps(REQUEST))
    request["rag"]["citations"] = []
    with pytest.raises(GatewayRefusal) as caught:
        service.complete(request)
    assert caught.value.code == "CITATIONS_REQUIRED"
    assert provider.calls == []


def test_rag_snapshot_and_model_version_must_match():
    service, provider = gateway()
    request = json.loads(json.dumps(REQUEST))
    request["model_versions"]["rag"] = "sha256:" + "0" * 64
    with pytest.raises(GatewayRefusal) as caught:
        service.complete(request)
    assert caught.value.code == "INVALID_REQUEST"
    assert provider.calls == []


def test_provider_cannot_invent_or_modify_a_citation():
    service, provider = gateway(["tampered_citation"])
    with pytest.raises(GatewayRefusal) as caught:
        service.complete(REQUEST)
    assert caught.value.code == "CITATION_BINDING_INVALID"
    assert len(provider.calls) == 1


def test_three_timeouts_open_breaker_and_fourth_call_is_short_circuited():
    service, provider = gateway(["timeout", "timeout", "timeout", "success"])
    for _ in range(3):
        with pytest.raises(GatewayRefusal) as caught:
            service.complete(REQUEST)
        assert caught.value.code == "PROVIDER_TIMEOUT"
    assert service.breaker.state is State.OPEN
    with pytest.raises(GatewayRefusal) as caught:
        service.complete(REQUEST)
    assert caught.value.code == "PROVIDER_CIRCUIT_OPEN"
    assert len(provider.calls) == 3


def test_half_open_probe_recovers_after_bounded_timeout_window():
    clock = Clock()
    service, provider = gateway(
        ["timeout", "timeout", "timeout", "success"], clock=clock
    )
    for _ in range(3):
        with pytest.raises(GatewayRefusal):
            service.complete(REQUEST)
    clock.advance(20)
    assert service.breaker.state is State.HALF_OPEN
    response = service.complete(REQUEST)
    assert response["provider"] == "fake_bedrock"
    assert service.breaker.state is State.CLOSED
    assert len(provider.calls) == 4


def test_provider_failure_has_no_invented_fallback():
    service, _ = gateway(["unavailable"])
    with pytest.raises(GatewayRefusal) as caught:
        service.complete(REQUEST)
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True


def test_http_boundary_and_open_breaker_readiness():
    service, _ = gateway(["timeout", "timeout", "timeout"])
    with TestClient(create_app(service)) as client:
        assert client.get("/readyz").status_code == 200
        for _ in range(3):
            response = client.post("/internal/v1/responses", json=REQUEST)
            assert response.status_code == 504
        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["breaker_state"] == "open"
        blocked = client.post("/internal/v1/responses", json=REQUEST)
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == "PROVIDER_CIRCUIT_OPEN"


def test_http_success_matches_schema_and_logs_no_content(caplog):
    transcript = REQUEST["transcript"]["verbatim"]
    excerpt = REQUEST["rag"]["citations"][0]["excerpt"]
    service, _ = gateway()
    caplog.set_level(logging.INFO, logger="medzen.llm")
    with TestClient(create_app(service)) as client:
        response = client.post("/internal/v1/responses", json=REQUEST)
    assert response.status_code == 200
    Draft202012Validator(
        RESPONSE_SCHEMA, format_checker=FormatChecker()
    ).validate(response.json())
    assert transcript not in caplog.text
    assert excerpt not in caplog.text
    assert response.json()["reply"]["text"] not in caplog.text


def test_payload_limit_and_unknown_fields_fail_closed():
    service, provider = gateway()
    with TestClient(create_app(service, max_body_bytes=128)) as client:
        oversized = client.post(
            "/internal/v1/responses",
            content=json.dumps(REQUEST),
            headers={"content-type": "application/json"},
        )
        assert oversized.status_code == 413
    with TestClient(create_app(service)) as client:
        unknown = json.loads(json.dumps(REQUEST))
        unknown["unexpected"] = True
        invalid = client.post("/internal/v1/responses", json=unknown)
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    assert provider.calls == []


def test_real_provider_mode_refuses_at_startup(monkeypatch):
    monkeypatch.setenv("MEDZEN_LLM_PROVIDER", "bedrock")
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["language_policies_loaded"] is False
        response = client.post("/internal/v1/responses", json=REQUEST)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"


def test_history_is_optional_bounded_and_alternating():
    """Phase 2: an optional, strictly-shaped history field is accepted;
    anything malformed refuses; absence still behaves exactly as before."""
    service, provider = gateway()
    service.complete(REQUEST)
    assert provider.calls[-1][0].history == ()
    good = dict(REQUEST, history=[{"role": "user", "text": "hi"},
                                  {"role": "assistant", "text": "hello"}])
    service.complete(good)
    assert provider.calls[-1][0].history == (("user", "hi"), ("assistant", "hello"))
    for bad in (
        [{"role": "assistant", "text": "x"}],                                   # must start with user
        [{"role": "user", "text": "x"}],                                        # must end with assistant
        [{"role": "user", "text": ""}, {"role": "assistant", "text": "y"}],     # empty text
        [{"role": "user", "text": "a", "extra": 1}, {"role": "assistant", "text": "b"}],
        [{"role": "user", "text": "a" * 1001}, {"role": "assistant", "text": "b"}],
        "not-a-list",
    ):
        with pytest.raises(GatewayRefusal):
            service.complete(dict(REQUEST, history=bad))


def test_complete_stream_narrates_deltas_and_returns_the_identical_response():
    """Phase 3b: streaming narrates the reply text in deltas and returns the
    exact response the buffered call returns (same checks, same binding)."""
    service, _ = gateway()
    buffered = service.complete(REQUEST)
    deltas = []
    streamed = service.complete_stream(REQUEST, deltas.append)
    assert "".join(deltas) == streamed["reply"]["text"] == buffered["reply"]["text"]
    for key in ("request_id", "language", "reply", "policy", "model_versions"):
        assert streamed[key] == buffered[key]


def test_reply_text_extractor_decodes_incrementally_with_escapes():
    from medzen_llm_gateway.provider import _ReplyTextExtractor
    x = _ReplyTextExtractor()
    out = ""
    for chunk in ['{"te', 'xt": "Bonj', 'our \\"ami\\"', ', \\u00e9t', 'oile", "cited_document_ids": []}']:
        out += x.feed(chunk)
    assert out == 'Bonjour "ami", \u00e9toile'
    assert x.finished


# ---------------------------------------------------------------------------
# Codex review 2026-09-03: prose replies, empty citation sets, worker thread
# ---------------------------------------------------------------------------
from medzen_llm_gateway import gateway as gateway_module  # noqa: E402
from medzen_llm_gateway.provider import BedrockProvider, ProviderRequest  # noqa: E402


class _ScriptedConverse:
    """A bedrock-runtime stand-in: returns the scripted replies in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        return {"output": {"message": {"content": [{"text": text}]}},
                "stopReason": "end_turn"}


def _provider_request(with_citation=True):
    citations = ()
    if with_citation:
        citations = ({"rank": 1, "document_id": "doc-1", "title": "Booking",
                      "source_uri": "medzen://corpus/product/en/booking--s01",
                      "section": "How do I book?", "content_sha256": "a" * 64,
                      "excerpt": "Open the app", "grounding_text": "Open the app and book.",
                      "score": 0.9},)
    return ProviderRequest(
        language="en", response_language="English", policy_id="en-v1",
        normalized_transcript="how do i book", citations=citations,
        citation_binding_sha256="b" * 64, maximum_output_tokens=600)


def test_prose_reply_is_retried_once_and_the_json_retry_is_used():
    client = _ScriptedConverse(["Just open the app and book.",
                                '{"text": "Open the app and book.", "cited_document_ids": ["doc-1"]}'])
    result = BedrockProvider("model-x", "eu-central-1", client=client).invoke(
        _provider_request(), timeout_ms=5000)
    assert result.text == "Open the app and book." and result.cited_document_ids == ("doc-1",)
    assert len(client.calls) == 2
    assert "not a JSON object" in client.calls[1]["system"][0]["text"]


def test_persistent_prose_becomes_an_ungrounded_answer_never_invented_json():
    client = _ScriptedConverse(["Muraho! Fungura porogaramu.", "Fungura porogaramu ya MedZen."])
    result = BedrockProvider("model-x", "eu-central-1", client=client).invoke(
        _provider_request(), timeout_ms=5000)
    assert result.text == "Fungura porogaramu ya MedZen." and result.cited_document_ids == ()
    assert len(client.calls) == 2
    # a well-formed first reply never triggers the retry
    client = _ScriptedConverse(['{"text": "ok", "cited_document_ids": []}'])
    BedrockProvider("model-x", "eu-central-1", client=client).invoke(
        _provider_request(with_citation=False), timeout_ms=5000)
    assert len(client.calls) == 1


def test_empty_citation_set_is_ungrounded_under_the_dev_flag_and_refused_without(monkeypatch):
    monkeypatch.setattr(gateway_module, "ALLOW_UNGROUNDED", False)
    service, _ = gateway(["cites_nothing"])
    with pytest.raises(GatewayRefusal) as caught:
        service.complete(REQUEST)
    assert caught.value.code == "CITATION_BINDING_INVALID"
    monkeypatch.setattr(gateway_module, "ALLOW_UNGROUNDED", True)
    service, _ = gateway(["cites_nothing"])
    response = service.complete(REQUEST)
    assert response["reply"]["citations"] == []
    assert service.breaker.state is State.CLOSED


def test_buffered_route_runs_the_provider_off_the_event_loop():
    import threading
    seen = {}

    class ThreadRecordingGateway:
        def complete(self, value):
            seen["thread"] = threading.current_thread().name
            return {"ok": True}

    with TestClient(create_app(ThreadRecordingGateway())) as client:
        response = client.post("/internal/v1/responses", json=REQUEST)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert seen["thread"] != "MainThread"      # asyncio.to_thread worker
