# PLAN-2026-004 - B4 Amharic decode compatibility experiment

**Status: COMPLETED — NO VIABLE STRATEGY; TRAINING NOT AUTHORISED.**

This is a no-training, non-promotable experiment. It may identify the decode
contract that a future corrected B4 run must use, but it cannot select a model,
complete B4, register an artifact, or unblock B5.

## Evidence that requires this experiment

`DIAG-2026-001-amharic-termination.json` established that all 25 frozen
Amharic rows hit the 440-token cap under greedy decoding with the untouched
base. The retained 1e-4 adapter terminates 3/25 and improves teacher-forced
content NLL and EOS probability, but the typical failed row remains almost
entirely repeated n-grams. Other languages improve. This is therefore not
evidence that the adapter created a global collapse.

The Base v5 architecture makes decoding a registry-controlled, experiment-
chosen part of the ASR artifact. A decode experiment is therefore an
architecture requirement, not a relaxation of model quality gates.

## Frozen inputs

- the 25-row speaker- and session-disjoint Amharic set from VAL-2026-001;
- untouched `openai/whisper-large-v3` revision
  `06f233fe06e710322aca913c1bc4249a0d71fce1`;
- retained 1e-4 checkpoint-100 adapter tree
  `5e8ddd18291911c776974fd09cdb291f1bf79da200de657b1159da2b7021ac94`;
- exact Amharic token `am`, task `transcribe`, no timestamps;
- `max_new_tokens=440`, structured output and exact prompt/EOS accounting;
- the current versioned Amharic normalizer and the same WER/CER functions used
  by corrected B4 validation.

## Three decode strategies

Both base and adapter run every strategy in the same process.

1. `greedy_v1` - the frozen control: one beam, no sampling.
2. `whisper_fallback_v1` - the Whisper paper/Transformers compatibility path:
   temperatures `(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)`, compression-ratio threshold
   `1.35`, log-probability threshold `-1.0`. Per-row sampling seeds are derived
   from the strategy name and audio checksum and are not persisted.
3. `beam5_v1` - deterministic five-beam search with early stopping and no
   sampling.

No repetition penalty or no-repeat n-gram constraint is included. Such a
constraint can force termination while hiding hallucination; it is not a
diagnosis of model/runtime compatibility.

## Aggregate-only metrics

For each model/strategy pair, record WER, CER, EOS rate, cap-hit rate, generated
token length, unique-token ratio, repeated bi/trigram rates, unexpected control
token count and latency. Record no transcript, decoded token sequence, audio
checksum, row identifier, speaker, session or audio.

## Predeclared decision rule

A strategy is viable only if, for both base and adapter:

- EOS rate is exactly 1.0 and cap-hit rate is exactly 0.0;
- no unexpected control token is generated;
- the adapter's Amharic WER is no more than base WER +0.05 absolute;
- the adapter's Amharic WER is no worse than the retained greedy reference
  1.2383.

Among viable strategies, choose the lowest adapter WER; ties within 0.0001 use
the lower median latency. The result is a decode-contract candidate only. It
must later reproduce after adapter merge and CTranslate2 `int8_float16` before
any gate or registry decision.

If no strategy passes, do not run another sweep. Investigate Amharic data/model
compatibility and exposure-bias mitigation with a new plan. Never drop Amharic
or weaken the per-language gate in response.

## Execution boundary

- one newly scanned, commit-bound trainer image;
- one `c6i.2xlarge` builder and one direct on-demand `g6.xlarge` GPU stage;
- no EKS and no Spot;
- a fresh campaign/ledger namespace and immutable output prefix;
- watchdog plus 600-second EC2 lifecycle envelope reserved before launch;
- no model, checkpoint, registry, approved artifact or eval-set write;
- MLflow parent/child run with aggregate metrics and an immutable SQLite
  snapshot after AWS-observed termination and root-volume deletion.

The AWS stage remains blocked until the implementation, behavioural tests,
pinned-image test suite, deterministic bundle, image scan and explicit packet
approval are complete.

## B4/B5 boundary

B4 remains incomplete. Even a successful decode strategy still requires the
Base v5 multilingual training mix, successful final run, Spot interruption and
resume proof, adapter merge, CTranslate2 `int8_float16`, full A5 gates and
reproducible tracking. B5 remains blocked.

## Outcome

Executed once as campaign `b4-amharic-decode-80756890fc17`, attempt 1. The
container completed successfully on one direct on-demand `g6.xlarge`; no EKS,
Spot or training was involved. All three predeclared strategies failed the
hard viability rule, so `selected_strategy` is null and
`training_authorised=false`.

The Whisper fallback was directionally useful but insufficient. For the
retained adapter it improved EOS from 3/25 to 20/25 and reduced cap hits from
22/25 to 5/25, while WER worsened from 1.2383 to 1.2562. The untouched base
still capped on 15/25. Beam search was worse. The complete aggregate-only
record is `platform/evidence/DIAG-2026-002-amharic-decode-compatibility.json`.

The operator desktop lost network connectivity after the container had
persisted its result. AWS execution was unaffected. Recovery was limited to
an already-terminated, identity-matched instance: root-volume deletion was
proved, the original MLflow observer error was preserved, both bound runs were
finished, and an immutable snapshot was written. No recovery launch or
termination occurred. Campaign spend was $1.7539 against a $2.25 ceiling.

Per the predeclared branch, another sweep is prohibited. The next work is a
new Amharic data/model-compatibility and exposure-bias investigation. It must
not weaken the gate, drop Amharic, select this adapter, or unblock B5.
