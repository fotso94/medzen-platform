from __future__ import annotations

import hashlib
import json
from pathlib import Path
import wave


ROOT = Path(__file__).resolve().parents[1]
WAV = ROOT / "platform/testdata/b6a-003c-b-synthetic.wav"
BINDING = ROOT / "platform/testdata/b6a-003c-b-synthetic.json"
GENERATOR = ROOT / "scripts/generate_b6a_003c_b_synthetic_audio.sh"


def test_synthetic_audio_matches_exact_no_phi_binding():
    binding = json.loads(BINDING.read_text())
    assert binding["classification"] == "SYNTHETIC_PLATFORM_TEST_ONLY"
    assert binding["contains_patient_data"] is False
    assert binding["contains_clinical_content"] is False
    assert binding["derived_from_training_evaluation_customer_or_production_audio"] is False
    assert hashlib.sha256(WAV.read_bytes()).hexdigest() == binding["wav"]["sha256"]
    assert WAV.stat().st_size == binding["wav"]["bytes"]


def test_synthetic_audio_is_exact_mono_16khz_pcm_wav():
    binding = json.loads(BINDING.read_text())
    with wave.open(str(WAV), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 16000
        assert audio.getcomptype() == "NONE"
        duration = audio.getnframes() / audio.getframerate()
    assert duration == binding["wav"]["duration_seconds"]


def test_generator_has_fixed_phrase_toolchain_and_fail_closed_hash():
    text = GENERATOR.read_text()
    binding = json.loads(BINDING.read_text())
    assert binding["phrase"] in text
    assert binding["wav"]["sha256"] in text
    assert "say -v Samantha -r 145" in text
    assert "-map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le" in text
    assert "rm -f -- \"$output\"" in text
