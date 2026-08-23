"""B6v2 round 5 (Codex): language routing + the complete loader init path.

Codex reproduced `'en' is not served by this artifact`: the registry
speaks wire codes, the manifest speaks aliases, the pipeline speaks
omnilingual ids, and nothing translated. And the v2 loader was a
validator no container ever invoked — no download, no tokenizer
integrity, no marker writer on the entrypoint.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
for service in ("model-loader", "asr-runtime"):
    sys.path.insert(0, str(ROOT / "services" / service))

from medzen_model_loader.languages_v2 import (  # noqa: E402
    SERVING_LANGUAGES_V1,
    canonical_language_ids,
    marker_language_ids,
)
from medzen_model_loader.loader_v2 import (  # noqa: E402
    CHECKPOINT_FILENAME,
    TOKENIZER_FILENAME,
    LoaderV2Refusal,
    run_b6v2_init,
    write_ready_marker_v2,
)
from medzen_asr_runtime.omniasr_backend import (  # noqa: E402
    load_v2_ready_marker,
    resolve_omni_language,
)
from medzen_asr_runtime.backend import BackendRefusal  # noqa: E402


# ------------------------------------------------ canonical language map
def test_canonical_map_agrees_with_both_committed_sources():
    """The embedded constant is pinned by the registry's iso codes AND
    the conditioning table the T6 gate actually used — drift refuses."""
    conditioning = json.loads(
        (ROOT / "services/asr-eval-runtime/assets/"
         "language-conditioning-v1.json").read_text())
    entries = conditioning.get("languages", conditioning)
    for alias, (iso, omni) in SERVING_LANGUAGES_V1.items():
        registry = yaml.safe_load(
            (ROOT / f"registry/languages/{alias}.yaml").read_text())
        assert registry["iso_code"] == iso, alias
        assert entries[alias]["meta_llm"] == omni, alias


def test_every_wire_code_and_alias_resolves_through_the_real_path():
    """All seven languages, by BOTH the code the orchestrator sends and
    the manifest alias, through the exact pre-inference lookup."""
    table = marker_language_ids()
    for alias, (iso, omni) in SERVING_LANGUAGES_V1.items():
        assert resolve_omni_language(table, alias) == omni
        assert resolve_omni_language(table, iso) == omni
    assert resolve_omni_language(table, None) is None
    with pytest.raises(BackendRefusal, match="not served"):
        resolve_omni_language(table, "zz")


def test_loader_destinations_equal_the_fairseq2_asset_card():
    """The loader stages at the EXACT paths the asset card deserializes."""
    cards = list(yaml.safe_load_all(
        (ROOT / "services/asr-eval-runtime/assets/models.yaml").read_text()))
    by_name = {card["name"]: card for card in cards if card}
    ctc = by_name["medzen_omniASR_CTC_1B_v2"]
    tokenizer = by_name["medzen_omniASR_tokenizer_written_v2"]
    assert Path(ctc["checkpoint"]).name == CHECKPOINT_FILENAME
    assert Path(tokenizer["tokenizer"]).name == TOKENIZER_FILENAME


# ---------------------------------------------------- loader init path
CHECKPOINT = b"FAKE-FAIRSEQ2-CHECKPOINT-BYTES"
TOKENIZER = b"FAKE-SENTENCEPIECE-TOKENIZER-BYTES"


def _serving_manifest():
    digest = hashlib.sha256(CHECKPOINT).hexdigest()
    return {
        "schema_version": 2,
        "classification": "NONPROD_REAL_PROVIDER_V2",
        "model_family": "omniasr_ctc_1b",
        "artifact": {"format": "fairseq2_pt", "sha256": digest,
                      "s3_filename": "model.pt"},
        "tokenizer": {"sha256": hashlib.sha256(TOKENIZER).hexdigest(),
                       "s3_filename": "tokenizer.model"},
        "languages": sorted(SERVING_LANGUAGES_V1),
        "language_ids": canonical_language_ids(),
        "model_version": f"omniasr_ctc_1b:{digest[:12]}",
    }


class FakeS3Body:
    def __init__(self, payload: bytes):
        self._reader = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._reader.read(size)


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def get_object(self, *, Bucket, Key):
        return {"Body": FakeS3Body(self.objects[f"{Bucket}/{Key}"])}


def _armed_env(monkeypatch, tmp_path, manifest_bytes):
    destination = tmp_path / "models"
    destination.mkdir()
    monkeypatch.setenv("MEDZEN_B6V2_MANIFEST_URI",
                        "s3://medzen-speech/serving/b6v2/MANIFEST.json")
    monkeypatch.setenv("MEDZEN_B6V2_MANIFEST_SHA256",
                        hashlib.sha256(manifest_bytes).hexdigest())
    monkeypatch.setenv("MODEL_DESTINATION", str(destination))
    return destination


def test_b6v2_init_downloads_verifies_stages_and_writes_marker_last(
    monkeypatch, tmp_path,
):
    manifest_bytes = json.dumps(_serving_manifest()).encode()
    destination = _armed_env(monkeypatch, tmp_path, manifest_bytes)
    s3 = FakeS3({
        "medzen-speech/serving/b6v2/MANIFEST.json": manifest_bytes,
        "medzen-speech/serving/b6v2/model.pt": CHECKPOINT,
        "medzen-speech/serving/b6v2/tokenizer.model": TOKENIZER,
    })
    result = run_b6v2_init(s3)
    assert (destination / CHECKPOINT_FILENAME).read_bytes() == CHECKPOINT
    assert (destination / TOKENIZER_FILENAME).read_bytes() == TOKENIZER
    marker = json.loads((destination / ".medzen-ready-v2.json").read_bytes())
    assert marker["tokenizer_sha256"] == hashlib.sha256(TOKENIZER).hexdigest()
    # BOTH the alias and the wire code the orchestrator sends resolve
    assert marker["language_ids"]["kinyarwanda"] == "kin_Latn"
    assert marker["language_ids"]["kin"] == "kin_Latn"
    assert marker["language_ids"]["en"] == "eng_Latn"
    assert result["classification"] == "NONPROD_REAL_PROVIDER_V2"
    # the STAGED bytes re-verify through the backend's own marker gate
    loaded = load_v2_ready_marker(destination)
    assert loaded["model_version"] == marker["model_version"]


def test_b6v2_init_refuses_wrong_tokenizer_and_writes_no_marker(
    monkeypatch, tmp_path,
):
    manifest_bytes = json.dumps(_serving_manifest()).encode()
    destination = _armed_env(monkeypatch, tmp_path, manifest_bytes)
    s3 = FakeS3({
        "medzen-speech/serving/b6v2/MANIFEST.json": manifest_bytes,
        "medzen-speech/serving/b6v2/model.pt": CHECKPOINT,
        "medzen-speech/serving/b6v2/tokenizer.model": b"TAMPERED",
    })
    with pytest.raises(LoaderV2Refusal, match="tokenizer"):
        run_b6v2_init(s3)
    assert not (destination / ".medzen-ready-v2.json").exists(), (
        "a failed verification must never leave a marker")


def test_b6v2_init_refuses_a_manifest_that_misses_the_deployment_pin(
    monkeypatch, tmp_path,
):
    manifest_bytes = json.dumps(_serving_manifest()).encode()
    destination = _armed_env(monkeypatch, tmp_path, manifest_bytes)
    monkeypatch.setenv("MEDZEN_B6V2_MANIFEST_SHA256", "0" * 64)
    s3 = FakeS3({
        "medzen-speech/serving/b6v2/MANIFEST.json": manifest_bytes,
    })
    with pytest.raises(LoaderV2Refusal, match="deployment pin"):
        run_b6v2_init(s3)
    assert not any(destination.iterdir()), "nothing may stage on refusal"


def test_manifest_language_ids_must_equal_the_canonical_map():
    from medzen_model_loader.loader_v2 import validate_manifest_v2
    manifest = _serving_manifest()
    manifest["language_ids"] = dict(manifest["language_ids"], english="en_US")
    with pytest.raises(LoaderV2Refusal, match="canonical"):
        validate_manifest_v2(manifest)


def test_backend_refuses_staged_bytes_that_drift_from_the_marker(
    monkeypatch, tmp_path,
):
    manifest_bytes = json.dumps(_serving_manifest()).encode()
    destination = _armed_env(monkeypatch, tmp_path, manifest_bytes)
    s3 = FakeS3({
        "medzen-speech/serving/b6v2/MANIFEST.json": manifest_bytes,
        "medzen-speech/serving/b6v2/model.pt": CHECKPOINT,
        "medzen-speech/serving/b6v2/tokenizer.model": TOKENIZER,
    })
    run_b6v2_init(s3)
    (destination / CHECKPOINT_FILENAME).write_bytes(b"SWAPPED-AFTER-LOAD")
    with pytest.raises(BackendRefusal, match="staged checkpoint"):
        load_v2_ready_marker(destination)
