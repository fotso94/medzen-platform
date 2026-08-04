from __future__ import annotations

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LOADER_ROOT = ROOT / "services/model-loader"
sys.path.insert(0, str(LOADER_ROOT))

from medzen_model_loader.loader import (  # noqa: E402
    LoaderRefusal,
    load_artifact,
    validate_manifest,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fixture():
    data = {
        "config.json": b"{}",
        "model.bin": b"ct2-model",
        "preprocessor_config.json": b"{}",
        "tokenizer.json": b"{}",
    }
    files = {
        name: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        for name, body in sorted(data.items())
    }
    tree = hashlib.sha256(canonical(files)).hexdigest()
    prefix = f"s3://medzen-speech/b6a/asr/v0/{tree}/"
    manifest = {
        "schema_version": 1,
        "classification": "PLATFORM_PROOF_ONLY",
        "serving_label": "v0",
        "artifact": {
            "s3_prefix": prefix,
            "tree_sha256": tree,
            "files": files,
            "model_id": "openai/whisper-large-v3",
            "base_model_revision": "06f233fe06e710322aca913c1bc4249a0d71fce1",
            "tokenizer_revision": "06f233fe06e710322aca913c1bc4249a0d71fce1",
            "processor_revision": "06f233fe06e710322aca913c1bc4249a0d71fce1",
            "precision": "CTranslate2_float16",
            "fine_tuned": False,
            "adapter_sha256": None,
        },
        "decode_configuration": {
            "purpose": "B6A_PLATFORM_TEST_ONLY_NOT_PROMOTION_GRADE",
            "task": "transcribe",
            "beam_size": 1,
            "best_of": 1,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "word_timestamps": False,
        },
        "quality_disclosure": {
            "production_approved": False,
            "quality_gate_outcome": "FAIL",
            "absolute_wer_max": 0.2,
            "zero_shot_base_wer": {
                "lingala": 0.9207,
                "luganda": 1.0659,
                "oromo": 1.1749,
            },
        },
        "provenance": {
            "git_commit": "a" * 40,
            "container_image_digest": "sha256:" + "b" * 64,
            "converter": "ct2-transformers-converter@4.8.1",
            "converted_at_utc": "2026-08-04T00:00:00Z",
        },
    }
    manifest_uri = prefix + "MANIFEST.json"
    raw = canonical(manifest)
    objects = {
        manifest_uri.removeprefix("s3://medzen-speech/"): raw,
        **{
            prefix.removeprefix("s3://medzen-speech/") + name: body
            for name, body in data.items()
        },
    }
    return manifest, manifest_uri, raw, objects


class S3:
    def __init__(self, objects):
        self.objects = objects
        self.requests = []

    def get_object(self, Bucket, Key):
        assert Bucket == "medzen-speech"
        self.requests.append(Key)
        return {"Body": BytesIO(self.objects[Key])}


def test_loader_verifies_tree_objects_smoke_and_writes_ready_marker(tmp_path):
    manifest, uri, raw, objects = fixture()
    smoke_paths = []

    def smoke(path):
        smoke_paths.append(path)
        assert (path / "model.bin").read_bytes() == b"ct2-model"
        return {"passed": True, "device": "test", "compute_type": "test"}

    marker = load_artifact(
        S3(objects), uri, hashlib.sha256(raw).hexdigest(), tmp_path, smoke)
    assert marker["ready"] is True
    assert marker["production_approved"] is False
    assert marker["quality_gate_outcome"] == "FAIL"
    assert marker["artifact_tree_sha256"] == manifest["artifact"]["tree_sha256"]
    assert smoke_paths[0].name == ".loading"
    assert not (tmp_path / ".loading").exists()
    assert (tmp_path / ".medzen-ready.json").exists()
    assert (tmp_path / "model.bin").read_bytes() == b"ct2-model"


def test_loader_refuses_manifest_hash_mismatch_before_artifact_download(tmp_path):
    _, uri, _, objects = fixture()
    s3 = S3(objects)
    with pytest.raises(LoaderRefusal, match="manifest SHA-256 mismatch"):
        load_artifact(s3, uri, "0" * 64, tmp_path, lambda _: {"passed": True})
    assert s3.requests == [uri.removeprefix("s3://medzen-speech/")]
    assert not (tmp_path / ".medzen-ready.json").exists()


def test_loader_refuses_object_mismatch_and_removes_partial_staging(tmp_path):
    _, uri, raw, objects = fixture()
    objects = dict(objects)
    object_key = next(key for key in objects if key.endswith("model.bin"))
    objects[object_key] = b"tampered"
    with pytest.raises(LoaderRefusal, match="hash/size mismatch"):
        load_artifact(S3(objects), uri, hashlib.sha256(raw).hexdigest(),
                      tmp_path, lambda _: {"passed": True})
    assert list(tmp_path.iterdir()) == []


def test_loader_refuses_finetuned_or_production_approved_artifact():
    manifest, uri, _, _ = fixture()
    manifest["artifact"]["fine_tuned"] = True
    with pytest.raises(LoaderRefusal, match="fine-tuned"):
        validate_manifest(manifest, uri)
    manifest, uri, _, _ = fixture()
    manifest["quality_disclosure"]["production_approved"] = True
    with pytest.raises(LoaderRefusal, match="quality failure disclosure"):
        validate_manifest(manifest, uri)


def test_loader_refuses_approved_or_non_content_addressed_paths():
    manifest, uri, _, _ = fixture()
    with pytest.raises(LoaderRefusal, match="non-approved B6A path"):
        validate_manifest(manifest, uri.replace("/b6a/asr/v0/", "/approved/asr/v0/"))
    wrong_prefix = "s3://medzen-speech/b6a/asr/v0/" + "f" * 64 + "/"
    manifest["artifact"]["s3_prefix"] = wrong_prefix
    with pytest.raises(LoaderRefusal, match="content-addressed"):
        validate_manifest(manifest, wrong_prefix + "MANIFEST.json")


def test_loader_refuses_path_traversal_even_when_tree_is_recomputed():
    manifest, uri, _, _ = fixture()
    files = manifest["artifact"]["files"]
    files["../escape"] = dict(files["config.json"])
    tree = hashlib.sha256(canonical(files)).hexdigest()
    manifest["artifact"]["tree_sha256"] = tree
    manifest["artifact"]["s3_prefix"] = (
        f"s3://medzen-speech/b6a/asr/v0/{tree}/")
    uri = manifest["artifact"]["s3_prefix"] + "MANIFEST.json"
    with pytest.raises(LoaderRefusal, match="unsafe artifact path"):
        validate_manifest(manifest, uri)


def test_model_manifest_schema_is_valid_json_schema():
    import jsonschema

    schema = json.loads((
        ROOT / "schemas/b6a-model-manifest.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    manifest, _, _, _ = fixture()
    jsonschema.Draft202012Validator(schema).validate(manifest)
