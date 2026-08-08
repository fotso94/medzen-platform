from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_b6a_003c_b_proof as base
from scripts.run_b6a_003c_d_proof import transcription_receipt_payload


def test_transcription_receipt_hashes_text_instead_of_persisting_it():
    response = {
        "request_id": base.REQUEST_ID,
        "classification": "PLATFORM_PROOF_ONLY",
        "production_approved": False,
        "model_versions": {
            "asr": "v0",
            "registry_snapshot": f"b6a-non-serving:{base.MANIFEST_SHA}",
            "llm": None,
            "rag": None,
            "tts": None,
        },
        "transcript": {
            "verbatim": "secret synthetic transcript",
            "normalized": "secret synthetic transcript",
            "normalization_version": "b6a-unicode-nfc-whitespace-v1",
        },
    }
    receipt = transcription_receipt_payload(
        response,
        {"ready": True},
        {"metadata": {"uid": "pod-uid"}},
    )
    assert "secret synthetic transcript" not in str(receipt)
    assert receipt["transcript_text_persisted"] is False
    assert len(receipt["transcript_verbatim_sha256"]) == 64


def test_transcription_receipt_is_persisted_before_memory_sampler_starts():
    text = (ROOT / "scripts/run_b6a_003c_d_proof.py").read_text()
    durable = text.index('"transcription",\n            "PASS"')
    sampler_call = text.index("memory = measure_gpu_memory", durable)
    assert durable < sampler_call
    assert "INCOMPLETE_MEASUREMENT" in text
    assert '"transcription_voided": False' in text
    assert "loaded_v0_model_plus_second_synthetic_inference" in text
