#!/usr/bin/env python3
"""Generate the deterministic B6.3 non-speech WAV and SSM-shaped registry."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platform/testdata/registry-ssm/source/b6-local-v1.json"
REGISTRY_OUTPUT = ROOT / "platform/testdata/registry-ssm/b6-local-v1.json"
AUDIO_OUTPUT = ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav"
ASR_BINDING_OUTPUT = ROOT / "platform/testdata/orchestrator/asr-fixture.json"
PLACEHOLDER = "$B6_ORCHESTRATOR_AUDIO_SHA256"
CLASSIFICATION = "B6_3_LOCAL_SYNTHETIC_ONLY"


def canonical(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def audio_fixture() -> bytes:
    sample_rate = 16_000
    frames = bytearray()
    for index in range(sample_rate):
        sample = int(1200 * math.sin(2 * math.pi * 440 * index / sample_rate))
        frames.extend(struct.pack("<h", sample))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(frames))
    return output.getvalue()


def products() -> dict[Path, bytes]:
    audio = audio_fixture()
    audio_sha = hashlib.sha256(audio).hexdigest()
    source = json.loads(SOURCE.read_bytes())
    raw_source = SOURCE.read_text()
    if raw_source.count(PLACEHOLDER) != 1:
        raise SystemExit("registry source must contain exactly one audio hash placeholder")
    source = json.loads(raw_source.replace(PLACEHOLDER, audio_sha))
    source_sha = hashlib.sha256(canonical(source)).hexdigest()
    root = f"/medzen/registry/test/b6/{source_sha}"
    index_value = canonical(source["index"]).decode("utf-8")
    route_values = {
        f"routes/{alias}": canonical(route).decode("utf-8")
        for alias, route in sorted(source["routes"].items())
    }
    values = {"index": index_value, **route_values}
    manifest = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "snapshot_sha256": source_sha,
        "snapshot_material_sha256": source_sha,
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
    fixture = {"schema_version": 1, "parameters": parameters}
    asr_binding = {
        "schema_version": 1,
        "classification": "B6_3_LOCAL_SYNTHETIC_NON_SPEECH",
        "audio_sha256": audio_sha,
        "audio_format": {
            "container": "wav",
            "encoding": "pcm_s16le",
            "sample_rate_hz": 16000,
            "channels": 1,
        },
        "duration_seconds": 1.0,
        "language": "en",
        "transcript": {
            "verbatim": "When does the fictional training desk open?",
            "normalized": "When does the fictional training desk open?",
            "normalization_version": "b6-local-synthetic-v1",
        },
        "model_version": "v0-local-synthetic-asr",
    }
    return {
        AUDIO_OUTPUT: audio,
        ASR_BINDING_OUTPUT: canonical(asr_binding, newline=True),
        REGISTRY_OUTPUT: canonical(fixture, newline=True),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = products()
    stale: list[str] = []
    for path, expected in outputs.items():
        if args.check:
            if not path.exists() or path.read_bytes() != expected:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if stale:
        raise SystemExit("generated B6.3 fixtures are stale: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
