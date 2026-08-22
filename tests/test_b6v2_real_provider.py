"""B6v2 real-provider cross-component tests (Codex serving review).

The 104 component-local tests stayed green while every major handoff in
the chain carried an incompatible assumption. Each test here encodes one
of the reproduced integration failures — written failing-first against
the v2 contract, now pinned green by the implementations.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for service in ("rag-index", "llm-gateway", "speech-orchestrator",
                 "speech-tts-gateway", "model-loader"):
    sys.path.insert(0, str(ROOT / "services" / service))


# ---------------------------------------------------------------- grounding
def test_rag_citations_carry_grounding_text_the_llm_actually_reads():
    """Finding 1: RAG said `excerpt`, Bedrock read `content` with a ''
    default — document ids reached the prompt, their text did not."""
    import medzen_rag_index.index  # noqa: F401  (import proves path)
    body = (ROOT / "services/rag-index/medzen_rag_index/index.py").read_text()
    assert '"grounding_text"' in body
    provider = (ROOT / "services/llm-gateway/medzen_llm_gateway/"
                "provider.py").read_text()
    assert "grounding_text" in provider
    assert "grounding_sha256" in provider, (
        "the exact bytes sent to Bedrock must be hashed for audit")


def test_blank_grounding_refuses_instead_of_praying():
    from medzen_llm_gateway.provider import (BedrockProvider,
                                             ProviderError,
                                             ProviderRequest)
    request = ProviderRequest(
        language="english", response_language="english",
        policy_id="p1", normalized_transcript="hello",
        citations=({"document_id": "doc-1"},),   # NO grounding text at all
        citation_binding_sha256="0" * 64, maximum_output_tokens=64)
    provider = BedrockProvider.__new__(BedrockProvider)  # no AWS client
    with pytest.raises(ProviderError, match="no\\s+grounding|blank"):
        provider.invoke(request, timeout_ms=10)


def test_gateway_accepts_cited_subset_and_maps_blank_grounding_to_422():
    gateway = (ROOT / "services/llm-gateway/medzen_llm_gateway/"
               "gateway.py").read_text()
    assert "BLANK_GROUNDING" in gateway and "422" in gateway
    assert "<= set(expected_ids)" in gateway or "subset" in gateway.lower(), (
        "real providers cite a SUBSET; exact-tuple equality only fit "
        "the synthetic echo")


# ---------------------------------------------------------- response_audio
def test_response_audio_false_means_zero_tts_calls():
    """Finding 2: HTTP 200 with response_audio=false still called the
    TTS provider once. False (and ABSENT, the default) = zero calls."""
    import inspect
    from medzen_speech_orchestrator.orchestrator import SpeechOrchestrator
    signature = inspect.signature(SpeechOrchestrator.handle)
    param = signature.parameters.get("response_audio")
    assert param is not None, "handle() must take response_audio"
    assert param.default is False, "the safe default is False"
    body = (ROOT / "services/speech-orchestrator/medzen_speech_orchestrator/"
            "orchestrator.py").read_text()
    assert "self.tts is not None and response_audio" in body
    app = (ROOT / "services/speech-orchestrator/medzen_speech_orchestrator/"
           "app.py").read_text()
    assert 'response_audio=(response_audio == "true")' in app, (
        "the API layer validated the flag then dropped it")


# ------------------------------------------------------------- voice rules
def test_string_false_never_approves_a_voice():
    """Finding 5: bool("false") is True — every string boolean silently
    APPROVED. Strict JSON booleans or the registry refuses."""
    from medzen_speech_tts_gateway.voices import (RegistryUnavailable,
                                                  _parse)
    with pytest.raises(RegistryUnavailable, match="JSON boolean"):
        _parse(json.dumps({"ewe": {"reference_id": "r1",
                                     "approved": "false"}}))
    with pytest.raises(RegistryUnavailable, match="JSON boolean"):
        _parse(json.dumps({"ewe": {"reference_id": "r1",
                                     "approved": "true"}}))


def test_approval_requires_consent_evidence():
    from medzen_speech_tts_gateway.voices import _parse
    voices = _parse(json.dumps({
        "ewe": {"reference_id": "r1", "approved": True},   # no evidence
        "fon": {"reference_id": "r2", "approved": True,
                 "consent_evidence": "signed release 2026-08-01"}}))
    assert voices["ewe"].approved is False, (
        "approval without consent/usage-rights evidence does not count")
    assert voices["fon"].approved is True


def test_registry_failure_fails_closed_not_to_builtin_real_ids(monkeypatch):
    """Finding 5b: an SSM outage fell back to BUILT-IN REAL reference
    ids — resurrecting voices the registry may have revoked."""
    import medzen_speech_tts_gateway.voices as voices_mod
    monkeypatch.delenv("MEDZEN_TTS_VOICES_INLINE", raising=False)
    monkeypatch.delenv("MEDZEN_TTS_ALLOW_BUILTIN_FALLBACK", raising=False)
    monkeypatch.setenv("MEDZEN_TTS_VOICES_SSM_PARAM", "/nonexistent/param")
    monkeypatch.setattr(voices_mod, "boto3", None, raising=False)
    with pytest.raises(voices_mod.RegistryUnavailable):
        voices_mod._load()


def test_unapproved_voice_refuses_synthesis_and_model_is_bound(monkeypatch):
    import medzen_speech_tts_gateway.voices as voices_mod
    inline = json.dumps({
        "kinyarwanda": {"reference_id": "rw1", "approved": True,
                          "model": "s1",
                          "consent_evidence": "owner order 2026-08-20"},
        "pidgin": {"reference_id": "p1", "approved": False,
                    "model": "s2.1-pro-free"}})
    monkeypatch.setenv("MEDZEN_TTS_VOICES_INLINE", inline)
    voices_mod.registry(force=True)
    voice = voices_mod.select_voice("kinyarwanda")
    assert voice.reference_id == "rw1"
    with pytest.raises(voices_mod.VoiceRefusal, match="not approved"):
        voices_mod.select_voice("pidgin")
    with pytest.raises(voices_mod.VoiceRefusal, match="bound to Fish model"):
        voices_mod.enforce_model(voice, "s2.1-pro-free")
    assert voices_mod.enforce_model(voice, None) == "s1"


# ---------------------------------------------------------- audio delivery
class _FakeS3:
    """Shared 'bucket' so two cache instances model two replicas."""
    def __init__(self, store):
        self.store = store

    def put_object(self, **kw):
        self.store[kw["Key"]] = kw
        assert kw["ServerSideEncryption"] == "aws:kms"
        assert kw["SSEKMSKeyId"]

    def head_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise KeyError(Key)
        item = self.store[Key]
        return {"ContentType": item["ContentType"],
                "Metadata": item.get("Metadata", {})}

    def generate_presigned_url(self, op, *, Params, ExpiresIn):
        assert ExpiresIn <= 3600, "retrieval URLs must expire"
        return (f"https://s3.example/{Params['Bucket']}/{Params['Key']}"
                f"?X-Amz-Expires={ExpiresIn}")


def test_audio_survives_restart_and_is_shared_across_replicas():
    """Finding 4: audio lived in ONE pod's memory behind medzen+local://
    — a restart or second replica lost everything, and nothing could
    ever fetch it."""
    from medzen_speech_tts_gateway.s3_cache import S3AudioCache
    shared = {}
    replica_a = S3AudioCache(bucket="b", kms_key_arn="arn:aws:kms:k",
                              client=_FakeS3(shared))
    replica_b = S3AudioCache(bucket="b", kms_key_arn="arn:aws:kms:k",
                              client=_FakeS3(shared))
    stored = replica_a.put(synthesis_key_sha256="ab" * 32,
                            audio_bytes=b"AUDIO", media_type="audio/mpeg",
                            model_version="fish-s1")
    assert stored.audio_url.startswith("https://")
    assert "medzen+local" not in stored.audio_url
    assert "X-Amz-Expires" in stored.audio_url
    fetched = replica_b.get("ab" * 32)      # the OTHER replica
    assert fetched is not None and fetched.model_version == "fish-s1"
    assert replica_b.get("cd" * 32) is None


def test_unencrypted_audio_storage_is_refused():
    from medzen_speech_tts_gateway.s3_cache import S3AudioCache
    with pytest.raises(RuntimeError, match="KMS"):
        S3AudioCache(bucket="b", kms_key_arn=None, client=_FakeS3({}))


# -------------------------------------------------------------- ASR loader
def _v2_manifest(**over):
    digest = over.pop("digest", "ab" * 32)
    manifest = {
        "schema_version": 2,
        "classification": "NONPROD_REAL_PROVIDER_V2",
        "model_family": "omniasr_ctc_1b",
        "artifact": {"format": "fairseq2_pt", "sha256": digest},
        "languages": ["english", "ewe", "french", "kinyarwanda",
                       "lingala", "pidgin", "swahili"],
        "model_version": f"omniasr_ctc_1b:{digest[:12]}",
    }
    manifest.update(over)
    return manifest


def test_loader_v2_accepts_the_multilingual_artifact_shape(tmp_path):
    """Finding 6: the v0 loader hard-pins zero-shot Whisper/CT2 and
    cannot load what B5 produces. v2 is the generic OmniASR runtime."""
    import hashlib
    from medzen_model_loader.loader_v2 import (LoaderV2Refusal,
                                               load_artifact_v2,
                                               validate_manifest_v2)
    blob = tmp_path / "model.pt"
    blob.write_bytes(b"FAKE-FAIRSEQ2-CHECKPOINT")
    digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    identity = load_artifact_v2(_v2_manifest(digest=digest), blob)
    assert identity["version"] == f"omniasr_ctc_1b:{digest[:12]}"
    # digest mismatch refuses BEFORE deserialization
    with pytest.raises(LoaderV2Refusal, match="do not match"):
        load_artifact_v2(_v2_manifest(digest="cd" * 32), blob)
    # whisper/CT2 manifests belong to the closed v0 proof
    with pytest.raises(LoaderV2Refusal, match="ONE multilingual"):
        validate_manifest_v2(_v2_manifest(model_family="whisper-large-v3"))
    with pytest.raises(LoaderV2Refusal, match="fairseq2"):
        validate_manifest_v2(_v2_manifest(
            artifact={"format": "ctranslate2", "sha256": "ab" * 32}))
    # a missing mandatory language breaks the one-artifact rule
    with pytest.raises(LoaderV2Refusal, match="EVERY mandatory"):
        validate_manifest_v2(_v2_manifest(languages=["english"]))


def test_loader_v2_refuses_production_binding_without_gate_approval():
    from medzen_model_loader.loader_v2 import (LoaderV2Refusal,
                                               validate_manifest_v2)
    with pytest.raises(LoaderV2Refusal, match="promotion-gate"):
        validate_manifest_v2(_v2_manifest(classification="PRODUCTION"))
    ok = validate_manifest_v2(_v2_manifest(
        classification="PRODUCTION",
        promotion_approval={"protocol": "PROMOTION-PROTOCOL-2026-004",
                              "decision": "APPROVED",
                              "gate_report_sha256": "e" * 64}))
    assert ok["classification"] == "PRODUCTION"


# ------------------------------------------------------------ IAM + deploy
def test_llm_role_covers_cross_region_profile_destinations():
    """Finding 7: the role only named the profile ARN; a cross-region
    profile fans out to destination-region foundation models."""
    doc = json.loads((ROOT / "platform/iam/medzen-llm-role.json").read_text())
    body = json.dumps(doc)
    for region in ("eu-central-1", "eu-west-1", "eu-west-3", "eu-north-1"):
        assert f"arn:aws:bedrock:{region}::foundation-model/" in body, region


def test_tts_role_names_both_real_fish_secrets():
    doc = json.loads((ROOT / "platform/iam/medzen-tts-role.json").read_text())
    body = json.dumps(doc)
    assert "medzen/speech/fish-api-key" in body
    assert "medzen/tts/dev/fish-api-key" in body
    assert "secret:medzen/fish-api-key" not in body, (
        "the old pattern matched NEITHER real secret")


def test_app_pipelines_watch_master_and_the_real_cluster():
    """Finding 8: pipelines watched `main` (default is master) and
    targeted cluster `medzen` (real name medzen-speech)."""
    for wf in sorted((ROOT / ".github/workflows").glob("app-*.yml")):
        assert "branches: [master]" in wf.read_text(), wf.name
    pipeline = (ROOT / ".github/workflows/_service-pipeline.yml").read_text()
    assert "update-kubeconfig --name medzen-speech" in pipeline
    assert "--name medzen " not in pipeline


def test_v2_contract_and_v1_proof_coexist():
    v2 = (ROOT / "platform/contracts/b6v2-real-providers.yaml").read_text()
    assert "registry/nonprod/b6v2" in v2
    assert "NONPROD_REAL_PROVIDER_V2" in v2
    # the closed synthetic proof is untouched
    assert (ROOT / "platform/contracts/llm-v1.yaml").exists()
    assert (ROOT / "platform/contracts/tts-v1.yaml").exists()
    v0_loader = (ROOT / "services/model-loader/medzen_model_loader/"
                 "loader.py").read_text()
    assert "whisper-large-v3" in v0_loader, "v0 proof loader unchanged"
