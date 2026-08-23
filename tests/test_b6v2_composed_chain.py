"""B6v2 round 3 (Codex): the GENUINELY COMPOSED chain.

Rounds 1-2 proved each seam in isolation and Codex kept finding the
joints disconnected. This test wires the REAL service classes together —
a content-addressed v2 registry snapshot through RegistryRouter, the
rag-index service's own search(), the real LLMGateway around a real
BedrockProvider (stubbed converse), the governed voice registry, the
real TTSGateway around a real RealFishProvider (stubbed HTTP) and the
real S3AudioCache (stubbed S3) — and validates the orchestrator's final
response against the v2 contract schema. Every identity that crosses a
seam (classification, contract version, omniasr/bedrock:/fish:s1,
citation subset + binding, presigned delivery) is exercised end to end.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
for service in ("speech-orchestrator", "rag-index", "llm-gateway",
                "speech-tts-gateway"):
    sys.path.insert(0, str(ROOT / f"services/{service}"))

from medzen_speech_orchestrator.emergency import EmergencyChecker  # noqa: E402
from medzen_speech_orchestrator.local_dependencies import (  # noqa: E402
    ASRResult,
    LocalLLMClient,
    LocalRAGClient,
)
from medzen_speech_orchestrator.orchestrator import SpeechOrchestrator  # noqa: E402
from medzen_speech_orchestrator.registry import (  # noqa: E402
    DEPLOYED_ENDPOINTS,
    V2_CLASSIFICATION,
    V2_CONTRACT_VERSION,
    V2_DEPLOYED_CONTRACTS,
    Parameter,
    RegistryRouter,
    canonical_json,
)

REQUEST_ID = "22222222-2222-4222-8222-222222222222"
ASR_TREE_SHA = "ab" * 32
# round 6 (Codex): the version IS the tree digest's prefix — the
# registry refuses a version naming a different artifact than the tree
ASR_VERSION = f"omniasr_ctc_1b:{ASR_TREE_SHA[:12]}"
ASR_REPORTED = "omniasr-nonprod:" + "cd" * 32
LLM_VERSION = "bedrock:eu.anthropic.claude-sonnet-4-5"
RAG_SHA = "ef" * 32
QUERY = "nfata imiti gute"


# ---------------------------------------------------------- v2 snapshot
def _v2_route() -> dict:
    return {
        "schema_version": 2,
        "classification": V2_CLASSIFICATION,
        "contract_version": V2_CONTRACT_VERSION,
        "language": {"alias": "kinyarwanda", "response_code": "kin",
                      "accepted_codes": ["kin"]},
        "asr": {"backend": "http_cluster_v1", "model_version": ASR_VERSION,
                 "artifact_tree_sha256": ASR_TREE_SHA,
                 "reported_registry_snapshot": ASR_REPORTED},
        "rag": {"alias": "current", "snapshot_sha256": RAG_SHA,
                 "query_language": "kin"},
        "llm": {"model_version": LLM_VERSION,
                 "policy_id": "kinyarwanda-medzen-v1"},
        "tts": {"backend": "http_fish_v2", "model_version": "fish:s1"},
        "dependencies": {
            name: {"endpoint": DEPLOYED_ENDPOINTS[name],
                    "contract_id": V2_DEPLOYED_CONTRACTS[name][0],
                    "contract_sha256": V2_DEPLOYED_CONTRACTS[name][1]}
            for name in DEPLOYED_ENDPOINTS
        },
    }


class InMemoryStore:
    """SSM-shaped store holding the content-addressed v2 snapshot."""

    def __init__(self):
        index = {"schema_version": 1, "default_language": "kinyarwanda",
                 "languages": [{"alias": "kinyarwanda", "codes": ["kin"],
                                 "route_parameter": "routes/kinyarwanda"}]}
        route = _v2_route()
        material = {"schema_version": 1, "classification": V2_CLASSIFICATION,
                    "index": index, "routes": {"kinyarwanda": route}}
        self.snapshot_sha256 = hashlib.sha256(
            canonical_json(material)).hexdigest()
        self.root = f"/medzen/registry/nonprod/b6v2/{self.snapshot_sha256}"
        values = {"index": index, "routes/kinyarwanda": route}
        manifest = {
            "schema_version": 1,
            "classification": V2_CLASSIFICATION,
            "snapshot_sha256": self.snapshot_sha256,
            "snapshot_material_sha256": self.snapshot_sha256,
            "parameter_value_sha256": {
                rel: hashlib.sha256(canonical_json(obj)).hexdigest()
                for rel, obj in values.items()
            },
        }
        self._parameters = {
            f"{self.root}/_manifest": Parameter(
                Name=f"{self.root}/_manifest", Type="SecureString",
                Value=canonical_json(manifest).decode(), Version=1),
        }
        for rel, obj in values.items():
            name = f"{self.root}/{rel}"
            self._parameters[name] = Parameter(
                Name=name, Type="SecureString",
                Value=canonical_json(obj).decode(), Version=1)

    def get_parameter(self, name: str) -> Parameter:
        return self._parameters[name]

    def get_parameters_by_path(self, path: str) -> tuple[Parameter, ...]:
        prefix = path.rstrip("/") + "/"
        return tuple(self._parameters[name]
                     for name in sorted(self._parameters)
                     if name.startswith(prefix))


# ------------------------------------------------------------- ASR seam
class ComposedASRClient:
    """The GPU OmniASR runtime cannot run in this venv; this stub honours
    the exact identity contract the real backend reports (the marker's
    model_version + omniasr-nonprod snapshot — see OmniASRBackend)."""

    def transcribe(self, audio, *, request_id, route):
        return ASRResult(
            request_id=request_id,
            language="kin",
            language_probability=1.0,
            transcript={
                "verbatim": QUERY,
                "normalized": QUERY,
                "normalization_version": "b6v2-unicode-nfc-whitespace-v1",
            },
            duration_seconds=1.0,
            model_versions=dict(route.expected_asr_versions),
            # round 7: the FULL tree digest, compared exactly in v2
            artifact_tree_sha256=route.asr_artifact_tree_sha256,
        )


# ------------------------------------------------------------- RAG seam
def _rag_client() -> LocalRAGClient:
    from medzen_rag_index.index import Document, IndexRepository, LoadedIndex, _tokens

    def doc(document_id, title, text):
        return Document(
            document_id=document_id, title=title,
            source_uri=f"medzen://synthetic/{document_id}",
            section="guidance", language="kin", text=text,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            title_tokens=_tokens(title), text_tokens=_tokens(text),
        )

    loaded = LoadedIndex(
        alias="current", version="b6v2-composed-v1",
        snapshot_sha256=RAG_SHA,
        classification="SYNTHETIC_NON_CLINICAL",
        documents=(
            doc("doc-1", "gufata imiti",
                "Fata imiti kabiri ku munsi nyuma yo kurya."),
            doc("doc-2", "kubika imiti",
                "Bika imiti ahantu hakonje kandi hatarimo urumuri."),
        ),
    )
    repository = IndexRepository.__new__(IndexRepository)
    repository.root = ROOT
    repository.alias = "current"
    repository.loaded = loaded

    class ServiceIdentityRAGClient(LocalRAGClient):
        """Round 4: over HTTP the rag-index service reports its OWN
        identity (LoadedIndex.model_versions), never the orchestrator's
        route — the round-3 route-echo hid a broken deployed seam."""

        def retrieve(self, *, request_id, query, route):
            response = super().retrieve(
                request_id=request_id, query=query, route=route)
            response["model_versions"] = (
                self._repository.loaded.model_versions)
            return response

    client = ServiceIdentityRAGClient.__new__(ServiceIdentityRAGClient)
    client._repository = repository
    return client


# ------------------------------------------------------------- LLM seam
class StubBedrockClient:
    """Contract-shaped converse() that cites a strict SUBSET."""

    def __init__(self, cited_ids=("doc-1",),
                 text="Fata imiti kabiri ku munsi nyuma yo kurya."):
        self.calls = []
        self._cited_ids = list(cited_ids)
        self._text = text

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [
            {"text": json.dumps({
                "text": self._text,
                "cited_document_ids": self._cited_ids,
            })}]}}}


def _llm_client(bedrock_stub) -> LocalLLMClient:
    from medzen_llm_gateway.gateway import LLMGateway
    from medzen_llm_gateway.policy import PolicyStore
    from medzen_llm_gateway.provider import BedrockProvider
    from medzen_llm_gateway.shared_resilience import CircuitBreaker, load_config

    policies = PolicyStore(ROOT / "registry/languages",
                           ROOT / "registry/llm-policies/v1.yaml")
    config = load_config()
    defaults = config["circuit_breakers"]["defaults"]
    bedrock = config["circuit_breakers"]["per_provider"]["bedrock"]
    breaker = CircuitBreaker(
        name="bedrock",
        failure_threshold=bedrock["failure_threshold"],
        timeout_threshold=defaults["timeout_threshold"],
        window_s=defaults["window_s"],
        open_duration_s=bedrock["open_duration_s"],
        half_open_max_calls=defaults["half_open_max_calls"],
    )
    provider = BedrockProvider(
        model_id="eu.anthropic.claude-sonnet-4-5",
        region="eu-central-1", client=bedrock_stub)
    client = LocalLLMClient.__new__(LocalLLMClient)
    client._gateway = LLMGateway(policies, provider, breaker)
    return client


# ------------------------------------------------------------- TTS seam
class StubFishResponse:
    status_code = 200
    ok = True
    content = b"MEDZEN-COMPOSED-MP3-BYTES"
    text = ""


class StubFishSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return StubFishResponse()


class FakeS3:
    def __init__(self):
        self.store = {}

    def put_object(self, **kw):
        assert kw["ServerSideEncryption"] == "aws:kms"
        assert kw["SSEKMSKeyId"]
        self.store[kw["Key"]] = kw

    def head_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise KeyError(Key)
        item = self.store[Key]
        return {"ContentType": item["ContentType"],
                "Metadata": item.get("Metadata", {})}

    def generate_presigned_url(self, op, *, Params, ExpiresIn):
        assert ExpiresIn <= 3600
        return (f"https://s3.example/{Params['Bucket']}/{Params['Key']}"
                f"?X-Amz-Expires={ExpiresIn}")


class ComposedTTSClient:
    """Adapts the orchestrator's call to the REAL TTSGateway, mirroring
    LocalLLMClient's adaptation pattern."""

    def __init__(self, fish_session, s3_client):
        from medzen_speech_tts_gateway.gateway import TTSGateway
        from medzen_speech_tts_gateway.provider import RealFishProvider
        from medzen_speech_tts_gateway.s3_cache import S3AudioCache
        from medzen_speech_tts_gateway.shared_resilience import (
            CircuitBreaker,
            load_config,
        )
        from medzen_speech_tts_gateway.voices import enforce_model, select_voice

        config = load_config()
        defaults = config["circuit_breakers"]["defaults"]
        fish = config["circuit_breakers"]["per_provider"]["fish"]
        breaker = CircuitBreaker(
            name="fish",
            failure_threshold=fish["failure_threshold"],
            timeout_threshold=defaults["timeout_threshold"],
            window_s=defaults["window_s"],
            open_duration_s=fish["open_duration_s"],
            half_open_max_calls=defaults["half_open_max_calls"],
        )

        def governed(language):
            voice = select_voice(language)
            return voice.reference_id, enforce_model(voice, None)

        self._gateway = TTSGateway(
            provider=RealFishProvider(api_key="composed-test-key",
                                       session=fish_session),
            breaker=breaker,
            voice_resolver=governed,
            cache=S3AudioCache(bucket="composed-cache",
                                kms_key_arn="arn:aws:kms:eu-central-1:0:key/t",
                                client=s3_client),
        )

    def synthesize(self, *, request_id, language, text, versions, route):
        return self._gateway.synthesize({
            "request_id": request_id,
            "language": language,
            "text": text,
            "model_versions": versions,
        })


VOICES_INLINE = json.dumps({
    "kinyarwanda": {
        "reference_id": "da02ddd729004bb98133102da10c36ba",
        "model": "s1",
        "label": "composed-test voice",
        "approved": True,
        "consent_evidence": "owner order 2026-08-20 (chat, verbatim)",
    },
})


@pytest.fixture()
def composed(monkeypatch):
    import medzen_speech_tts_gateway.voices as voices

    monkeypatch.setenv("MEDZEN_TTS_VOICES_INLINE", VOICES_INLINE)
    monkeypatch.setattr(voices, "_cache", {})
    store = InMemoryStore()
    router = RegistryRouter(store, store.root,
                            expected_classification=V2_CLASSIFICATION)
    bedrock = StubBedrockClient()
    fish = StubFishSession()
    s3 = FakeS3()
    orchestrator = SpeechOrchestrator(
        router=router,
        emergency=EmergencyChecker(ROOT / "registry/emergency-policies/v1.yaml"),
        asr=ComposedASRClient(),
        rag=_rag_client(),
        llm=_llm_client(bedrock),
        tts=ComposedTTSClient(fish, s3),
    )
    return orchestrator, store, bedrock, fish, s3


def test_composed_v2_chain_end_to_end(composed):
    orchestrator, store, bedrock, fish, s3 = composed
    _, response = orchestrator.handle(
        audio=b"RIFF\x00\x00\x00\x00WAVEcomposed",
        request_id=REQUEST_ID,
        language_hint="kin",
        response_audio=True,
    )
    # one real Bedrock converse, one real Fish POST, one SSE-KMS S3 object
    assert len(bedrock.calls) == 1
    assert len(fish.calls) == 1
    assert fish.calls[0][1]["headers"]["model"] == "s1"
    assert len(s3.store) == 1

    # v2 identities all the way through
    assert response["model_versions"] == {
        "asr": ASR_VERSION,
        "registry_snapshot": f"b6v2-nonprod:{store.snapshot_sha256}",
        "llm": LLM_VERSION,
        "rag": f"sha256:{RAG_SHA}",
        "tts": "fish:s1",
    }

    # citation SUBSET: doc-1 only, byte-equal to the supplied citation,
    # binding computed over what the reply carries
    reply = response["reply"]
    assert [c["document_id"] for c in reply["citations"]] == ["doc-1"]
    assert reply["citations"][0]["grounding_text"].startswith("Fata imiti")
    binding = hashlib.sha256(json.dumps(
        reply["citations"], sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode()).hexdigest()
    assert reply["citation_binding_sha256"] == binding

    # presigned, expiring delivery — never medzen+local
    assert reply["tts_backend"] == "fish"
    assert reply["audio_url"].startswith("https://")
    assert "X-Amz-Expires" in reply["audio_url"]
    assert "medzen+local" not in reply["audio_url"]

    # the final response IS the v2 contract
    schema = json.loads((ROOT / "platform/contracts/schemas/"
                         "orchestrator-file-v2/response.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(response)


def test_composed_default_makes_zero_provider_tts_calls(composed):
    orchestrator, store, bedrock, fish, s3 = composed
    _, response = orchestrator.handle(
        audio=b"RIFF\x00\x00\x00\x00WAVEcomposed",
        request_id=REQUEST_ID,
        language_hint="kin",
    )
    assert fish.calls == []
    assert s3.store == {}
    assert response["reply"]["tts_backend"] == "text_only"
    assert response["reply"]["audio_url"] is None
    assert response["model_versions"]["tts"] is None


def test_composed_blank_grounding_refuses_before_bedrock(composed):
    from medzen_llm_gateway.gateway import GatewayRefusal

    orchestrator, store, bedrock, fish, s3 = composed
    rag = orchestrator.rag.retrieve(
        request_id=REQUEST_ID, query=QUERY,
        route=orchestrator.router.resolve("kin"))
    # gateway validation already rejects EMPTY excerpts; the provider
    # refusal guards the hole string-truthiness misses — whitespace-only
    # grounding, which passes every "non-empty string" check upstream
    blanked = [dict(c, grounding_text=" ", excerpt=" \n ")
               for c in rag["citations"]]
    with pytest.raises(GatewayRefusal) as caught:
        orchestrator.llm._gateway.complete({
            "request_id": REQUEST_ID,
            "language": "kinyarwanda",
            "transcript": {"verbatim": QUERY, "normalized": QUERY,
                            "normalization_version": "b6v2-unicode-nfc-whitespace-v1"},
            "rag": {"query_id": rag["query_id"],
                     "index_snapshot_sha256": rag["index"]["snapshot_sha256"],
                     "citations": blanked},
            "model_versions": {"asr": ASR_VERSION,
                                "registry_snapshot": f"b6v2-nonprod:{store.snapshot_sha256}",
                                "llm": None, "rag": f"sha256:{RAG_SHA}",
                                "tts": None},
        })
    assert caught.value.code == "BLANK_GROUNDING"
    assert caught.value.status_code == 422
    assert bedrock.calls == [], "blank grounding must never reach Bedrock"
