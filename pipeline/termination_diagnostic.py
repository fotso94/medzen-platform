"""Aggregate-only termination diagnostics for the retained B4 adapter.

This module deliberately has no writer for transcripts, token sequences, row
identifiers, speakers, sessions, or audio.  Each row is reduced immediately to
numeric accumulators.  The resulting record can explain a termination failure
without turning private validation material into a new artifact.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter
from typing import Iterable

from pipeline import orchestrate
from pipeline.generation import (EOT_TOKEN, account, expected_prompt,
                                 extract_sequence, generation_kwargs)
from pipeline.label_length import decoder_start_id
from pipeline.languages import LANG_TOKEN


def _summary(values: Iterable[float], digits: int = 6) -> dict:
    values = [float(v) for v in values]
    if not values or any(not math.isfinite(v) for v in values):
        raise SystemExit("REFUSING: diagnostic metric is empty or non-finite")
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), digits),
        "median": round(statistics.median(values), digits),
        "min": round(min(values), digits),
        "max": round(max(values), digits),
    }


def repeated_ngram_rate(ids: list[int], n: int) -> float:
    """Fraction of emitted n-grams that repeat an earlier n-gram."""
    if n < 1:
        raise ValueError("n must be positive")
    grams = [tuple(ids[i:i + n]) for i in range(len(ids) - n + 1)]
    if not grams:
        return 0.0
    return (len(grams) - len(set(grams))) / len(grams)


def teacher_forced_row(model, processor, row: dict, audio, sample_rate: int,
                       language: str, device: str) -> dict:
    """Reduce one private row to numeric likelihood/terminal measurements."""
    import torch
    from pipeline.train_asr import collate

    lang_token = LANG_TOKEN[language]
    prompt = expected_prompt(processor, lang_token)
    eot = processor.tokenizer.convert_tokens_to_ids(EOT_TOKEN)
    labels_raw = processor.tokenizer(row["text_normalized"]).input_ids
    features = processor.feature_extractor(
        audio, sampling_rate=sample_rate,
        return_tensors="pt").input_features[0]
    batch = collate(
        processor, decoder_start_id(processor.tokenizer, model.config))([{
            "input_features": features, "labels": labels_raw,
        }])
    batch = {name: value.to(device) for name, value in batch.items()}
    if model.dtype != batch["input_features"].dtype:
        batch["input_features"] = batch["input_features"].to(model.dtype)

    labels = batch["labels"][0]
    valid = labels.ne(-100)
    target = labels[valid]
    # collate removes only SOT; language, task and no-timestamps remain.
    expected_control = torch.tensor(prompt[1:], device=target.device)
    if (target.numel() < 4
            or not torch.equal(target[:3], expected_control)
            or int(target[-1]) != eot
            or int(target.eq(eot).sum()) != 1):
        raise SystemExit(
            "REFUSING: teacher-forced target does not have the exact three "
            "post-SOT control labels followed by one terminal EOS")

    with torch.no_grad():
        logits = model(**batch).logits[0][valid].float()
        log_probs = torch.log_softmax(logits, dim=-1)
        token_nll = -log_probs.gather(1, target[:, None]).squeeze(1)
    content = token_nll[3:-1]
    if content.numel() == 0:
        raise SystemExit("REFUSING: diagnostic row has no content targets")
    terminal = logits[-1]
    eos_logit = terminal[eot]
    eos_log_prob = terminal[eot] - torch.logsumexp(terminal, dim=0)
    return {
        "target_tokens": int(target.numel()),
        "content_tokens": int(content.numel()),
        "total_nll_sum": float(token_nll.sum()),
        "content_nll_sum": float(content.sum()),
        "eos_nll": float(token_nll[-1]),
        "eos_probability": float(eos_log_prob.exp()),
        "eos_rank": int((terminal > eos_logit).sum()) + 1,
    }


def generated_row(model, processor, audio, sample_rate: int,
                  language: str, device: str) -> dict:
    """Reduce one generated sequence immediately; never return its token IDs."""
    import torch

    lang_token = LANG_TOKEN[language]
    prompt = expected_prompt(processor, lang_token)
    eot = processor.tokenizer.convert_tokens_to_ids(EOT_TOKEN)
    features = processor.feature_extractor(
        audio, sampling_rate=sample_rate,
        return_tensors="pt").input_features.to(device)
    if model.dtype != features.dtype:
        features = features.to(model.dtype)
    with torch.no_grad():
        out = model.generate(features, **generation_kwargs(lang_token))
    ids = extract_sequence(out)
    measured = account(ids, prompt, eot)
    generated = ids[len(prompt):]
    content = generated[:generated.index(eot)] if eot in generated else generated
    specials = set(processor.tokenizer.all_special_ids)
    unexpected_controls = sum(1 for token in content if token in specials)
    unique_ratio = len(set(content)) / len(content) if content else 1.0
    return {
        "generated_tokens": measured["generated_tokens"],
        "eos_emitted": measured["eos_emitted"],
        "hit_length_cap": measured["hit_length_cap"],
        "unique_token_ratio": unique_ratio,
        "repeated_bigram_rate": repeated_ngram_rate(content, 2),
        "repeated_trigram_rate": repeated_ngram_rate(content, 3),
        "unexpected_control_tokens": unexpected_controls,
    }


def diagnose_model(runtime, model, arm: str) -> dict:
    """Aggregate all nine frozen languages for one model arm."""
    per_language = {}
    all_rows: list[dict] = []
    for language in orchestrate.VALIDATION_LANGUAGES:
        rows, audios = runtime._loaded[language]
        reduced = []
        for row, (audio, sample_rate) in zip(rows, audios):
            teacher = teacher_forced_row(
                model, runtime.processor, row, audio, sample_rate,
                language, runtime.device)
            generated = generated_row(
                model, runtime.processor, audio, sample_rate,
                language, runtime.device)
            reduced.append({**teacher, **generated})
        per_language[language] = aggregate_rows(reduced)
        all_rows.extend(reduced)
    return {
        "arm": arm,
        "rows": len(all_rows),
        "per_language": per_language,
        "all_languages_weighted": aggregate_rows(all_rows),
    }


def aggregate_rows(rows: list[dict]) -> dict:
    if not rows:
        raise SystemExit("REFUSING: cannot aggregate zero diagnostic rows")
    total_targets = sum(r["target_tokens"] for r in rows)
    total_content = sum(r["content_tokens"] for r in rows)
    if not total_targets or not total_content:
        raise SystemExit("REFUSING: diagnostic target counts are zero")
    stop = Counter(
        "eos" if r["eos_emitted"] else
        "max_new_tokens" if r["hit_length_cap"] else "other"
        for r in rows)
    return {
        "rows": len(rows),
        "teacher_forced": {
            "target_tokens": total_targets,
            "content_tokens": total_content,
            "total_nll_per_token": round(
                sum(r["total_nll_sum"] for r in rows) / total_targets, 6),
            "content_nll_per_token": round(
                sum(r["content_nll_sum"] for r in rows) / total_content, 6),
            "eos_nll": _summary(r["eos_nll"] for r in rows),
            "eos_probability": _summary(
                (r["eos_probability"] for r in rows), 9),
            "eos_rank": _summary((r["eos_rank"] for r in rows), 2),
        },
        "generation": {
            "generated_tokens": _summary(
                (r["generated_tokens"] for r in rows), 2),
            "eos_rate": round(
                sum(bool(r["eos_emitted"]) for r in rows) / len(rows), 6),
            "cap_hit_rate": round(
                sum(bool(r["hit_length_cap"]) for r in rows) / len(rows), 6),
            "stop_reasons": dict(sorted(stop.items())),
            "unique_token_ratio": _summary(
                r["unique_token_ratio"] for r in rows),
            "repeated_bigram_rate": _summary(
                r["repeated_bigram_rate"] for r in rows),
            "repeated_trigram_rate": _summary(
                r["repeated_trigram_rate"] for r in rows),
            "unexpected_control_tokens": {
                "total": sum(r["unexpected_control_tokens"] for r in rows),
                "rows_with_any": sum(
                    r["unexpected_control_tokens"] > 0 for r in rows),
            },
        },
    }


def prompt_contract_sha256(runtime) -> str:
    """Bind prompts without persisting even their control-token sequence."""
    payload = []
    for language in orchestrate.VALIDATION_LANGUAGES:
        payload.append((language, expected_prompt(
            runtime.processor, LANG_TOKEN[language])))
    return hashlib.sha256(repr(payload).encode()).hexdigest()
