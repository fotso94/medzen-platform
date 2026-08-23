#!/usr/bin/env python3
"""B6v2 provider qualification harness (Codex round 8: the ad-hoc script
DOCUMENTED caps but did not ENFORCE them — reruns silently re-called
Bedrock). This harness is idempotent and cap-enforced:

  python scripts/b6v2_qualification_harness.py bedrock --receipt-dir DIR
  python scripts/b6v2_qualification_harness.py fish    --receipt-dir DIR

Each leg runs INDEPENDENTLY. A per-leg ledger file records every
provider invocation BEFORE it happens; when the ledger already holds
max-calls entries the harness REFUSES to invoke the provider again —
reruns re-report the recorded outcome instead of spending. Synthetic
non-PHI content only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for service in ("llm-gateway", "speech-tts-gateway"):
    sys.path.insert(0, str(ROOT / f"services/{service}"))

QUERY = "When does the fictional training desk open?"
KINYARWANDA_TEXT = ("Uyu ni umurongo w'ikizamini wa MedZen. "
                     "Nta makuru y'umuntu arimo.")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CallLedger:
    """Counts provider invocations ACROSS runs; refuses past the cap."""

    def __init__(self, receipt_dir: Path, leg: str, max_calls: int):
        self.path = receipt_dir / f"{leg}.ledger.jsonl"
        self.max_calls = max_calls

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in
                self.path.read_text().splitlines() if line.strip()]

    def reserve(self) -> int:
        entries = self.entries()
        if len(entries) >= self.max_calls:
            raise SystemExit(
                f"REFUSED: {self.path.name} already records "
                f"{len(entries)}/{self.max_calls} provider calls — the "
                "cap is enforced, not documented. Delete the ledger ONLY "
                "with owner approval to re-qualify.")
        with open(self.path, "a") as f:
            f.write(json.dumps({"call": len(entries) + 1,
                                  "reserved_utc": _now()}) + "\n")
        return len(entries) + 1


def bedrock_leg(receipt_dir: Path, max_calls: int) -> dict:
    from medzen_llm_gateway.gateway import LLMGateway
    from medzen_llm_gateway.policy import PolicyStore
    from medzen_llm_gateway.provider import BedrockProvider
    from medzen_llm_gateway.shared_resilience import (CircuitBreaker,
                                                       load_config)

    ledger = CallLedger(receipt_dir, "bedrock", max_calls)
    call_number = ledger.reserve()
    model_id = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    config = load_config()
    breaker = CircuitBreaker(
        name="bedrock",
        failure_threshold=config["circuit_breakers"]["per_provider"]["bedrock"]["failure_threshold"],
        timeout_threshold=config["circuit_breakers"]["defaults"]["timeout_threshold"],
        window_s=config["circuit_breakers"]["defaults"]["window_s"],
        open_duration_s=config["circuit_breakers"]["per_provider"]["bedrock"]["open_duration_s"],
        half_open_max_calls=config["circuit_breakers"]["defaults"]["half_open_max_calls"],
    )
    gateway = LLMGateway(
        PolicyStore(ROOT / "registry/languages",
                    ROOT / "registry/llm-policies/v1.yaml"),
        BedrockProvider(model_id=model_id, region="eu-central-1"),
        breaker,
    )
    grounding = ("The fictional training desk opens Monday at 09:00 and "
                  "closes at 17:00. It is a synthetic test entity for the "
                  "MedZen platform qualification.")
    citation = {
        "rank": 1, "document_id": "synthetic-hours",
        "title": "Fictional training desk hours",
        "source_uri": "medzen://synthetic/training-desk",
        "section": "hours",
        "content_sha256": hashlib.sha256(b"synthetic").hexdigest(),
        "excerpt": grounding[:280], "grounding_text": grounding,
        "score": 4.0,
    }
    started = time.perf_counter()
    response = gateway.complete({
        "request_id": "77777777-7777-4777-8777-777777777777",
        "language": "english",
        "transcript": {"verbatim": QUERY, "normalized": QUERY.lower(),
                        "normalization_version": "b6v2-unicode-nfc-whitespace-v1"},
        "rag": {"query_id": "c" * 64,
                 "index_snapshot_sha256": "6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160",
                 "citations": [citation]},
        "model_versions": {"asr": "omniasr_ctc_1b:qualtest0000",
                            "registry_snapshot": "b6v2-qualification:harness",
                            "llm": None,
                            "rag": "sha256:6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160",
                            "tts": None},
    })
    return {
        "leg": "bedrock", "call": call_number, "status": "PASS",
        "model_version": response["model_versions"]["llm"],
        "grounding_sha256": response.get("grounding_sha256"),
        "cited": [c["document_id"] for c in response["reply"]["citations"]],
        "reply_sha256": hashlib.sha256(
            response["reply"]["text"].encode()).hexdigest(),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "utc": _now(),
    }


def fish_leg(receipt_dir: Path, max_calls: int) -> dict:
    from medzen_speech_tts_gateway.app import fish_breaker
    from medzen_speech_tts_gateway.gateway import TTSGateway
    from medzen_speech_tts_gateway.provider import RealFishProvider
    from medzen_speech_tts_gateway.s3_cache import S3AudioCache
    from medzen_speech_tts_gateway.voices import enforce_model, select_voice

    ledger = CallLedger(receipt_dir, "fish", max_calls)
    call_number = ledger.reserve()

    def governed(language):
        voice = select_voice(language)
        return voice.reference_id, enforce_model(voice, None)

    gateway = TTSGateway(
        provider=RealFishProvider(),
        breaker=fish_breaker(),
        voice_resolver=governed,
        cache=S3AudioCache(
            bucket="medzen-audio-cache", prefix="tts/",
            kms_key_arn=("arn:aws:kms:eu-central-1:558069890522:key/"
                          "d3c8d8fa-b188-476b-a32b-fb65bebb3bda")),
    )
    started = time.perf_counter()
    result = gateway.synthesize({
        "request_id": "88888888-8888-4888-8888-888888888888",
        "language": "kinyarwanda",
        "text": KINYARWANDA_TEXT,
        "model_versions": {"asr": None,
                             "registry_snapshot": "b6v2-qualification:harness",
                             "llm": None, "rag": None, "tts": None},
    })
    delivered = str(result.get("audio_url") or "").startswith("https://")
    return {
        "leg": "fish", "call": call_number,
        "status": "PASS" if result.get("tts_backend") == "fish" else "DEGRADED",
        "tts_backend": result.get("tts_backend"),
        "tts_model_version": result["model_versions"].get("tts"),
        "audio_sha256": result.get("audio_sha256"),
        "s3_delivery": delivered,
        "degradation_reason": result.get("degradation_reason"),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "utc": _now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("leg", choices=("bedrock", "fish"))
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=1)
    args = parser.parse_args()
    args.receipt_dir.mkdir(parents=True, exist_ok=True)
    outcome = (bedrock_leg if args.leg == "bedrock" else fish_leg)(
        args.receipt_dir, args.max_calls)
    results = args.receipt_dir / f"{args.leg}.results.jsonl"
    with open(results, "a") as f:
        f.write(json.dumps(outcome, sort_keys=True) + "\n")
    print(json.dumps(outcome, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
