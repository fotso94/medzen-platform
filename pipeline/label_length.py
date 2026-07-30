"""One definition of "how long is this label", used everywhere.

The first full B4 run died at step 59 with "Labels' sequence length 536 cannot
exceed the maximum allowed length of 448". An ad-hoc audit run afterwards
reported 534 for the same row. Two numbers for one row means at least one of
them was not measuring what training measures, and a length check that does not
reproduce training is not a check.

The gap is the prefix. Training sets the language and task before tokenising:

    tokenizer.set_prefix_tokens(language=LANG_TOKEN[lang], task="transcribe")

which emits <|startoftranscript|><|xx|><|transcribe|><|notimestamps|>. The audit
called the tokenizer with no prefix set, getting the default (shorter) prefix --
hence two fewer tokens. The collator then strips a leading BOS from the batch,
so the length the model actually receives is one lower again.

So there are three numbers, and only the last one is the one the decoder limit
applies to:

    raw        tokens as tokenised, with the language/task prefix
    effective  after the collator removes a leading BOS -- what the model sees
    limit      model.config.max_target_positions

This module exists so the audit, the runtime guard and the tests cannot drift
apart: they all call the same function, and the tests call it against the real
tokenizer rather than asserting on source text.
"""
from __future__ import annotations

# Imported from the neutral module, NOT from the trainer: the trainer uses this
# checker, so importing the trainer here would close a cycle.
from pipeline.languages import LANG_TOKEN


def label_lengths(tokenizer, text: str, lang: str) -> tuple[int, int]:
    """Return (raw, effective) label token counts exactly as training computes.

    `lang` is the corpus language name (e.g. "amharic"), not the Whisper token;
    the mapping is applied here so callers cannot apply a different one.
    """
    tokenizer.set_prefix_tokens(language=LANG_TOKEN.get(lang, "en"),
                                task="transcribe")
    ids = tokenizer(text).input_ids
    raw = len(ids)
    bos = tokenizer.bos_token_id
    # The collator drops a leading BOS because the decoder re-adds it; the model
    # therefore receives one fewer token than the tokenizer produced.
    effective = raw - 1 if raw and bos is not None and ids[0] == bos else raw
    return raw, effective


def exceeds_limit(tokenizer, text: str, lang: str, limit: int) -> bool:
    """True when the model would reject this label."""
    return label_lengths(tokenizer, text, lang)[1] > limit
