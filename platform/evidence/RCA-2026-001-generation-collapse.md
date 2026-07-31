# RCA-2026-001 — B4 candidate generation collapse

**Run** `23868bab2d8448759fc1b9ed26156952` · **Adapter** `17e1b7381b7b3fdb…`
**Status** rejected, candidates-only · **Decision** `EVAL-2026-001-b4-candidate-failed.json`
**Revision** 2 (2026-07-31) — corrected after independent review; see *Corrections*.

> **Confidence.** The duplicated start-of-transcript token is a **confirmed
> trainer defect**, sufficient on its own to invalidate the run, and it is the
> **strongest available explanation** for the collapse. It is not yet *proven*
> to be the sole cause: that requires a corrected controlled A/B run, which has
> not been performed.

## What happened

Training completed cleanly. Loss fell 22.53 → 4.00 over 600 steps, the descent
gate passed, every provenance, licence, exclusion and checkpoint check held,
and the run recorded itself accurately. The resulting adapter then scored
**722% WER** against a base of **51%** on the frozen 44-clip Pidgin set.

The tell is not the error rate. It is the **output length**: a median of 52.5
tokens against the base's 14, and a maximum of 444 against 71.

An earlier revision of this report read 444 as `444 generated + 4 prefix = 448 =
max_target_positions` and treated the exact match as proof. **That arithmetic is
withdrawn.** `generate()` returns a sequence that *includes* the decoder prompt,
while `max_new_tokens` *excludes* it, so 444 is equally consistent with 4 prompt
+ 440 generated. The external evaluator's token accounting is unknown, and a
coincidence that survives only under one of two readings is not evidence.

What the lengths *do* support: the candidate generates far past the base and at
least one row reached a generation limit. Whether `<|endoftext|>` was emitted —
the actual question — was not measured. `scripts/evaluate_candidate.py` now
records `prompt_tokens`, `generated_tokens`, `total_tokens`, `eos_emitted`, the
EOS position and the stopping reason, and defines a cap hit operationally:
**EOS absent AND generated tokens reaching `max_new_tokens`**. The termination
claim stands or falls on reproducing that.

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
2. **Every target sits one position later than at inference.**

**Correction.** An earlier revision added that cross-entropy over "all four
prefix positions" trained the model to emit control tokens "as if they were
content". That is wrong. `<|lang|>`, `<|transcribe|>` and `<|notimestamps|>`
**are** legitimate training targets in standard Whisper fine-tuning — the
official collator strips only `decoder_start_token_id` and keeps the rest. The
sole erroneous target is the **retained SOT**.

At decode time the prefix is supplied by the generation config, so the decoder
state never matches anything seen in training. The reported output lengths are
consistent with the model having no reliable path to `<|endoftext|>` — but that
is a hypothesis until EOS emission is measured directly, which the hardened
evaluator now does.

## The signal that was already there and was misread

The opening loss of **22.53** (and **23.26** on the earlier 14-language run) was
flagged twice as "unexplained" and left as a curiosity.

A cross-entropy of 22.5 means near-zero probability mass on the targets, which
is what a one-position shift would produce. What it is *not* is a calibrated
finding: no correctly aligned baseline loss has been measured for this
14-language mixture, so "22.5 is too high" rests on general expectation rather
than on this corpus. Measuring that distribution is a prerequisite for turning
loss magnitude into any kind of gate.

The process point stands regardless of calibration:

**an anomalous number appeared at step 0 of every run and was recorded as an
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

## Fix (applied 2026-07-31)

1. The collator takes `model.config.decoder_start_token_id`, **asserts every row
   begins with it**, and strips exactly that one token. Language, task,
   no-timestamps and the trailing `<|endoftext|>` all survive as targets. A row
   that does not match refuses the batch rather than being guessed at.
2. `pipeline/label_length.py` used the identical `bos_token_id` comparison, so
   `effective` silently equalled `raw` for **every row ever measured**. It now
   resolves the token by name and cross-checks it against the model config.
3. The saved processor is reloaded clean from the pinned base. `Dataset.
   __getitem__` calls `set_prefix_tokens()` per row, so the training copy ends
   pinned to whichever language was sampled last — run 23868bab shipped one
   stuck at `language="yo"`.

**Rejected alternative.** Masking the prefix with `-100` was proposed and is
**not** used: without separately constructing `decoder_input_ids`, the shift
would propagate masked values into the decoder inputs in place of the
language/task prompt.

### Loss thresholds — withdrawn as gates

An earlier revision proposed "correct loss starts at 1–3" and "abort above 6".
Those are **plausible heuristics, not calibrated figures**, and were never
measured for this 14-language mixture. They must not become hard gates. Until a
correctly aligned per-language base-loss distribution is measured:

| Gate | Status |
|---|---|
| Label-alignment assertions | **hard gate** |
| Finite loss / gradient checks | **hard gate** |
| Loss magnitude bounds | **warning only** |
| Validation WER, CER, EOS rate, cap-hit rate | training stop criteria |

## Corrections in revision 2

| Claim (rev 1) | Status |
|---|---|
| `444 + 4 = 448` proves the cap was hit | **withdrawn** — prompt/generated accounting was unverified |
| All four prefix tokens were wrongly trained as content | **corrected** — only the retained SOT is erroneous |
| Loss "should" start at 1–3; abort above 6 | **demoted** to uncalibrated warning |
| Duplicated SOT is *the* cause | **qualified** — confirmed defect and strongest explanation; sole-cause proof needs a corrected A/B run |
| `-100` prefix masking as an option | **rejected** — breaks decoder inputs |

Also found by review and now fixed: the same `bos_token_id` mistake in
`label_length.py`, and a saved processor left pinned to `language="yo"`.

## What must not be concluded

- Not that the data is bad. The corpus, exclusions and provenance were correct.
- Not that the deferred 20 rows caused this. They were excluded and remain
  unreviewed.
- Not that LoRA or the base model is unsuitable. Neither has been fairly tested.
- Not that the frozen Pidgin set is disqualified entirely. It has **zero exact
  train/eval audio or text overlap**, but shares both speakers and sessions with
  training, so it is **diagnostic-only**: usable to study when the failed
  checkpoints degenerated, never to select a checkpoint or support promotion.

The candidate is rejected. The training-run record and MLflow run are unchanged
and remain accurate: the run did complete, and the loss did fall.
