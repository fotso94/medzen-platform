"""ONE generation configuration, used by training, smoke tests and evaluation.

The failed run set `forced_decoder_ids = None` and `suppress_tokens = []` on
`model.config` while generation reads `generation_config` -- so training-time
intent and generation-time behaviour lived in two different objects and neither
knew about the other. Not confirmed as a cause, but it made the run impossible
to reason about, and every later attempt to measure what the model did ran into
some version of the same split.

So there is exactly one definition here, it is frozen, and every consumer
imports it. A caller that needs different settings must change this file, which
changes the code hash, which changes the run record.
"""
from __future__ import annotations

from types import MappingProxyType

# Whisper's four-token decoder prompt: <|startoftranscript|><|lang|>
# <|transcribe|><|notimestamps|>. Resolved by name at runtime, never hardcoded
# beyond these two anchors, which are stable across whisper checkpoints.
SOT_TOKEN = "<|startoftranscript|>"
EOT_TOKEN = "<|endoftext|>"
TASK = "transcribe"
PREDICT_TIMESTAMPS = False

# max_new_tokens EXCLUDES the decoder prompt. It is deliberately below
# max_target_positions (448) so that reaching it is unambiguous evidence of
# non-termination rather than of hitting a model-architecture wall.
MAX_NEW_TOKENS = 440

# Greedy and deterministic. Sampling would make two arms differ for reasons
# that have nothing to do with the adapter under test.
GENERATION = MappingProxyType({
    "max_new_tokens": MAX_NEW_TOKENS,
    "num_beams": 1,
    "do_sample": False,
    # Whisper's default tensor return slices the decoder prompt AND EOS off
    # (WhisperGenerationMixin._postprocess_outputs returns
    # seek_outputs[:, start_idx:]). Without these two flags, termination cannot
    # be measured at all -- which is how a runaway model was scored as merely
    # bad. force_unique_generate_call is only valid for short-form audio; see
    # require_short_form().
    "return_dict_in_generate": True,
    "force_unique_generate_call": True,
})

SEGMENT_LIMIT_S = 30.0          # Whisper's single-segment boundary


def generation_kwargs(lang_token: str) -> dict:
    """The complete kwargs for one decode. Language and task always forced."""
    return dict(GENERATION, language=lang_token, task=TASK)


def config_fingerprint() -> str:
    """A short hash of the frozen configuration, logged with every run.

    Two runs quoting the same fingerprint decoded the same way. Two runs that
    differ here are not comparable, whatever else they share.
    """
    import hashlib
    import json
    payload = {"generation": dict(GENERATION), "task": TASK,
               "predict_timestamps": PREDICT_TIMESTAMPS,
               "segment_limit_s": SEGMENT_LIMIT_S}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()


def expected_prompt(processor, lang_token: str) -> list[int]:
    """The exact decoder prompt this configuration must produce.

    Built from the tokenizer's own prefix machinery -- the same call the
    trainer's collator uses -- so evaluator and trainer cannot disagree about
    where a transcript begins.
    """
    tk = processor.tokenizer
    tk.set_prefix_tokens(language=lang_token, task=TASK,
                         predict_timestamps=PREDICT_TIMESTAMPS)
    return list(tk.prefix_tokens)


def require_short_form(rows: list[dict]) -> float:
    """force_unique_generate_call is only meaningful below the segment limit.

    Above 30 s Whisper chunks internally and returns per-segment results, so a
    forced single call would describe one segment as if it were the whole clip.
    Durations come from manifest metadata; no audio is inspected.
    """
    longest = max(r["duration_s"] for r in rows)
    if longest >= SEGMENT_LIMIT_S:
        over = sum(1 for r in rows if r["duration_s"] >= SEGMENT_LIMIT_S)
        raise SystemExit(
            f"REFUSING: {over} clip(s) reach or exceed Whisper's "
            f"{SEGMENT_LIMIT_S:.0f}s segment boundary (longest {longest:.2f}s). "
            "force_unique_generate_call would describe one segment as the whole "
            "clip. Segment-aware accounting is required and is not implemented.")
    return longest


def extract_sequence(out) -> list[int]:
    """Pull the single sequence from a structured generate() result."""
    seqs = getattr(out, "sequences", None)
    if seqs is None:
        raise SystemExit(
            f"REFUSING: generate() returned {type(out).__name__} with no "
            "`sequences`. This requires return_dict_in_generate=True; a bare "
            "tensor has the decoder prompt and EOS sliced off, so termination "
            "cannot be measured.")
    if len(seqs) != 1:
        raise SystemExit(f"REFUSING: expected exactly 1 sequence, got {len(seqs)}")
    return seqs[0].tolist()


def split_prompt(ids: list[int], prompt: list[int]) -> int:
    """Assert the sequence starts with the expected prompt; return its length.

    Everything after it is generated output, INCLUDING any control token the
    model chose to emit -- treating a stray control token as prompt is how a
    runaway decode gets scored as a short one.
    """
    if ids[:len(prompt)] != prompt:
        raise SystemExit(
            f"REFUSING: sequence begins {ids[:len(prompt)]} but the pinned "
            f"configuration requires the prompt {prompt}. The decode did not "
            "run under the configuration this evaluation claims.")
    return len(prompt)


def account(ids: list[int], prompt: list[int], eot_id: int) -> dict:
    """The one token-accounting rule, shared by smoke tests and evaluation."""
    n_prompt = split_prompt(ids, prompt)
    n_total = len(ids)
    n_gen = n_total - n_prompt
    eos_pos = ids.index(eot_id, n_prompt) if eot_id in ids[n_prompt:] else None
    eos = eos_pos is not None
    cap = (not eos) and n_gen >= MAX_NEW_TOKENS
    return {
        "prompt_tokens": n_prompt,
        "generated_tokens": n_gen,
        "total_tokens": n_total,
        "eos_emitted": eos,
        "eos_position": eos_pos,
        "hit_length_cap": cap,
        "stop_reason": "eos" if eos else "max_new_tokens" if cap else "other",
    }
