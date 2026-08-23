"""B6v2 round 4 (Codex): the HTTP-LEVEL composed chain.

The round-3 composed test connected service CLASSES and missed every
finding that lives in the layers it skipped: FastAPI request/header
handling, build_deployed_orchestrator, SSM, contract-version negotiation
and per-hop response contracts. This test runs the REAL FastAPI apps of
all four dependencies via their TestClients, boots the orchestrator
through build_deployed_orchestrator (deployed_http_ssm) against an
SSM-shaped v2 snapshot, and bridges the reviewed cluster endpoints to
those apps — real multipart, real headers, real JSON contracts end to
end. Only the pieces that REQUIRE external hardware or networks are
stubbed at their outermost edge: the GPU pipeline inside the ASR app,
Bedrock's converse(), Fish's HTTP session, and S3's API.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
for service in ("speech-orchestrator", "rag-index", "llm-gateway",
                "speech-tts-gateway", "asr-runtime", "model-loader"):
    sys.path.insert(0, str(ROOT / f"services/{service}"))

from test_b6v2_composed_chain import (  # noqa: E402
    FakeS3,
    StubBedrockClient,
    StubFishSession,
)
from medzen_speech_orchestrator.registry import (  # noqa: E402
    DEPLOYED_ENDPOINTS,
    V2_CLASSIFICATION,
    V2_CONTRACT_VERSION,
    V2_DEPLOYED_CONTRACTS,
    canonical_json,
)
from medzen_speech_orchestrator.remote_dependencies import (  # noqa: E402
    ClusterHTTPTransport,
    RemoteDependencyRefusal,
)

KEY = "medzen-b6-synthetic-client-key"
REQUEST_ID = "33333333-3333-4333-8333-333333333333"
ASR_TREE_SHA = "ab" * 32
ASR_VERSION = f"omniasr_ctc_1b:{ASR_TREE_SHA[:12]}"
LLM_MODEL_ID = "eu.anthropic.claude-sonnet-4-5"
QUERY = "When does the fictional training desk open?"
RAG_FIXTURE = ROOT / "platform/testdata/rag-index"
AUDIO = (ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav").read_bytes()


def _rag_snapshot_sha256() -> str:
    alias = json.loads((RAG_FIXTURE / "aliases/current.json").read_bytes())
    return alias["manifest_sha256"]


def _v2_snapshot() -> tuple[str, dict]:
    route = {
        "schema_version": 2,
        "classification": V2_CLASSIFICATION,
        "contract_version": V2_CONTRACT_VERSION,
        "language": {"alias": "english", "response_code": "en",
                      "accepted_codes": ["en"]},
        "asr": {"backend": "http_cluster_v1", "model_version": ASR_VERSION,
                 "artifact_tree_sha256": ASR_TREE_SHA,
                 "reported_registry_snapshot": "omniasr-nonprod:" + "cd" * 32},
        "rag": {"alias": "current", "snapshot_sha256": _rag_snapshot_sha256(),
                 "query_language": "en"},
        "llm": {"model_version": f"bedrock:{LLM_MODEL_ID}",
                 "policy_id": "english-medzen-v1"},
        "tts": {"backend": "http_fish_v2", "model_version": "fish:s1"},
        "dependencies": {
            name: {"endpoint": DEPLOYED_ENDPOINTS[name],
                    "contract_id": V2_DEPLOYED_CONTRACTS[name][0],
                    "contract_sha256": V2_DEPLOYED_CONTRACTS[name][1]}
            for name in DEPLOYED_ENDPOINTS
        },
    }
    index = {"schema_version": 1, "default_language": "english",
             "languages": [{"alias": "english", "codes": ["en"],
                             "route_parameter": "routes/english"}]}
    material = {"schema_version": 1, "classification": V2_CLASSIFICATION,
                "index": index, "routes": {"english": route}}
    snapshot = hashlib.sha256(canonical_json(material)).hexdigest()
    root = f"/medzen/registry/nonprod/b6v2/{snapshot}"
    values = {"index": index, "routes/english": route}
    manifest = {
        "schema_version": 1,
        "classification": V2_CLASSIFICATION,
        "snapshot_sha256": snapshot,
        "snapshot_material_sha256": snapshot,
        "parameter_value_sha256": {
            rel: hashlib.sha256(canonical_json(obj)).hexdigest()
            for rel, obj in values.items()
        },
    }
    parameters = {
        f"{root}/_manifest": {
            "Name": f"{root}/_manifest", "Type": "SecureString",
            "Value": canonical_json(manifest).decode(), "Version": 1},
    }
    for rel, obj in values.items():
        parameters[f"{root}/{rel}"] = {
            "Name": f"{root}/{rel}", "Type": "SecureString",
            "Value": canonical_json(obj).decode(), "Version": 1}
    return root, parameters


class FakeSSM:
    def __init__(self, parameters: dict):
        self.parameters = parameters

    def get_parameter(self, **kwargs):
        return {"Parameter": self.parameters[kwargs["Name"]]}

    def get_parameters_by_path(self, **kwargs):
        prefix = kwargs["Path"].rstrip("/") + "/"
        return {"Parameters": [self.parameters[name]
                                for name in sorted(self.parameters)
                                if name.startswith(prefix)]}


class FakeSecrets:
    def get_secret_value(self, **kwargs):
        assert kwargs == {"SecretId": "medzen/client-api-keys"}
        return {"SecretString": json.dumps({
            "schema_version": 1,
            "classification": "B6_6_SYNTHETIC_INTEGRATION_ONLY",
            "clients": [{
                "client_id": "b6-synthetic-backend",
                "key_sha256": hashlib.sha256(KEY.encode()).hexdigest(),
                "enabled": True,
            }],
        })}


class StubOmniBackend:
    """Hosted INSIDE the real ASR FastAPI app — only the GPU pipeline is
    stubbed; the HTTP layer, payload shape and identity reporting are the
    service's own code."""

    ready = True
    classification = V2_CLASSIFICATION
    production_approved = False
    # round 7: the FULL tree digest rides the payload for exact compare
    artifact_tree_sha256 = ASR_TREE_SHA
    model_versions = {
        "asr": ASR_VERSION,
        "registry_snapshot": "omniasr-nonprod:" + "cd" * 32,
        "llm": None,
        "rag": None,
        "tts": None,
    }

    def transcribe(self, audio_path, language_hint):
        from medzen_asr_runtime.backend import Transcript
        # round 5 (Codex): the round-4 stub ignored the language map and
        # hid the reproduced `'en' is not served` refusal — the hint now
        # goes through the REAL pre-inference resolution over the REAL
        # canonical marker table before anything transcribes
        from medzen_asr_runtime.omniasr_backend import resolve_omni_language
        from medzen_model_loader.languages_v2 import marker_language_ids
        assert resolve_omni_language(
            marker_language_ids(), language_hint) == "eng_Latn"
        return Transcript(
            language=language_hint or "en",
            language_probability=1.0,
            verbatim=QUERY,
            normalized=QUERY,
            normalization_version="b6v2-unicode-nfc-whitespace-v1",
            duration_seconds=1.0,
        )


class BridgeTransport:
    """Same boundary rules as ClusterHTTPTransport (_target is reused
    verbatim), but delivery goes to the real in-process FastAPI apps."""

    def __init__(self, *, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds
        self.cancelled: list[str] = []
        self.clients: dict[str, TestClient] = {}

    def post(self, *, endpoint, request_id, body, content_type, headers=None):
        host, _, path = ClusterHTTPTransport._target(endpoint)
        client = self.clients[host.split(".")[0]]
        response = client.post(path, content=body, headers={
            "Content-Type": content_type,
            "X-Request-ID": request_id,
            **(headers or {}),
        })
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        if response.status_code != 200 or media_type != "application/json":
            raise RemoteDependencyRefusal(
                "dependency returned a non-success contract response")
        return response.json()

    def post_json(self, *, endpoint, request_id, value):
        return self.post(endpoint=endpoint, request_id=request_id,
                          body=canonical_json(value),
                          content_type="application/json")

    def cancel(self, request_id: str):
        self.cancelled.append(request_id)


VOICES_INLINE = json.dumps({
    "english": {
        "reference_id": "da02ddd729004bb98133102da10c36ba",
        "model": "s1",
        "label": "http-chain test voice",
        "approved": True,
        "consent_evidence": "owner order 2026-08-20 (chat, verbatim)",
    },
})


@pytest.fixture()
def http_chain(monkeypatch):
    import medzen_speech_orchestrator.app as orchestrator_app
    import medzen_speech_tts_gateway.voices as voices

    monkeypatch.setenv("MEDZEN_TTS_VOICES_INLINE", VOICES_INLINE)
    monkeypatch.setattr(voices, "_cache", {})

    # --- the four REAL dependency apps -------------------------------
    from medzen_asr_runtime.app import create_app as create_asr_app
    from medzen_rag_index.app import create_app as create_rag_app
    from medzen_rag_index.index import IndexRepository
    from medzen_llm_gateway.app import create_app as create_llm_app
    from medzen_llm_gateway.gateway import LLMGateway
    from medzen_llm_gateway.policy import PolicyStore
    from medzen_llm_gateway.provider import BedrockProvider
    from medzen_llm_gateway.shared_resilience import (
        CircuitBreaker as LLMBreaker,
        load_config as llm_config,
    )
    from medzen_speech_tts_gateway.app import (
        create_app as create_tts_app,
        fish_breaker,
    )
    from medzen_speech_tts_gateway.gateway import TTSGateway
    from medzen_speech_tts_gateway.provider import RealFishProvider
    from medzen_speech_tts_gateway.s3_cache import S3AudioCache
    from medzen_speech_tts_gateway.voices import enforce_model, select_voice

    bedrock = StubBedrockClient(
        cited_ids=["synthetic-hours"],
        text="The fictional training desk opens Monday at 09:00.")
    fish = StubFishSession()
    s3 = FakeS3()

    config = llm_config()
    llm_breaker = LLMBreaker(
        name="bedrock",
        failure_threshold=config["circuit_breakers"]["per_provider"]["bedrock"]["failure_threshold"],
        timeout_threshold=config["circuit_breakers"]["defaults"]["timeout_threshold"],
        window_s=config["circuit_breakers"]["defaults"]["window_s"],
        open_duration_s=config["circuit_breakers"]["per_provider"]["bedrock"]["open_duration_s"],
        half_open_max_calls=config["circuit_breakers"]["defaults"]["half_open_max_calls"],
    )
    llm_gateway = LLMGateway(
        PolicyStore(ROOT / "registry/languages",
                    ROOT / "registry/llm-policies/v1.yaml"),
        BedrockProvider(model_id=LLM_MODEL_ID, region="eu-central-1",
                        client=bedrock),
        llm_breaker,
    )

    def governed(language):
        voice = select_voice(language)
        return voice.reference_id, enforce_model(voice, None)

    tts_gateway = TTSGateway(
        provider=RealFishProvider(api_key="http-chain-test-key", session=fish),
        breaker=fish_breaker(),
        voice_resolver=governed,
        cache=S3AudioCache(bucket="composed-cache",
                            kms_key_arn="arn:aws:kms:eu-central-1:0:key/t",
                            client=s3),
    )

    bridge = BridgeTransport()
    with TestClient(create_asr_app(StubOmniBackend())) as asr_client, \
            TestClient(create_rag_app(
                IndexRepository(RAG_FIXTURE, "current"))) as rag_client, \
            TestClient(create_llm_app(llm_gateway)) as llm_client, \
            TestClient(create_tts_app(tts_gateway)) as tts_client:
        bridge.clients = {"asr-runtime": asr_client, "rag-index": rag_client,
                           "llm-gateway": llm_client, "tts-gateway": tts_client}

        # --- the orchestrator, through build_deployed_orchestrator ---
        root, parameters = _v2_snapshot()
        monkeypatch.setenv("MEDZEN_ORCHESTRATOR_MODE", "deployed_http_ssm")
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        monkeypatch.setenv("MEDZEN_REGISTRY_ROOT", root)
        monkeypatch.setenv("MEDZEN_CLIENT_KEYS_SECRET_ID", "medzen/client-api-keys")
        monkeypatch.setattr(
            boto3, "client",
            lambda service, **kwargs: (FakeSSM(parameters)
                                        if service == "ssm" else FakeSecrets()))
        monkeypatch.setattr(orchestrator_app, "ClusterHTTPTransport", BridgeTransport)
        with TestClient(orchestrator_app.create_app()) as orchestrator_client:
            # rebind the bridge the app constructed to our clients
            transport = orchestrator_client.app.state.orchestrator.asr.transport
            transport.clients = bridge.clients
            yield orchestrator_client, bedrock, fish, s3


def _speech_request(client, *, contract_version, response_audio="true"):
    return client.post(
        "/v1/conversations/speech",
        headers={"Authorization": f"Bearer {KEY}",
                  "X-MedZen-Contract-Version": contract_version},
        data={"request_id": REQUEST_ID, "language_hint": "en",
               "response_audio": response_audio},
        files={"audio": ("synthetic.wav", AUDIO, "audio/wav")},
    )


def test_v2_deployment_negotiates_v2_and_refuses_v1(http_chain):
    client, bedrock, fish, s3 = http_chain
    stale = _speech_request(client, contract_version="medzen.speech.v1")
    assert stale.status_code == 426
    assert "medzen.speech.v2" in stale.json()["error"]["message"]


def test_http_chain_end_to_end_over_real_apps(http_chain):
    client, bedrock, fish, s3 = http_chain
    response = _speech_request(client, contract_version=V2_CONTRACT_VERSION)
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(bedrock.calls) == 1
    assert len(fish.calls) == 1
    assert len(s3.store) == 1

    rag_sha = _rag_snapshot_sha256()
    root_sha = client.app.state.orchestrator.router.snapshot_sha256
    assert body["model_versions"] == {
        "asr": ASR_VERSION,
        "registry_snapshot": f"b6v2-nonprod:{root_sha}",
        "llm": f"bedrock:{LLM_MODEL_ID}",
        "rag": f"sha256:{rag_sha}",
        "tts": "fish:s1",
    }
    reply = body["reply"]
    assert reply["tts_backend"] == "fish"
    assert reply["audio_url"].startswith("https://")
    assert "grounding_sha256" in reply, (
        "grounding provenance must survive the final boundary")
    assert [c["document_id"] for c in reply["citations"]] == ["synthetic-hours"]

    # round 5 (Codex): the response must satisfy BOTH published views of
    # the contract — the orchestrator schema AND the speech-v2 response
    # schema (grounding/citation fields were rejected by the latter)
    for relative in ("orchestrator-file-v2/response.schema.json",
                      "speech-v2/file-response.schema.json"):
        schema = json.loads(
            (ROOT / "platform/contracts/schemas" / relative).read_text())
        Draft202012Validator(
            schema, format_checker=FormatChecker()).validate(body)


def test_v2_contract_constants_match_the_committed_contracts():
    """The registry pins are self-checking: each constant must equal the
    sha256 of the committed v2 contract file it claims to bind."""
    files = {"asr": "speech-v2.yaml", "rag": "speech-v2.yaml",
             "llm": "llm-v2.yaml", "tts": "tts-v2.yaml"}
    for name, (contract_id, sha) in V2_DEPLOYED_CONTRACTS.items():
        actual = hashlib.sha256(
            (ROOT / "platform/contracts" / files[name]).read_bytes()).hexdigest()
        assert sha == actual, f"{name}: {contract_id} pin is stale"
        assert contract_id.endswith("2026-002")


def test_round8_v2_websocket_events_carry_the_full_tree(monkeypatch, tmp_path):
    """Codex round 8 (V2_WS_TREE_FIELDS=[None, None, None]): every v2
    streaming event carries the FULL artifact tree digest, exactly like
    the HTTP payload; the frozen v0 stream is unchanged."""
    import json as _json
    from fastapi.testclient import TestClient
    from medzen_asr_runtime.app import create_app

    with TestClient(create_app(StubOmniBackend())) as client:
        with client.websocket_connect(
            "/internal/v1/transcriptions/stream"
        ) as stream:
            stream.send_text(_json.dumps({
                "type": "start",
                "request_id": "66666666-6666-4666-8666-666666666666",
                "language_hint": "en",
            }))
            ready = stream.receive_json()
            assert ready["type"] == "ready"
            assert ready["artifact_tree_sha256"] == ASR_TREE_SHA
            stream.send_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            stream.send_text(_json.dumps({"type": "end_of_speech"}))
            events = [stream.receive_json() for _ in range(2)]
    kinds = {event["type"] for event in events}
    assert {"final_transcript", "completed"} <= kinds
    for event in events:
        assert event["artifact_tree_sha256"] == ASR_TREE_SHA, event["type"]


def test_round10_early_websocket_errors_are_schema_valid():
    """Codex rounds 9-10: INVALID_START and MODEL_NOT_READY bypassed the
    tree helper, used the literal 'unknown' as ids, and MODEL_NOT_READY
    sent model_versions={}. Both early paths now emit schema-valid
    events; the v2 backend's events carry the tree digest."""
    import uuid as _uuid
    from medzen_asr_runtime.app import create_app as create_asr_app

    stream_schema = json.loads(
        (ROOT / "platform/contracts/schemas/speech-v2/"
         "stream-event.schema.json").read_text())
    validator = Draft202012Validator(stream_schema,
                                      format_checker=FormatChecker())

    # INVALID_START on a ready v2 backend
    with TestClient(create_asr_app(StubOmniBackend())) as client:
        with client.websocket_connect(
            "/internal/v1/transcriptions/stream"
        ) as stream:
            stream.send_text("not json at all")
            event = stream.receive_json()
    assert event["error"]["code"] == "INVALID_START"
    assert event["artifact_tree_sha256"] == ASR_TREE_SHA
    _uuid.UUID(event["request_id"]); _uuid.UUID(event["session_id"])
    validator.validate(event)

    # MODEL_NOT_READY (no backend at all)
    class _Unready:
        ready = False
        model_versions = {}
    with TestClient(create_asr_app(_Unready())) as client:
        with client.websocket_connect(
            "/internal/v1/transcriptions/stream"
        ) as stream:
            event = stream.receive_json()
    assert event["error"]["code"] == "MODEL_NOT_READY"
    assert set(event["model_versions"]) == {
        "asr", "registry_snapshot", "llm", "rag", "tts"}
    _uuid.UUID(event["request_id"]); _uuid.UUID(event["session_id"])
    validator.validate(event)
