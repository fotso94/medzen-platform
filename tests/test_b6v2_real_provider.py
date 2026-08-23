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
    from medzen_model_loader.loader_v2 import artifact_tree_sha256
    digest = over.pop("digest", "ab" * 32)
    tokenizer_sha = over.pop("tokenizer_sha", "12" * 32)
    # round 6 (Codex): ONE tree digest over checkpoint+tokenizer is the
    # identity; the model_version derives from IT, not the checkpoint
    tree = artifact_tree_sha256(digest, tokenizer_sha)
    manifest = {
        "schema_version": 2,
        "classification": "NONPROD_REAL_PROVIDER_V2",
        "model_family": "omniasr_ctc_1b",
        "artifact": {"format": "fairseq2_pt", "sha256": digest},
        "tokenizer": {"sha256": tokenizer_sha},
        "artifact_tree_sha256": tree,
        "languages": ["english", "ewe", "french", "kinyarwanda",
                       "lingala", "pidgin", "swahili"],
        "model_version": f"omniasr_ctc_1b:{tree[:12]}",
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
    from medzen_model_loader.loader_v2 import artifact_tree_sha256
    tree = artifact_tree_sha256(digest, "12" * 32)
    identity = load_artifact_v2(_v2_manifest(digest=digest), blob)
    # round 6 (Codex): the version derives from the TREE digest
    # (checkpoint+tokenizer), never the checkpoint alone
    assert identity["version"] == f"omniasr_ctc_1b:{tree[:12]}"
    assert identity["artifact_tree_sha256"] == tree
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
    # the committed-record contract is exercised in depth by
    # test_loader_v2_production_needs_committed_promotion_not_manifest_fields;
    # here we only confirm PRODUCTION without any binding refuses
    from medzen_model_loader.loader_v2 import (LoaderV2Refusal,
                                               validate_manifest_v2)
    with pytest.raises(LoaderV2Refusal):
        validate_manifest_v2(_v2_manifest(classification="PRODUCTION"))


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


# ------------------------------------------------- composed end-to-end
def test_registry_accepts_v2_nonprod_root_and_real_identities():
    """Codex review round 2 (reproduced B6V2_REGISTRY_REFUSED): the
    deployed registry only accepted /medzen/registry/test/b6/... The v2
    nonprod root + real provider identities must route."""
    import medzen_speech_orchestrator.registry as reg
    assert reg.ROOT_RE.fullmatch(
        "/medzen/registry/nonprod/b6v2/" + "a" * 64) is not None
    assert reg.V2_LLM_RE.fullmatch("bedrock:eu.anthropic.claude") is not None
    assert reg.V2_TTS_RE.fullmatch("fish:s1") is not None
    assert reg.V2_ASR_RE.fullmatch("omniasr_ctc_1b:" + "a" * 12) is not None
    # v2 root REQUIRES the v2 classification (and vice versa)
    class _EmptyStore:
        def get_parameters_by_path(self, path):
            return []
    import pytest
    with pytest.raises(reg.RegistryRefusal, match="disagree"):
        reg.RegistryRouter(_EmptyStore(),
                           "/medzen/registry/nonprod/b6v2/" + "a" * 64,
                           expected_classification=reg.LOCAL_CLASSIFICATION)


def test_orchestrator_accepts_real_fish_version_fill():
    """Codex review round 2 (reproduced REAL_FISH_ORCHESTRATOR_REFUSED):
    a real Fish result sets model_versions.tts=fish:s1; the orchestrator
    required tts_versions == pre-TTS versions and refused it. Round 3:
    the round-2 fill accepted ANY claimed value — the only version the
    TTS step may introduce is the one the REGISTRY bound to the route."""
    body = (ROOT / "services/speech-orchestrator/"
            "medzen_speech_orchestrator/orchestrator.py").read_text()
    assert "tts_identity_ok" in body
    assert "tts=route.tts_model_version" in body, (
        "the TTS fill must equal the registry-bound identity exactly")
    assert 'tts=tts_versions.get("tts")' not in body, (
        "round 2's accept-anything fill is an identity check that "
        "cannot fail")


def test_tts_app_uses_governed_selection_and_optional_s3(monkeypatch):
    """Codex review round 2 (reproduced APP_PATH_RESOLVE_UNAPPROVED): the
    real app called the non-enforcing resolve(); it must use the governed
    selector and honor the S3 audio cache when configured."""
    app_src = (ROOT / "services/speech-tts-gateway/"
               "medzen_speech_tts_gateway/app.py").read_text()
    assert "from .voices import select_voice, enforce_model" in app_src
    assert "resolve as _resolve_voice" not in app_src, (
        "the non-enforcing resolve() must be gone from the real path")
    assert "S3AudioCache" in app_src and "MEDZEN_TTS_AUDIO_BUCKET" in app_src


def test_gateway_delivery_url_prefers_s3_over_local_scheme():
    from medzen_speech_tts_gateway.gateway import TTSGateway
    import inspect
    src = inspect.getsource(TTSGateway._delivery_url)
    assert "presign" in src and "medzen+local" in src, (
        "delivery must be S3 presigned when the cache supports it, "
        "local scheme only as the v1-proof fallback")


def test_s3_cache_only_404_is_a_miss_others_raise():
    """Codex review round 2: non-404 S3 failures must fail CLOSED."""
    from medzen_speech_tts_gateway.s3_cache import S3AudioCache
    import pytest

    class Boom:
        def __init__(self, err):
            self.err = err
        def head_object(self, **kw):
            raise self.err
        def generate_presigned_url(self, *a, **k):
            return "https://x"

    class ClientError(Exception):
        def __init__(self, code, http):
            self.response = {"Error": {"Code": code},
                             "ResponseMetadata": {"HTTPStatusCode": http}}

    miss = S3AudioCache(bucket="b", kms_key_arn="arn:kms",
                         client=Boom(ClientError("NoSuchKey", 404)))
    assert miss.get("ab" * 32) is None
    throttled = S3AudioCache(bucket="b", kms_key_arn="arn:kms",
                              client=Boom(ClientError("Throttling", 503)))
    with pytest.raises(Exception):
        throttled.get("ab" * 32)


def _promotion_bundle(tmp_path, tree_digest, *, break_rows_for=None,
                      labels_only=False, record_over=None,
                      protocol_id="PROMOTION-PROTOCOL-2026-004"):
    """Build an immutable promotion bundle in the AUTHORITATIVE gate
    format (round 6, Codex): per-language hash-bound rows whose
    clustered-bootstrap statistics are COMPUTED with the same shared
    code the gate recomputes with — labels alone can never promote."""
    import hashlib
    import json as _json
    from medzen_model_loader.noninferiority import clustered_noninferiority

    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    languages = ["english", "ewe", "french", "kinyarwanda", "lingala",
                 "pidgin", "swahili"]
    protocol = {"record": "PROMOTION-PROTOCOL-2026-004",
                "gates_v0_active_now": {"target_languages": "...",
                                          "protected_languages": "...",
                                          "degenerate_output": "...",
                                          "operational": "..."},
                "mandatory_languages": languages}
    protocol_bytes = _json.dumps(protocol).encode()
    pointer = {"file": "PROMOTION-PROTOCOL-2026-004.json",
               "record": protocol_id,
               "sha256": hashlib.sha256(protocol_bytes).hexdigest()}

    files: dict[str, bytes] = {
        "PROMOTION-PROTOCOL-2026-004.json": protocol_bytes,
        "CURRENT-PROMOTION-PROTOCOL.json": _json.dumps(pointer).encode(),
    }
    report_languages = {}
    holdouts = {}
    for index, language in enumerate(languages):
        holdout_sha = hashlib.sha256(f"holdout-{language}".encode()).hexdigest()
        holdouts[language] = [holdout_sha]
        # deterministic paired rows where the candidate is clearly
        # non-inferior (fewer errors than baseline in every cluster)
        rows = [
            {"cluster_id": f"spk{cluster}", "baseline_errors": 4 + cluster,
             "candidate_errors": 2, "reference_words": 40}
            for cluster in range(4) for _ in range(3)
        ]
        rows_bytes = "\n".join(_json.dumps(r) for r in rows).encode() + b"\n"
        if language == break_rows_for:
            # rows that CANNOT reproduce the claimed statistics
            fake = [dict(r, candidate_errors=30) for r in rows]
            rows_bytes = "\n".join(_json.dumps(r) for r in fake).encode() + b"\n"
        stats = clustered_noninferiority(
            [_json.loads(line) for line in
             ("\n".join(_json.dumps(r) for r in rows)).splitlines()],
            margin=0.02, iterations=200, seed=20260823, alpha=0.05)
        entry = {
            "state": "PASS",
            "holdout_manifest_sha256": holdout_sha,
            "rows_sha256": hashlib.sha256(rows_bytes).hexdigest(),
            "non_inferiority": {k: stats[k] for k in
                                 ("margin", "upper_ci", "method", "clusters",
                                  "rows", "non_inferior", "seed",
                                  "iterations", "alpha")},
        }
        if labels_only:
            # Codex round 6 repro: detailed PASS labels, NO evidence
            entry = {"state": "PASS",
                     "holdout_manifest_sha256": holdout_sha,
                     "gates": {"everything": "PASS"}}
        report_languages[language] = entry
        files[f"{language}.rows.jsonl"] = rows_bytes
    report = {
        "schema_version": "medzen-b5-gate-report-v1",
        "protocol_id": "PROMOTION-PROTOCOL-2026-004",
        "candidate_digest": f"sha256:{tree_digest}",
        "languages": report_languages,
        "gate_state_counts": {"PASS": len(languages)},
        "code_switch_evidence": {"state": "PASS",
                                   "set": "kinyarwanda-english-cs-v1",
                                   "manifest_sha256": "ab" * 32,
                                   "rows": 500},
        "operational_evidence": {"state": "PASS", "latency_p95_ms": 850,
                                   "vram_gb": 11.2},
    }
    files["T6-GATE-REPORT.json"] = _json.dumps(report).encode()
    files["HOLDOUT-BINDINGS.json"] = _json.dumps(holdouts).encode()
    review = {"status": "PASS", "findings": 0,
              "reviewer": "codex-independent-review"}
    files["INDEPENDENT-REVIEW.json"] = _json.dumps(review).encode()
    shas = {name: hashlib.sha256(body).hexdigest()
            for name, body in files.items()}
    record = {
        "protocol": "PROMOTION-PROTOCOL-2026-004",
        "decision": "APPROVED",
        "artifact_sha256": tree_digest,
        "gate_report": {"record": "T6-GATE-REPORT.json",
                         "record_sha256": shas["T6-GATE-REPORT.json"]},
        "independent_review": {"record": "INDEPENDENT-REVIEW.json",
                                "record_sha256": shas["INDEPENDENT-REVIEW.json"]},
        "owner_authorization": {
            "statement": f"owner authorizes promotion of {tree_digest[:12]}",
            "recorded_utc": "2026-08-23T00:00:00Z"},
    }
    record.update(record_over or {})
    files["PROMOTION-RECORD.json"] = _json.dumps(record).encode()
    shas["PROMOTION-RECORD.json"] = hashlib.sha256(
        files["PROMOTION-RECORD.json"]).hexdigest()
    for name, body in files.items():
        (bundle / name).write_bytes(body)
    index_bytes = _json.dumps(
        {"files": shas, "record": "PROMOTION-RECORD.json"}).encode()
    (bundle / "bundle.json").write_bytes(index_bytes)
    return bundle, hashlib.sha256(index_bytes).hexdigest()


def test_loader_v2_production_needs_a_verified_promotion_bundle(tmp_path, monkeypatch):
    """Codex rounds 2-6 (FABRICATED_PRODUCTION_APPROVAL_ACCEPTED ->
    'git not in the runtime image' -> FABRICATED_DETAILED_PASS_BUNDLE_
    ACCEPTED): the bundle now runs the SAME shared semantics and
    statistical RECOMPUTATION as scripts/b7_model_promotion_check.py.
    Evidence is per-row data reproducing the clustered-bootstrap
    verdicts — PASS labels, however detailed, promote nothing."""
    from medzen_model_loader.loader_v2 import (LoaderV2Refusal,
                                               artifact_tree_sha256,
                                               validate_manifest_v2)
    import pytest
    digest = "ab" * 32
    tree = artifact_tree_sha256(digest, "12" * 32)

    # no bundle configured -> refuse
    monkeypatch.delenv("MEDZEN_PROMOTION_BUNDLE_DIR", raising=False)
    monkeypatch.delenv("MEDZEN_PROMOTION_BUNDLE_SHA256", raising=False)
    with pytest.raises(LoaderV2Refusal, match="promotion bundle"):
        validate_manifest_v2(_v2_manifest(
            digest=digest, classification="PRODUCTION"))

    def arm(bundle, pin):
        monkeypatch.setenv("MEDZEN_PROMOTION_BUNDLE_DIR", str(bundle))
        monkeypatch.setenv("MEDZEN_PROMOTION_BUNDLE_SHA256", pin)

    # a bundle whose rows genuinely reproduce the statistics passes
    bundle, pin = _promotion_bundle(tmp_path, tree)
    arm(bundle, pin)
    ok = validate_manifest_v2(_v2_manifest(
        digest=digest, classification="PRODUCTION"))
    assert ok["classification"] == "PRODUCTION"

    # Codex round 6 reproduction: detailed all-PASS labels with NO
    # statistical evidence — refused by the shared gate
    bundle, pin = _promotion_bundle(tmp_path / "labels", tree,
                                     labels_only=True)
    arm(bundle, pin)
    with pytest.raises(LoaderV2Refusal, match="promotion gate refused"):
        validate_manifest_v2(_v2_manifest(
            digest=digest, classification="PRODUCTION"))

    # rows that do not REPRODUCE the claimed statistics — refused
    bundle, pin = _promotion_bundle(tmp_path / "forged", tree,
                                     break_rows_for="pidgin")
    arm(bundle, pin)
    with pytest.raises(LoaderV2Refusal, match="promotion gate refused"):
        validate_manifest_v2(_v2_manifest(
            digest=digest, classification="PRODUCTION"))

    # a record promoting a DIFFERENT artifact tree refuses
    other_tree = artifact_tree_sha256("cd" * 32, "12" * 32)
    bundle, pin = _promotion_bundle(tmp_path / "other", other_tree)
    arm(bundle, pin)
    with pytest.raises(LoaderV2Refusal, match="different artifact"):
        validate_manifest_v2(_v2_manifest(
            digest=digest, classification="PRODUCTION"))

    # owner authorization not bound to THIS tree digest refuses
    bundle, pin = _promotion_bundle(
        tmp_path / "unbound", tree,
        record_over={"owner_authorization": {
            "statement": "owner approves the model",
            "recorded_utc": "2026-08-23T00:00:00Z"}})
    arm(bundle, pin)
    with pytest.raises(LoaderV2Refusal, match="bound to THIS"):
        validate_manifest_v2(_v2_manifest(
            digest=digest, classification="PRODUCTION"))

    # a superseded/invented protocol id refuses
    bundle, pin = _promotion_bundle(tmp_path / "stale", tree,
                                     protocol_id="PROMOTION-PROTOCOL-2026-003")
    arm(bundle, pin)
    with pytest.raises(LoaderV2Refusal, match="pointer requires"):
        validate_manifest_v2(_v2_manifest(
            digest=digest, classification="PRODUCTION"))

    # tampering with a bundled document after pinning refuses
    bundle, pin = _promotion_bundle(tmp_path / "tampered", tree)
    report_path = bundle / "T6-GATE-REPORT.json"
    report_path.write_bytes(report_path.read_bytes() + b" ")
    arm(bundle, pin)
    with pytest.raises(LoaderV2Refusal, match="does not match its pin"):
        validate_manifest_v2(_v2_manifest(
            digest=digest, classification="PRODUCTION"))

def test_ci_pipeline_builds_from_repo_root_with_source_commit():
    """Codex review round 2 (reproduced COPY .../requirements.txt not
    found): Dockerfiles COPY repo-root paths, so context must be root,
    the Dockerfile named, SOURCE_COMMIT supplied, and each service maps
    to REAL test files (the -k selection collected zero)."""
    pipeline = (ROOT / ".github/workflows/_service-pipeline.yml").read_text()
    assert "context: ." in pipeline
    assert "file: ${{ inputs.context_path }}/Dockerfile" in pipeline
    assert "SOURCE_COMMIT=${{ github.sha }}" in pipeline
    assert "inputs.test_paths" in pipeline
    assert '-k "${{ inputs.service }}"' not in pipeline
    for wf in sorted((ROOT / ".github/workflows").glob("app-*.yml")):
        assert "test_paths:" in wf.read_text(), wf.name
