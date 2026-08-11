from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/speech-orchestrator"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_speech_orchestrator.app import create_app  # noqa: E402
from medzen_speech_orchestrator.emergency import EmergencyChecker  # noqa: E402
from medzen_speech_orchestrator.orchestrator import (  # noqa: E402
    OrchestratorRefusal,
    SpeechOrchestrator,
)
from medzen_speech_orchestrator.registry import (  # noqa: E402
    DEPLOYED_CLASSIFICATION,
    LocalParameterStore,
    RegistryRouter,
)
from medzen_speech_orchestrator.remote_dependencies import (  # noqa: E402
    ClusterHTTPTransport,
    RemoteASRClient,
    RemoteDependencyRefusal,
    RemoteLLMClient,
    RemoteRAGClient,
    RemoteTTSClient,
)


FIXTURE = ROOT / "platform/generated/registry-ssm/b6-v0-synthetic.json"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def fixture_root() -> str:
    value = json.loads(FIXTURE.read_bytes())
    names = [
        item["Name"] for item in value["parameters"]
        if item["Name"].endswith("/_manifest")
    ]
    assert len(names) == 1
    return names[0].removesuffix("/_manifest")


def deployed_router() -> RegistryRouter:
    return RegistryRouter(
        LocalParameterStore(FIXTURE),
        fixture_root(),
        expected_classification=DEPLOYED_CLASSIFICATION,
    )


class RecordingTransport:
    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []
        self.cancelled: list[str] = []

    def post(self, *, endpoint, request_id, body, content_type, headers=None):
        self.calls.append(("asr", endpoint, (body, content_type, headers)))
        route = deployed_router().resolve("en")
        return {
            "request_id": request_id,
            "language": "en",
            "language_probability": 1.0,
            "transcript": {
                "verbatim": "When does the fictional training desk open?",
                "normalized": "When does the fictional training desk open?",
                "normalization_version": "b6a-verbatim-v1",
            },
            "duration_seconds": 1.0,
            "model_versions": route.expected_asr_versions,
            "latency_ms": 1.0,
        }

    def post_json(self, *, endpoint, request_id, value):
        route = deployed_router().resolve("en")
        if endpoint == route.endpoint("rag"):
            self.calls.append(("rag", endpoint, value))
            citation = {
                "rank": 1,
                "document_id": "synthetic-hours",
                "title": "Fictional training desk hours",
                "source_uri": "medzen://synthetic/training-desk",
                "section": "hours",
                "content_sha256": (
                    "7e59cbce28a530171a9632e4920df90d90f5bc726205f5a04efee2ec4805d68c"
                ),
                "excerpt": "The fictional training desk opens Monday at 09:00.",
                "score": 4.0,
            }
            return {
                "request_id": request_id,
                "query_id": "c" * 64,
                "index": {
                    "alias": "current",
                    "version": "synthetic-v1",
                    "snapshot_sha256": route.rag_snapshot_sha256,
                    "classification": "SYNTHETIC_NON_CLINICAL",
                },
                "citations": [citation],
                "model_versions": {
                    "asr": None,
                    "registry_snapshot": (
                        "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001"
                    ),
                    "llm": None,
                    "rag": f"sha256:{route.rag_snapshot_sha256}",
                    "tts": None,
                },
                "latency_ms": 1.0,
            }
        if endpoint == route.endpoint("llm"):
            self.calls.append(("llm", endpoint, value))
            citations = value["rag"]["citations"]
            binding = hashlib.sha256(json.dumps(
                citations, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()).hexdigest()
            versions = dict(value["model_versions"])
            versions["llm"] = route.llm_model_version
            return {
                "request_id": request_id,
                "language": route.alias,
                "reply": {
                    "text": "Synthetic cited answer.",
                    "citations": citations,
                    "citation_binding_sha256": binding,
                },
                "policy": {"id": route.llm_policy_id, "sha256": "d" * 64},
                "provider": "fake_bedrock",
                "model_versions": versions,
                "latency_ms": 1.0,
            }
        if endpoint == route.endpoint("tts"):
            self.calls.append(("tts", endpoint, value))
            return {
                "request_id": request_id,
                "language": route.alias,
                "text": value["text"],
                "content_sha256": hashlib.sha256(value["text"].encode()).hexdigest(),
                "synthesis_key_sha256": None,
                "tts_backend": "text_only",
                "provider": "text_only",
                "audio_url": None,
                "media_type": None,
                "audio_sha256": None,
                "model_versions": value["model_versions"],
                "cache_hit": False,
                "provider_attempted": False,
                "degradation_reason": "POLICY_TEXT_ONLY",
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def cancel(self, request_id: str):
        self.cancelled.append(request_id)


def test_remote_http_chain_preserves_contract_versions_citations_and_text_fallback():
    transport = RecordingTransport()
    service = SpeechOrchestrator(
        router=deployed_router(),
        emergency=EmergencyChecker(ROOT / "registry/emergency-policies/v1.yaml"),
        asr=RemoteASRClient(transport),
        rag=RemoteRAGClient(transport),
        llm=RemoteLLMClient(transport),
        tts=RemoteTTSClient(transport),
    )
    _, result = service.handle(
        audio=b"RIFF\x00\x00\x00\x00WAVEsynthetic",
        request_id=REQUEST_ID,
        language_hint="en",
    )
    assert [call[0] for call in transport.calls] == ["asr", "rag", "llm", "tts"]
    assert result["reply"]["tts_backend"] == "text_only"
    assert result["reply"]["audio_url"] is None
    assert result["model_versions"] == {
        "asr": "v0",
        "registry_snapshot": f"b6-test:{fixture_root().rsplit('/', 1)[1]}",
        "llm": "fake-bedrock-local-v1",
        "rag": "sha256:6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160",
        "tts": None,
    }
    service.cancel(REQUEST_ID)
    assert transport.cancelled


class MismatchedRAGIdentityTransport(RecordingTransport):
    def post_json(self, *, endpoint, request_id, value):
        response = super().post_json(
            endpoint=endpoint, request_id=request_id, value=value
        )
        route = deployed_router().resolve("en")
        if endpoint == route.endpoint("rag"):
            response["index"]["snapshot_sha256"] = "0" * 64
        return response


def test_remote_http_chain_refuses_a_rag_registry_identity_mismatch():
    transport = MismatchedRAGIdentityTransport()
    service = SpeechOrchestrator(
        router=deployed_router(),
        emergency=EmergencyChecker(ROOT / "registry/emergency-policies/v1.yaml"),
        asr=RemoteASRClient(transport),
        rag=RemoteRAGClient(transport),
        llm=RemoteLLMClient(transport),
        tts=RemoteTTSClient(transport),
    )
    with pytest.raises(
        OrchestratorRefusal,
        match="RAG result does not match the request and registry",
    ) as caught:
        service.handle(
            audio=b"RIFF\x00\x00\x00\x00WAVEsynthetic",
            request_id=REQUEST_ID,
            language_hint="en",
        )
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert [call[0] for call in transport.calls] == ["asr", "rag"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://asr-runtime.medzen.svc.cluster.local:8081/internal/v1/transcriptions",
        "http://example.com:8081/internal/v1/transcriptions",
        "http://asr-runtime.medzen.svc.cluster.local:8082/internal/v1/transcriptions",
        "http://asr-runtime.medzen.svc.cluster.local:8081/public/v1/transcriptions",
    ],
)
def test_remote_transport_refuses_every_unreviewed_network_target(endpoint):
    with pytest.raises(RemoteDependencyRefusal, match="reviewed cluster boundary"):
        ClusterHTTPTransport._target(endpoint)


class FakeSSM:
    def __init__(self):
        self.parameters = {
            item["Name"]: item for item in json.loads(FIXTURE.read_bytes())["parameters"]
        }

    def get_parameter(self, **kwargs):
        return {"Parameter": self.parameters[kwargs["Name"]]}

    def get_parameters_by_path(self, **kwargs):
        return {
            "Parameters": [
                self.parameters[name] for name in sorted(self.parameters)
                if name.startswith(kwargs["Path"].rstrip("/") + "/")
            ]
        }


class FakeSecrets:
    def get_secret_value(self, **kwargs):
        assert kwargs == {"SecretId": "medzen/client-api-keys"}
        return {"SecretString": json.dumps({
            "schema_version": 1,
            "classification": "B6_6_SYNTHETIC_INTEGRATION_ONLY",
            "clients": [{
                "client_id": "b6-synthetic-backend",
                "key_sha256": hashlib.sha256(b"synthetic-key").hexdigest(),
                "enabled": True,
            }],
        })}


def test_configuration_selects_deployed_ssm_http_mode_without_calling_dependencies(
    monkeypatch,
):
    monkeypatch.setenv("MEDZEN_ORCHESTRATOR_MODE", "deployed_http_ssm")
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    monkeypatch.setenv("MEDZEN_REGISTRY_ROOT", fixture_root())
    monkeypatch.setenv("MEDZEN_CLIENT_KEYS_SECRET_ID", "medzen/client-api-keys")
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service, **kwargs: FakeSSM() if service == "ssm" else FakeSecrets(),
    )
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["mode"] == "deployed_http_ssm"
    assert ready.json()["registry_snapshot"] == (
        f"b6-test:{fixture_root().rsplit('/', 1)[1]}"
    )
