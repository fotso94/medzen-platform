# RCA-2026-001 — B4 candidate generation collapse

**Run** `23868bab2d8448759fc1b9ed26156952` · **Adapter** `17e1b7381b7b3fdb…`
**Status** rejected, candidates-only · **Decision** `EVAL-2026-001-b4-candidate-failed.json`

## What happened

Training completed cleanly. Loss fell 22.53 → 4.00 over 600 steps, the descent
gate passed, every provenance, licence, exclusion and checkpoint check held,
and the run recorded itself accurately. The resulting adapter then scored
**722% WER** against a base of **51%** on the frozen 44-clip Pidgin set.

The tell is not the error rate. It is the **maximum output length: 444 tokens**.

```
444 generated + 4 prefix tokens = 448 = max_target_positions
```

The model ran to the hard decoder cap without emitting `<|endoftext|>`. This is
a **termination failure**, not a quality failure — a merely bad model produces
wrong words, not 444 of them.

## Root cause

`pipeline/train_asr.py`, in `collate()`:

```python
# the decoder re-adds BOS; drop it if the tokenizer already put one there
if (labels[:, 0] == processor.tokenizer.bos_token_id).all().item():
    labels = labels[:, 1:]
```

Measured against the pinned tokenizer (`openai/whisper-large-v3@06f233fe`):

| | value |
|---|---|
| `labels[0]` | **50258** `<|startoftranscript|>` |
| `tokenizer.bos_token_id` | **50257** `<|endoftext|>` |
| condition | **False, always** |

In Whisper, `bos_token` *is* `<|endoftext|>`. The comparison can never be true,
so **the strip is dead code** and the labels keep the full four-token prefix:

```
labels = [<|startoftranscript|>, <|en|>, <|transcribe|>, <|notimestamps|>, …text…, <|endoftext|>]
```

HuggingFace then derives the decoder inputs itself:

```
decoder_input_ids = shift_tokens_right(labels, decoder_start_token_id=50258)
                  = [SOT, SOT, <|en|>, <|transcribe|>, <|notimestamps|>, …text…]
```

Two things follow, and both are fatal:

1. **`<|startoftranscript|>` is duplicated.** The model is trained to *predict*
   SOT at position 0 given SOT — a state that never occurs at inference.
2. **Every real text token sits one position later than at inference**, and
   cross-entropy is computed across all four prefix positions, training the
   model to emit control tokens as if they were content.

At decode time the prefix is supplied by the generation config, so the decoder
state never matches anything seen in training. The model has no reliable path
to `<|endoftext|>` and generates until the cap.

## The signal that was already there and was misread

The opening loss of **22.53** (and **23.26** on the earlier 14-language run) was
flagged twice as "unexplained" and left as a curiosity. A correctly aligned
Whisper LoRA fine-tune opens near **1–3**. A cross-entropy of 22.5 means
near-zero probability mass on the targets — exactly what a one-position shift
plus special-token targets produces.

**The defect announced itself at step 0 of every run and was recorded as an
open question rather than investigated.** That is the process failure worth
carrying forward, more than the code defect itself.

## Why every existing gate passed

| Guard | Checked | Missed |
|---|---|---|
| Provenance / licence / adoption | corpus identity | — |
| Exclusion policy | which rows | — |
| Label **length** | ≤ 448 tokens | label **content** |
| Descent gate | loss falls | *what* loss is falling |
| Checkpoint upload | bytes landed | whether the model works |

The descent gate is the sharpest illustration: the model learned the misaligned
objective *well*. Descent against a wrong target is indistinguishable from
descent against a right one if you only look at the number.

## Secondary findings — suspects, not causes

- **`model.config.forced_decoder_ids = None` / `suppress_tokens = []`** are set
  on `model.config`, but generation reads `generation_config`, where the pinned
  config carries `forced_decoder_ids [[1, null], [2, 50360]]`,
  `begin_suppress_tokens [220, 50257]` and 88 `suppress_tokens`. Training-time
  intent and generation-time behaviour live in two different places.
  **Not confirmed** as contributing here; a correctness hazard found in passing.
- **LoRA lr 1e-3 at rank 32** is high for Whisper (typical 1e-4–5e-4).
  **Suspected aggravating factor only.** It would amplify a bad objective; it
  does not explain termination failure and must not be treated as the cause.
- **Pidgin → proxy language token.** Pidgin has no Whisper language token.
  **Unquantified** and unassessable until alignment is fixed.

Fixing the learning rate without fixing the labels would produce a better-trained
wrong model.

## Proposed fix (not yet applied)

1. Strip the prefix by **identity, not by `bos_token_id`** — compare against
   `convert_tokens_to_ids("<|startoftranscript|>")`, or mask the prefix
   positions with `-100` so no loss is computed on them.
2. **Assert alignment in code**: decoded `decoder_input_ids[:2]` must not be two
   SOTs, and labels must not begin with SOT after collation. A run that cannot
   prove its own alignment should refuse to start.
3. **Pin generation config in one place** and record it in the run record.
4. Add a **step-0 loss sanity bound** — an opening cross-entropy above ~6 on a
   pretrained model is evidence of a broken objective, not of a hard task.
5. Reconsider lr after alignment is proven, not before.

## What must not be concluded

- Not that the data is bad. The corpus, exclusions and provenance were correct.
- Not that the deferred 20 rows caused this. They were excluded and remain
  unreviewed.
- Not that LoRA or the base model is unsuitable. Neither has been fairly tested.

The candidate is rejected. The training-run record and MLflow run are unchanged
and remain accurate: the run did complete, and the loss did fall.
