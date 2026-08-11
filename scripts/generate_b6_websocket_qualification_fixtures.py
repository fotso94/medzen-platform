#!/usr/bin/env python3
"""Generate exact-window synthetic fixtures for local WebSocket qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.b6_6_proof_audio_binding import (
    PROOF_AUDIO_PATH,
    PROOF_AUDIO_SHA256,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platform/testdata/registry-ssm/source/b6-local-v1.json"
PROOF_AUDIO_RECORD = ROOT / "platform/testdata/b6a-003c-b-synthetic.json"
REGISTRY_OUTPUT = (
    ROOT / "platform/testdata/registry-ssm/b6-window-websocket-v1.json"
)
ASR_BINDING_OUTPUT = (
    ROOT / "platform/testdata/orchestrator/b6-window-asr-fixture.json"
)
PLACEHOLDER = "$B6_ORCHESTRATOR_AUDIO_SHA256"
CLASSIFICATION = "B6_3_LOCAL_SYNTHETIC_ONLY"


def canonical(value: Any, *, newline: bool = False) -> bytes:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return encoded + (b"\n" if newline else b"")


def products() -> dict[Path, bytes]:
    proof = json.loads(PROOF_AUDIO_RECORD.read_bytes())
    wav = proof["wav"]
    if (
        proof.get("classification") != "SYNTHETIC_PLATFORM_TEST_ONLY"
        or proof.get("contains_patient_data") is not False
        or proof.get("contains_clinical_content") is not False
        or wav.get("path") != str(PROOF_AUDIO_PATH.relative_to(ROOT))
        or wav.get("sha256") != PROOF_AUDIO_SHA256
        or hashlib.sha256(PROOF_AUDIO_PATH.read_bytes()).hexdigest()
        != PROOF_AUDIO_SHA256
    ):
        raise SystemExit("B6 proof audio binding differs")
    raw_source = SOURCE.read_text()
    if raw_source.count(PLACEHOLDER) != 1:
        raise SystemExit("local registry source placeholder differs")
    source = json.loads(raw_source.replace(PLACEHOLDER, PROOF_AUDIO_SHA256))
    source_sha256 = hashlib.sha256(canonical(source)).hexdigest()
    root = f"/medzen/registry/test/b6/{source_sha256}"
    values = {
        "index": canonical(source["index"]).decode("utf-8"),
        **{
            f"routes/{alias}": canonical(route).decode("utf-8")
            for alias, route in sorted(source["routes"].items())
        },
    }
    manifest = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "snapshot_sha256": source_sha256,
        "snapshot_material_sha256": source_sha256,
        "parameter_value_sha256": {
            relative: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for relative, value in sorted(values.items())
        },
    }
    parameters = [{
        "Name": f"{root}/_manifest",
        "Type": "SecureString",
        "Value": canonical(manifest).decode("utf-8"),
        "Version": 1,
    }]
    parameters.extend({
        "Name": f"{root}/{relative}",
        "Type": "SecureString",
        "Value": value,
        "Version": 1,
    } for relative, value in sorted(values.items()))
    phrase = proof.get("phrase")
    if not isinstance(phrase, str) or not phrase:
        raise SystemExit("B6 proof audio phrase is absent")
    asr_binding = {
        "schema_version": 1,
        "classification": "B6_3_LOCAL_SYNTHETIC_NON_SPEECH",
        "audio_sha256": PROOF_AUDIO_SHA256,
        "audio_format": {
            "container": "wav",
            "encoding": "pcm_s16le",
            "sample_rate_hz": 16000,
            "channels": 1,
        },
        "duration_seconds": wav["duration_seconds"],
        "language": "en",
        "transcript": {
            "verbatim": phrase,
            "normalized": phrase,
            "normalization_version": "b6-window-local-synthetic-v1",
        },
        "model_version": "v0-local-synthetic-asr",
    }
    return {
        REGISTRY_OUTPUT: canonical(
            {"schema_version": 1, "parameters": parameters}, newline=True
        ),
        ASR_BINDING_OUTPUT: canonical(asr_binding, newline=True),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = [
        str(path.relative_to(ROOT))
        for path, expected in products().items()
        if not path.exists() or path.read_bytes() != expected
    ]
    if args.check and stale:
        raise SystemExit(
            "generated WebSocket qualification fixtures are stale: "
            + ", ".join(stale)
        )
    if not args.check:
        raise SystemExit(
            "refusing implicit writes; add reviewed generated outputs with apply_patch"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
