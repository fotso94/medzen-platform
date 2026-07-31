"""Aggregate-only Amharic decode-strategy compatibility experiment.

The prior diagnostic proved that the frozen greedy contract collapses into
repetition on Amharic even for the untouched base.  This module compares three
predeclared decode strategies without training and immediately reduces every
private row to corpus metrics.  It has no writer for transcripts, token
sequences, checksums, row identifiers, speakers, sessions, or audio.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from types import MappingProxyType

from pipeline.generation import (EOT_TOKEN, account, extract_sequence,
                                 generation_kwargs)
from pipeline.termination_diagnostic import repeated_ngram_rate


STRATEGIES = MappingProxyType({
    "greedy_v1": MappingProxyType({}),
    "whisper_fallback_v1": MappingProxyType({
        "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        "compression_ratio_threshold": 1.35,
        "logprob_threshold": -1.0,
    }),
    "beam5_v1": MappingProxyType({
        "num_beams": 5,
        "do_sample": False,
        "early_stopping": True,
    }),
})

MAX_LANGUAGE_REGRESSION = 0.05
RETAINED_GREEDY_WER = 1.2383


def strategy_kwargs(name: str, lang_token: str) -> dict:
    if name not in STRATEGIES:
        raise SystemExit(f"REFUSING: unknown decode strategy {name!r}")
    out = generation_kwargs(lang_token)
    out.update(STRATEGIES[name])
    return out


def strategy_fingerprint() -> str:
    payload = {
        name: {key: list(value) if isinstance(value, tuple) else value
               for key, value in values.items()}
        for name, values in STRATEGIES.items()
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def row_seed(strategy: str, audio_checksum_sha256: str) -> int:
    """Pair base/candidate sampling without persisting row-derived seeds."""
    raw = hashlib.sha256(
        f"{strategy}/{audio_checksum_sha256}".encode()).digest()
    return int.from_bytes(raw[:4], "big") & 0x7FFF_FFFF


def _summary(values: list[float], digits: int = 6) -> dict:
    if not values:
        raise SystemExit("REFUSING: decode metric is empty")
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), digits),
        "median": round(statistics.median(values), digits),
        "min": round(min(values), digits),
        "max": round(max(values), digits),
    }


def score_strategy(model, processor, rows: list[dict], audios: list,
                   language: str, device: str, lang_token: str,
                   prompt: list[int], strategy: str) -> dict:
    """Score one model/strategy and return corpus aggregates only."""
    import torch
    from pipeline.normalizers import for_language
    from scripts.run_baseline import bootstrap_ci, wer_cer

    if len(rows) != len(audios) or not rows:
        raise SystemExit("REFUSING: decode rows/audio are empty or misaligned")
    kwargs = strategy_kwargs(strategy, lang_token)
    norm = for_language(language)
    eot = processor.tokenizer.convert_tokens_to_ids(EOT_TOKEN)
    refs: list[str] = []
    hyps: list[str] = []
    generated_tokens: list[float] = []
    latencies: list[float] = []
    unique_ratios: list[float] = []
    repeated_bigrams: list[float] = []
    repeated_trigrams: list[float] = []
    eos_rows = cap_rows = unexpected_controls = rows_with_controls = 0
    stop_reasons = {"eos": 0, "max_new_tokens": 0, "other": 0}
    specials = set(processor.tokenizer.all_special_ids)

    for rec, (audio, sample_rate) in zip(rows, audios):
        checksum = rec.get("audio_checksum_sha256")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise SystemExit("REFUSING: Amharic row has no checksum seed")
        seed = row_seed(strategy, checksum)
        torch.manual_seed(seed)
        if device == "cuda":
            torch.cuda.manual_seed_all(seed)
        features = processor.feature_extractor(
            audio, sampling_rate=sample_rate,
            return_tensors="pt").input_features.to(device)
        if model.dtype != features.dtype:
            features = features.to(model.dtype)
        started = time.perf_counter()
        with torch.no_grad():
            output = model.generate(features, **kwargs)
        latencies.append(time.perf_counter() - started)
        ids = extract_sequence(output)
        measured = account(ids, prompt, eot)
        generated = ids[len(prompt):]
        content = generated[:generated.index(eot)] \
            if eot in generated else generated
        text = processor.tokenizer.decode(ids, skip_special_tokens=True)
        refs.append(norm(rec["text_normalized"]))
        hyps.append(norm(text))

        eos_rows += int(measured["eos_emitted"])
        cap_rows += int(measured["hit_length_cap"])
        stop_reasons[measured["stop_reason"]] += 1
        generated_tokens.append(float(measured["generated_tokens"]))
        unique_ratios.append(
            len(set(content)) / len(content) if content else 1.0)
        repeated_bigrams.append(repeated_ngram_rate(content, 2))
        repeated_trigrams.append(repeated_ngram_rate(content, 3))
        controls = sum(token in specials for token in content)
        unexpected_controls += controls
        rows_with_controls += int(controls > 0)

    wer, cer = wer_cer(refs, hyps)
    low, high = bootstrap_ci(refs, hyps)
    serialised_kwargs = {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in kwargs.items()
    }
    return {
        "rows": len(rows),
        "strategy": strategy,
        "generation_flags": serialised_kwargs,
        "wer": round(wer, 4),
        "cer": round(cer, 4),
        "wer_ci95": [round(low, 4), round(high, 4)],
        "eos_rate": round(eos_rows / len(rows), 6),
        "cap_hit_rate": round(cap_rows / len(rows), 6),
        "stop_reasons": stop_reasons,
        "generated_tokens": _summary(generated_tokens, 2),
        "latency_s": _summary(latencies, 4),
        "unique_token_ratio": _summary(unique_ratios),
        "repeated_bigram_rate": _summary(repeated_bigrams),
        "repeated_trigram_rate": _summary(repeated_trigrams),
        "unexpected_control_tokens": {
            "total": unexpected_controls,
            "rows_with_any": rows_with_controls,
        },
        "normalization_version": norm.version,
    }


def select_strategy(results: dict) -> dict:
    """Apply the predeclared all-or-nothing compatibility rule."""
    if set(results) != set(STRATEGIES):
        raise SystemExit("REFUSING: decode result does not contain all strategies")
    viability = {}
    passing = []
    for strategy in STRATEGIES:
        arms = results[strategy]
        if set(arms) != {"base", "retained_1e_4"}:
            raise SystemExit(
                f"REFUSING: {strategy} does not contain both model arms")
        base = arms["base"]
        candidate = arms["retained_1e_4"]
        checks = {
            "both_arms_have_25_rows": (
                base.get("rows") == 25 and candidate.get("rows") == 25),
            "strategy_identity_matches": (
                base.get("strategy") == strategy
                and candidate.get("strategy") == strategy),
            "base_eos_rate_1": base.get("eos_rate") == 1.0,
            "base_cap_hit_rate_0": base.get("cap_hit_rate") == 0.0,
            "candidate_eos_rate_1": candidate.get("eos_rate") == 1.0,
            "candidate_cap_hit_rate_0": candidate.get("cap_hit_rate") == 0.0,
            "no_unexpected_controls": (
                (base.get("unexpected_control_tokens") or {}).get("total") == 0
                and (candidate.get("unexpected_control_tokens") or {}).get(
                    "total") == 0),
            "candidate_vs_base_wer": (
                float(candidate.get("wer"))
                <= float(base.get("wer")) + MAX_LANGUAGE_REGRESSION),
            "candidate_not_worse_than_retained_greedy": (
                float(candidate.get("wer")) <= RETAINED_GREEDY_WER),
        }
        passed = all(checks.values())
        viability[strategy] = {"passed": passed, "checks": checks}
        if passed:
            passing.append((
                float(candidate["wer"]),
                float(candidate["latency_s"]["median"]),
                strategy,
            ))
    passing.sort()
    if passing:
        best_wer = passing[0][0]
        tied = [row for row in passing if row[0] - best_wer <= 0.0001]
        tied.sort(key=lambda row: (row[1], row[2]))
        selected = tied[0][2]
    else:
        selected = None
    return {
        "selected_strategy": selected,
        "viability": viability,
        "selection_rule": (
            "lowest retained-adapter WER; ties within 0.0001 use median "
            "latency, then strategy name"),
        "training_authorised": False,
        "promotable": False,
    }
