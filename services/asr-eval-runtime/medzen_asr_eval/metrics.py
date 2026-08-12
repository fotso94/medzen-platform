"""Deterministic quality, latency and resource aggregation for pilot receipts."""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

from .harness import EvaluationRefusal


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        raise EvaluationRefusal("score text must be a string")
    folded = unicodedata.normalize("NFC", value).casefold()
    chars = [char if char.isalnum() or char.isspace() or char == "'" else " " for char in folded]
    return " ".join("".join(chars).split())


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row_index, ref in enumerate(reference, 1):
        current = [row_index]
        for column_index, hyp in enumerate(hypothesis, 1):
            current.append(min(
                current[-1] + 1,
                previous[column_index] + 1,
                previous[column_index - 1] + (ref != hyp),
            ))
        previous = current
    return previous[-1]


def error_counts(reference: str, hypothesis: str) -> dict[str, int]:
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    words_ref, words_hyp = ref.split(), hyp.split()
    chars_ref = list(ref.replace(" ", ""))
    chars_hyp = list(hyp.replace(" ", ""))
    return {
        "word_errors": edit_distance(words_ref, words_hyp),
        "reference_words": len(words_ref),
        "character_errors": edit_distance(chars_ref, chars_hyp),
        "reference_characters": len(chars_ref),
    }


def _ratio(errors: int, units: int) -> float | None:
    return None if units == 0 else round(errors / units, 6)


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return round(ordered[index], 6)


def aggregate(rows: Iterable[dict[str, Any]], memory_samples_mib: list[float]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "PASS_ROW_INFERENCE"]
    if not completed:
        raise EvaluationRefusal("no completed pilot rows to aggregate")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    languages: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    sources: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in completed:
        groups[(row["candidate"], row["mode"])].append(row)
        languages[(row["candidate"], row["mode"], row["language"])].append(row)
        sources[(row["candidate"], row["mode"], row["source_id"])].append(row)

    def score(items: list[dict[str, Any]]) -> dict[str, Any]:
        word_errors = sum(item["errors"]["word_errors"] for item in items)
        reference_words = sum(item["errors"]["reference_words"] for item in items)
        character_errors = sum(item["errors"]["character_errors"] for item in items)
        reference_characters = sum(item["errors"]["reference_characters"] for item in items)
        latency = [float(item["latency_seconds"]) for item in items]
        rtf = [float(item["rtf"]) for item in items]
        return {
            "rows": len(items),
            "wer": _ratio(word_errors, reference_words),
            "cer": _ratio(character_errors, reference_characters),
            "word_errors": word_errors,
            "reference_words": reference_words,
            "character_errors": character_errors,
            "reference_characters": reference_characters,
            "eos_failures": sum(bool(item.get("eos_failure")) for item in items),
            "cap_hits": sum(bool(item.get("cap_hit")) for item in items),
            "latency_median_seconds": round(statistics.median(latency), 6),
            "latency_p95_seconds": percentile(latency, 0.95),
            "rtf_median": round(statistics.median(rtf), 6),
            "rtf_p95": percentile(rtf, 0.95),
        }

    group_values = {f"{candidate}|{mode}": score(items) for (candidate, mode), items in sorted(groups.items())}
    per_language = {f"{candidate}|{mode}|{language}": score(items) for (candidate, mode, language), items in sorted(languages.items())}
    per_source = {f"{candidate}|{mode}|{source}": score(items) for (candidate, mode, source), items in sorted(sources.items())}
    for key, value in group_values.items():
        candidate, mode = key.split("|")
        wers = [entry["wer"] for language_key, entry in per_language.items() if language_key.startswith(f"{candidate}|{mode}|") and entry["wer"] is not None]
        value["language_macro_wer"] = None if not wers else round(sum(wers) / len(wers), 6)
    numeric_memory = [float(value) for value in memory_samples_mib if math.isfinite(float(value)) and float(value) >= 0]
    return {
        "status": "PASS_AGGREGATE" if numeric_memory else "INCOMPLETE_MEASUREMENT",
        "completed_rows": len(completed),
        "groups": group_values,
        "per_language": per_language,
        "per_source": per_source,
        "gpu_memory": {
            "unit": "MiB",
            "sample_count": len(numeric_memory),
            "baseline": None if not numeric_memory else round(numeric_memory[0], 3),
            "peak": None if not numeric_memory else round(max(numeric_memory), 3),
        },
    }
