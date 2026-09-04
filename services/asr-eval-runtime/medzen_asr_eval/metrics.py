"""Deterministic quality, latency and resource aggregation for pilot receipts.

TONE POLICY (v2, 2026-09-04)
---------------------------
Scoring text for these languages is not tone-neutral, so the normaliser is
NOT allowed to pick a tone policy silently. Every caller names one.

  TONE_SENSITIVE     combining marks are kept, attached to their base letter.
                     Tone is phonemic in the Grassfields/Bantu orthographies
                     in this eval surface, so this is the honest default for
                     any figure published as "WER" or "CER".
  TONE_INSENSITIVE   all combining marks are removed, consistently, from
                     precomposed and decomposed vowels alike. A legitimate
                     metric, but it must be LABELLED as tone-insensitive
                     wherever it is reported.
  LEGACY_V1          the pre-fix behaviour, kept only to reproduce scores
                     that were already published under it. DEPRECATED.

The v1 defect (see platform/evidence/CM-PILOT-SCORING-VERDICT-2026-001.json,
error_decomposition_in_domain): v1 mapped every character that is not
alphanumeric, whitespace or an apostrophe to a SPACE. A combining mark with
no precomposed NFC form survives NFC as category Mn, is not alphanumeric, and
so became a space. That SPLIT the word and DESTROYED the mark at once, while
precomposed vowels (a-acute, e-circumflex) passed through untouched. The
damage was therefore SELECTIVE: it landed on exactly the vowels these
orthographies need and Latin-1 lacks (schwa, open-o, barred-u, open-e).
Destroying a mark hands the model free credit for every tone error it makes,
so v1 WER and CER are OPTIMISTIC — the published figures are floors.

Deleting the marks is NOT the fix. It is the same information loss without
the word split. That is why TONE_INSENSITIVE is offered as a DECLARED
choice rather than as the repair.

This mirrors pipeline/normalizers.py, which has carried a per-row
`normalization_version` and an explicit tonal/generic split since B2.4, on
the same rule: changing a normaliser invalidates every score cached under
the old one, so bump the version and never edit one in place.
"""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

from .harness import EvaluationRefusal

TONE_SENSITIVE = "asr-eval-norm-v2-tone-sensitive"
TONE_INSENSITIVE = "asr-eval-norm-v2-tone-insensitive"
LEGACY_V1 = "asr-eval-norm-v1-mark-destroying"

#: Human-readable label for any report carrying figures from each policy.
POLICY_LABELS: dict[str, str] = {
    TONE_SENSITIVE: "tone-sensitive (combining marks scored)",
    TONE_INSENSITIVE: "TONE-INSENSITIVE (combining marks removed; not comparable with tone-sensitive WER/CER)",
    LEGACY_V1: "DEPRECATED v1 (combining marks destroyed and words split; figures are optimistic floors)",
}


def _fold(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _legacy_v1(value: str) -> str:
    """The pre-fix normaliser, byte-for-byte. Do not change it: it exists so
    an already-published score can still be reproduced and shown to be wrong."""
    folded = _fold(value)
    chars = [char if char.isalnum() or char.isspace() or char == "'" else " " for char in folded]
    return " ".join("".join(chars).split())


def _tone_sensitive(value: str) -> str:
    """Keep every combining mark attached to the base character it modifies.

    A mark is never emitted as a space — that is the v1 defect. A mark with no
    surviving base to attach to (leading mark, or a mark stranded behind
    dropped punctuation) is dropped rather than left as an orphan token."""
    out: list[str] = []
    has_base = False
    for char in _fold(value):
        if unicodedata.category(char) == "Mn":
            if has_base:
                out.append(char)
            continue
        if char.isalnum() or char == "'":
            out.append(char)
            has_base = True
        else:
            out.append(" ")
            has_base = False
    return " ".join("".join(out).split())


def _tone_insensitive(value: str) -> str:
    """Remove tone CONSISTENTLY — from precomposed vowels too, which v1 kept.

    Same tokenisation as TONE_SENSITIVE, so the two differ only in whether
    tone is scored."""
    decomposed = unicodedata.normalize("NFD", _tone_sensitive(value))
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(unicodedata.normalize("NFC", stripped).split())


_NORMALIZERS = {
    TONE_SENSITIVE: _tone_sensitive,
    TONE_INSENSITIVE: _tone_insensitive,
    LEGACY_V1: _legacy_v1,
}


def normalize_text(value: str, *, policy: str) -> str:
    """Normalise scoring text under an EXPLICITLY named tone policy.

    `policy` is keyword-only and required: a silent default is what made the
    canonical WER and CER tone-blind without any report saying so."""
    if not isinstance(value, str):
        raise EvaluationRefusal("score text must be a string")
    normalizer = _NORMALIZERS.get(policy)
    if normalizer is None:
        raise EvaluationRefusal(
            f"unknown normalization policy {policy!r}; name one of {sorted(_NORMALIZERS)}")
    return normalizer(value)


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


def _char_stream(text: str, policy: str) -> str:
    """The codepoint sequence CER is measured over, with spaces removed.

    Under the v2 policies this is NFD, so a character costs the same whether
    or not its accent happens to precompose in Unicode. Without it the metric
    is composition-dependent: "a-acute" is ONE codepoint (U+00E1) and
    "schwa-acute" is TWO (U+0259 U+0301), so dropping the tone costs a
    1-of-1 substitution on the first and a 1-of-2 deletion on the second —
    a different relative penalty for the same error, decided by which vowel
    Latin-1 happened to precompose. NFD puts both on two codepoints.

    LEGACY_V1 is deliberately excluded: it is frozen to reproduce scores
    already published under it, and normalising its character stream would
    silently change those numbers."""
    if policy == LEGACY_V1:
        return text.replace(" ", "")
    return unicodedata.normalize("NFD", text).replace(" ", "")


def error_counts(reference: str, hypothesis: str, *, policy: str) -> dict[str, Any]:
    """Edit counts under an explicitly named tone policy.

    The policy travels WITH the counts, per row, for the same reason
    pipeline/normalizers.py stores `normalization_version` per row: a count
    is meaningless without the normaliser that produced it, and two policies
    must never be pooled into one rate.

    Characters are counted as CODEPOINTS over the NFD stream (see
    _char_stream), so under TONE_SENSITIVE a combining mark is its own
    character and the cost of a tone error does not depend on whether its
    vowel precomposes. That is what makes `reference_characters` rise
    against v1 — v1 lost exactly one character per destroyed mark."""
    ref = normalize_text(reference, policy=policy)
    hyp = normalize_text(hypothesis, policy=policy)
    words_ref, words_hyp = ref.split(), hyp.split()
    chars_ref = list(_char_stream(ref, policy))
    chars_hyp = list(_char_stream(hyp, policy))
    return {
        "word_errors": edit_distance(words_ref, words_hyp),
        "reference_words": len(words_ref),
        "character_errors": edit_distance(chars_ref, chars_hyp),
        "reference_characters": len(chars_ref),
        "normalization_policy": policy,
    }


def _ratio(errors: int, units: int) -> float | None:
    return None if units == 0 else round(errors / units, 6)


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return round(ordered[index], 6)


def _declared_policy(completed: list[dict[str, Any]]) -> str:
    """Every pooled row must name the SAME tone policy.

    An undeclared policy is refused rather than defaulted: a WER whose
    normaliser is unknown is not a measurement, and pooling two policies into
    one rate silently averages a tone-sensitive and a tone-blind number."""
    policies = {str(row.get("errors", {}).get("normalization_policy") or "") for row in completed}
    if "" in policies:
        raise EvaluationRefusal(
            "rows carry no normalization_policy; re-score them under a named tone policy "
            f"({sorted(POLICY_LABELS)}) before aggregating")
    if len(policies) > 1:
        raise EvaluationRefusal(f"rows mix normalization policies {sorted(policies)}; refusing to pool them")
    policy = policies.pop()
    if policy not in POLICY_LABELS:
        raise EvaluationRefusal(f"rows name an unknown normalization policy {policy!r}")
    return policy


def aggregate(rows: Iterable[dict[str, Any]], memory_samples_mib: list[float]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "PASS_ROW_INFERENCE"]
    if not completed:
        raise EvaluationRefusal("no completed pilot rows to aggregate")
    policy = _declared_policy(completed)
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
        "normalization_policy": policy,
        "normalization_policy_label": POLICY_LABELS[policy],
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
