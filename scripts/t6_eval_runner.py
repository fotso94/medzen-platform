#!/usr/bin/env python3
"""T6 gate evaluation runner — runs INSIDE the pinned eval-runtime image.

Evaluates the wave-1 trained model (campaign r2 merged checkpoint, staged
at the asset card's own path) on the frozen 9-language suite rows, through
the IDENTICAL inference path that produced the baselines
(medzen_asr_eval.backends.MetaBackend -> omnilingual ASRInferencePipeline,
batch_size 1, unconditioned) and the IDENTICAL scorer
(medzen_asr_eval.metrics.error_counts, pooled per language).

Inputs (mounted):
  /inputs/t6-selection-*.json   frozen row chunks (hash-pinned upstream)
  /inputs/audio/<sha256>.wav    prestaged eval audio, named by checksum
  /models/...                   merged model at the card path + tokenizer
Output:
  /outputs/t6-results.json      pooled per-language metrics + row receipts
                                (hypothesis text NEVER persisted — sha only)

Fail-closed: every audio file is checksum-verified before decode; any
missing row, checksum mismatch or backend refusal aborts the whole run.
"""
from __future__ import annotations

import glob
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/repo/services/asr-eval-runtime")

from medzen_asr_eval.backends import MetaBackend  # noqa: E402
from medzen_asr_eval.metrics import error_counts  # noqa: E402


def main() -> int:
    chunks = sorted(glob.glob("/inputs/t6-selection-*.json"))
    if not chunks:
        raise SystemExit("no selection chunks mounted")
    backend = MetaBackend("medzen_omniASR_CTC_1B_v2")
    per_language: dict[str, list[dict]] = defaultdict(list)
    receipts = []
    total = 0
    for chunk_path in chunks:
        selection = json.loads(Path(chunk_path).read_bytes())
        for row in selection["rows"]:
            sha = row["audio_checksum_sha256"]
            audio = Path(f"/inputs/audio/{sha}.wav")
            if not audio.is_file():
                raise SystemExit(f"missing prestaged audio {sha}")
            if hashlib.sha256(audio.read_bytes()).hexdigest() != sha:
                raise SystemExit(f"checksum mismatch for {sha}")
            started = time.monotonic()
            transcript = backend.transcribe(audio, None)   # unconditioned
            latency = time.monotonic() - started
            errors = error_counts(row["reference"], transcript.text)
            per_language[row["language"]].append(errors)
            receipts.append({
                "audio_sha256": sha,
                "language": row["language"],
                "hypothesis_sha256": hashlib.sha256(
                    transcript.text.encode()).hexdigest(),
                "errors": errors,
                "latency_seconds": round(latency, 4),
            })
            total += 1
            if total % 200 == 0:
                print(f"progress {total}", flush=True)
    results = {"record": "T6-GATE-MEASUREMENTS",
               "candidate": "omniASR_CTC_1B_v2_medzen_r2",
               "mode": "unconditioned",
               "rows": total,
               "per_language": {}}
    for language, items in sorted(per_language.items()):
        word_errors = sum(i["word_errors"] for i in items)
        reference_words = sum(i["reference_words"] for i in items)
        character_errors = sum(i["character_errors"] for i in items)
        reference_characters = sum(i["reference_characters"] for i in items)
        results["per_language"][language] = {
            "rows": len(items),
            "wer": round(word_errors / reference_words, 6),
            "cer": round(character_errors / reference_characters, 6),
            "word_errors": word_errors,
            "reference_words": reference_words,
            "character_errors": character_errors,
            "reference_characters": reference_characters,
        }
    Path("/outputs/t6-results.json").write_text(
        json.dumps(results, indent=1, sort_keys=True) + "\n")
    Path("/outputs/t6-row-receipts.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True) for r in receipts) + "\n")
    print(json.dumps({k: v["wer"] for k, v in results["per_language"].items()},
                     sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
