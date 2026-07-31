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
hence two fewer tokens. The collator then strips the leading START-OF-TRANSCRIPT
token from the batch, so the model receives one fewer token than the tokenizer
produced.

CORRECTED 2026-07-31. This module previously identified that token by
`tokenizer.bos_token_id`. In Whisper `bos_token` is <|endoftext|> (50257), NOT
<|startoftranscript|> (50258), so the comparison was always false and
`effective` silently equalled `raw` for every row ever measured. The same
mistake in the collator is what invalidated run 23868bab. The token is now
resolved by identity and, when a model config is supplied, cross-checked
against `decoder_start_token_id` -- the definition HuggingFace itself uses.

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

SOT = "<|startoftranscript|>"


def decoder_start_id(tokenizer, model_config=None) -> int:
    """The token the collator strips and the decoder re-adds.

    Resolved by NAME, never via `bos_token_id`. When a model config is given,
    the two definitions must agree; a mismatch means the tokenizer and the model
    disagree about where a transcript begins, and nothing downstream of that is
    trustworthy.
    """
    tid = tokenizer.convert_tokens_to_ids(SOT)
    if tid is None or (isinstance(tid, int) and tid < 0):
        raise ValueError(f"tokenizer does not define {SOT}")
    if model_config is not None:
        want = getattr(model_config, "decoder_start_token_id", None)
        if want is not None and want != tid:
            raise ValueError(
                f"decoder_start_token_id mismatch: model says {want}, tokenizer "
                f"resolves {SOT} to {tid}")
    return tid


def label_lengths(tokenizer, text: str, lang: str,
                  model_config=None) -> tuple[int, int]:
    """Return (raw, effective) label token counts exactly as training computes.

    `lang` is the corpus language name (e.g. "amharic"), not the Whisper token;
    the mapping is applied here so callers cannot apply a different one.

    `effective` is the length AFTER the collator strips the leading
    start-of-transcript token -- the length the decoder limit applies to. Only
    that one token is removed: the language, task and no-timestamps tokens stay,
    because they are legitimate training targets in standard Whisper
    fine-tuning, not prefix decoration.
    """
    tokenizer.set_prefix_tokens(language=LANG_TOKEN.get(lang, "en"),
                                task="transcribe")
    ids = tokenizer(text).input_ids
    raw = len(ids)
    sot = decoder_start_id(tokenizer, model_config)
    effective = raw - 1 if raw and ids[0] == sot else raw
    return raw, effective


def exceeds_limit(tokenizer, text: str, lang: str, limit: int,
                  model_config=None) -> bool:
    """True when the model would reject this label."""
    return label_lengths(tokenizer, text, lang, model_config)[1] > limit
