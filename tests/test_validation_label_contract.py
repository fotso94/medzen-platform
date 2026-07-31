from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.audit_label_lengths import (
    BASE_REVISION,
    TOKENIZER_CACHE_FILES,
    pinned_tokenizer,
)
from scripts.audit_validation_label_contract import audit_language_rows


class FakeTokenizer:
    prefix_tokens = [1, 2, 3, 4]

    def __init__(self, encoded):
        self.encoded = encoded

    def set_prefix_tokens(self, language, task):
        assert language == "am" and task == "transcribe"

    def convert_tokens_to_ids(self, token):
        return {"<|startoftranscript|>": 1, "<|endoftext|>": 9}[token]

    def __call__(self, text):
        return SimpleNamespace(input_ids=self.encoded[text])


def test_validation_contract_counts_reference_generation_budget():
    tok = FakeTokenizer({
        "fits": [1, 2, 3, 4, 7, 8, 9],
        "too-long": [1, 2, 3, 4, 7, 8, 6, 5, 9],
    })
    rows = [
        {"text_normalized": "fits", "duration_s": 1.0},
        {"text_normalized": "too-long", "duration_s": 2.0},
    ]
    got = audit_language_rows(
        tok, "amharic", rows, model_limit=7, generation_cap=4)

    assert got["rows"] == 2
    assert got["wrong_prefix_rows"] == 0
    assert got["missing_eos_target_rows"] == 0
    assert got["rows_over_model_label_limit"] == 1
    assert got["rows_over_generation_cap"] == 1
    assert got["reference_generated_tokens_including_eos"]["max"] == 5


def test_validation_contract_reports_prefix_and_eos_defects():
    tok = FakeTokenizer({
        "wrong-prefix": [1, 99, 3, 4, 7, 9],
        "missing-eos": [1, 2, 3, 4, 7, 8],
    })
    rows = [
        {"text_normalized": "wrong-prefix", "duration_s": 1.0},
        {"text_normalized": "missing-eos", "duration_s": 1.0},
    ]
    got = audit_language_rows(tok, "amharic", rows)

    assert got["wrong_prefix_rows"] == 1
    assert got["missing_eos_target_rows"] == 1


def test_tokenizer_audit_never_downloads_model_weights(monkeypatch):
    payloads = {name: name.encode() for name in TOKENIZER_CACHE_FILES}
    payloads["model.safetensors"] = b"must-not-be-read"
    manifest = {
        "revision": BASE_REVISION,
        "files": {
            name: {"sha256": hashlib.sha256(body).hexdigest()}
            for name, body in payloads.items()
        },
    }

    class Body:
        def __init__(self, value):
            self.value = value

        def read(self):
            return self.value

    class S3:
        def __init__(self):
            self.keys = []

        def get_object(self, Bucket, Key):
            self.keys.append(Key)
            if Key.endswith("/MANIFEST.json"):
                return {"Body": Body(json.dumps(manifest).encode())}
            return {"Body": Body(payloads[Key.rsplit("/", 1)[-1]])}

    class FakeFast:
        @classmethod
        def from_pretrained(cls, path):
            return ("tokenizer", path)

    fake_transformers = SimpleNamespace(WhisperTokenizerFast=FakeFast)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    s3 = S3()
    tokenizer, got_manifest = pinned_tokenizer(s3)

    assert tokenizer[0] == "tokenizer"
    assert got_manifest == manifest
    assert not any(key.endswith("/model.safetensors") for key in s3.keys)
    assert len(s3.keys) == 1 + len(TOKENIZER_CACHE_FILES)
