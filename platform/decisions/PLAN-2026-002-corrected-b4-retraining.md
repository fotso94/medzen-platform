# PLAN-2026-002 — Corrected B4 retraining · **Option B**

**Status: PREPARED, NOT EXECUTED.** Revision 2 (2026-07-31).
Nothing published, adopted, built, launched, trained, registered or deployed.

**Option B selected: training-system validation.** Option A data collection
follows only after the corrected pipeline passes.

---

## 13 · What success means, and what it does not

> **Expected outcome: ZERO registered models.**
> No model registry entry, no alias, no promotion, no deployment, no B5
> transition. Option B is **structurally non-promotable**.

A pass proves **the corrected training path is viable** — that the objective is
now correctly specified and the model terminates. It does **not** prove Pidgin
quality, does not prove quality anywhere, and authorises nothing.

The candidate is evaluated **diagnostically on nine speaker/session-disjoint
language sets**. There is **no untouched promotion holdout for any language**,
so even a strong result cannot support promotion — the sets used for selection
cannot also serve as the holdout that judges the selection.

---

## 1 · Cost — one itemised ceiling

The earlier draft quoted "~$11 GPU" in one table and "≈$2" in another. Neither
was itemised and both were wrong: the first was a loose guess, the second
omitted image build, validation passes and any failure allowance.

| Item | Basis | Est. |
|---|---|---|
| Image build | c6i.2xlarge @ $0.34/hr × ~12 min (last build: 8 min) | $0.07 |
| ECR storage | ~6 GB × 1 month | $0.06 |
| LR sweep — training | 3 × 100 steps @ ~5.4 s/it + 4 min startup ≈ 13 min each | $0.65 |
| LR sweep — validation | 3 × 385 clips @ ~1 s ≈ 20 min total | $0.34 |
| Final run — training | 600 steps ≈ 47 min + startup ≈ 55 min | $0.92 |
| Final run — base arm | 385 clips once ≈ 7 min | $0.12 |
| Final run — checkpoint eval | 6 checkpoints × 385 clips ≈ 39 min | $0.65 |
| S3 storage | ~0.5 GB adapters + JSON, 1 month | $0.01 |
| **Subtotal** | | **$2.82** |
| Failure allowance | one full re-run of the largest item | $0.92 |
| **Itemised total** | | **$3.74** |
| **HARD CEILING** | enforced by watchdogs and instance count | **$6.00** |

All GPU work on g6.xlarge on-demand at $1.0064/hr. Every instance carries
`shutdown-behavior=terminate` and a watchdog; the ceiling assumes at most one
GPU instance at a time.

## 2 · Prerequisites — technical and governance

Option B has **no new Pidgin data-collection prerequisite**. It is not
prerequisite-free. All of the following must be complete first:

**Technical**

| # | Item | State |
|---|---|---|
| T1 | Corrected collator + `label_length` | **done**, `89d9162` |
| T2 | Corrected label audit | **done** — `ce0b9a29a39f5d4f`, 5+14 |
| T3 | 19-row deferral policy generated | **done, unpublished** — `bb6f64d8ab244db7` |
| T4 | New `ADOPTION.json` binding raw COMPLETE + new policy | **prepared, not published** |
| T5 | Recomputed dataset fingerprint | **computed** — `ad8c63d157419cbd`, dry-run verified |
| T6 | Nine validation manifests frozen | **done** — `VAL-2026-001` |
| T7 | New trainer image from a post-fix commit | **not built** |
| T8 | `generation_config` pinned in one place | **not implemented** |
| T9 | LoRA `task_type=SEQ_2_SEQ_LM` | **not implemented** |
| T10 | One-batch overfit + generation smoke harness | **not implemented** |

**Governance**

| # | Item | State |
|---|---|---|
| G1 | 19-row policy reviewed and approved | **awaiting review** |
| G2 | Adoption approved and published | **blocked on G1** |
| G3 | `DQ-2026-001` scope re-derived **by human review** | **awaiting a human** |
| G4 | Execution approval for GPU spend | **awaiting** |

**G3 is not mechanical.** One row (`d0ffd52881d0b074`, amharic) is no longer
decoder-incompatible, so the draft's 20 entries no longer match the audit. But
*which* rows a human must listen to, and whether that row still warrants review
on its token-rate alone, is a judgement about data — not an arithmetic
adjustment. The draft stays **draft with all 20 entries unclassified** until a
person decides.

## 3 · The 19-row deferral policy review packet

`platform/decisions/DQ-2026-003-policy-deferral-corrected.json` — **generated,
not published.**

| | |
|---|---|
| Policy sha256 | `bb6f64d8ab244db71512aa4fad166ba505a07fc6959fef40491ead3d61774229` |
| Entries | **19** — 5 `over_decoder_limit` + 14 `extreme_token_rate_under_limit` |
| `human_review_performed` | **false** |
| Defects claimed | **0** — every row `unreviewed_anomaly_deferred_by_policy`, `defect: false`, `action: defer_pending_review` |
| Reason code | `policy_deferral_no_human_review` (all 19) |
| Bound to | audit `ce0b9a29a39f5d4f`, COMPLETE raw `a4c0211eb83f3830`, 18 manifests, tokenizer `06f233fe06e7` |
| Content | checksums and numeric metrics only — no transcript, path, speaker or session |

The row that moved: `d0ffd52881d0b074` (amharic) is raw 449 → effective **448**,
exactly *at* the limit and never over it. The `bos_token_id` defect inflated
every row by one token and this one sat on the boundary.

## 4 · Frozen validation sets — predeclared

`VAL-2026-001-frozen-validation-sets.json`. **385 rows, 126.76 min, 9
languages**, all speaker- and session-disjoint from the v2 `asr_train` pool,
zero exact overlap, none used to inform any investigation.

| Language | Rows | Min | Spk | Sess | B3 base WER | manifest sha256 |
|---|---|---|---|---|---|---|
| acholi | 65 | 22.18 | 21 | 21 | 1.2362 | `db519db57317fc3d…` |
| akan | 72 | 23.81 | 13 | 13 | 0.9689 | `fc4fbe7dc085ddf0…` |
| amharic | 25 | 7.46 | 11 | 11 | 1.0000 | `7935560ca958dfb8…` |
| ewe | 34 | 10.71 | 26 | 26 | 1.0049 | `10dba432787cfef6…` |
| fula | 51 | 16.51 | 18 | 18 | 0.9295 | `d9de2db6855f4e16…` |
| lingala | 35 | 10.84 | 8 | 8 | 0.9379 | `a1e033bfd734a5b7…` |
| luganda | 53 | 18.72 | 25 | 25 | 0.9863 | `321865d723977bd7…` |
| oromo | 35 | 10.88 | 11 | 11 | 1.1834 | `fe49f04d7b1f3600…` |
| shona | 15 | 5.65 | 9 | 9 | 1.1365 | `53391b3f7ca111e5…` |

**Normalization:** `pipeline.normalizers.for_language(<lang>)`, version recorded
per language in the frozen record and logged with every result.

**Baselines:** the B3 figures above are MLX native decode and are **context
only**. The comparison arm is the **untouched base scored in the same process,
same runtime, same flags** as the candidate — never a stored number.

**Aggregation: macro-average across the nine languages**, unweighted, so shona
(15 rows) counts as much as akan (72). Weighting by rows would let the two
largest sets carry the verdict.

**Reported per language, always.** The macro-average never appears without the
nine values beside it.

### Gates

| Gate | Threshold | Kind |
|---|---|---|
| `val_eos_rate` | **= 1.0** on every language | **hard** |
| `val_cap_hit_rate` | **= 0.0** on every language | **hard** |
| Per-language regression | no language may exceed its in-run base WER by more than **+0.05 absolute** | **hard** |
| Macro-average WER | must be **≤** the base macro-average | **hard** |
| `train_loss` | recorded | **not a gate** |

The per-language cap exists so a macro-average improvement cannot conceal one
language collapsing. Loss magnitude stays a warning until a correctly aligned
per-language baseline distribution is measured.

## 5 · LR sweep — deterministic, decided in advance

| | |
|---|---|
| Candidates | `1e-4`, `3e-4`, `5e-4` |
| Excluded | `1e-3` — the failed run's value and a suspected aggravator |
| Seed | `0`, identical for all three |
| Data order | identical — same seed, same fingerprint, same temperature 0.5 |
| Everything else | identical: rank 32, batch 2, grad-accum 8, same image, same policy |
| Steps | 100 |
| Comparison point | **checkpoint-100 exactly**, evaluated on all nine sets |
| Selection | lowest macro-average `val_wer` among candidates passing both hard gates |

**Decision, recorded before execution: the final run STARTS FROM SCRATCH at the
selected learning rate. The 100-step sweep checkpoint is NOT resumed.** Resuming
would give the winner a 100-step head start that no other configuration had,
and would make its 600-step curve incomparable to any from-scratch run. The
sweep selects a hyperparameter; it does not contribute weights.

## 6 · One-batch overfit — numeric pass criterion

| | |
|---|---|
| Data | one fixed batch, seed 0 |
| Record | `L0` = loss at step 0 |
| Budget | **≤ 200 optimisation steps** |
| Pass | `L_final ≤ 0.05 × L0` **and** `L_final < 0.5` absolute |
| Also required | every gradient norm finite at every step; no NaN/Inf loss |
| Fail | **stop. Do not proceed to the sweep.** Report `L0`, `L_final`, ratio, steps used |

Both conditions are needed: the ratio alone would pass a run that started
absurdly high and merely fell a long way — which is exactly what the failed run
did, 22.53 → 4.00, an 82% decrease while learning the wrong objective.

## 7 · Generation smoke — after overfit, and at every checkpoint

Runs against the **post-overfit adapter**, then again at **every evaluated
checkpoint**. All five must hold:

1. decoder prompt exactly `[50258, <lang>, 50360, 50364]`;
2. `<|endoftext|>` emitted;
3. **no cap hit** — generated tokens below `max_new_tokens`;
4. logits and loss finite;
5. **the intended LoRA modules are active** — `q_proj`/`v_proj` adapters present
   with `requires_grad=True`, and the wrapped model is a `PeftModel`.

Point 5 exists because a silently inert adapter would produce base-quality
numbers that look like a fix.

Any failure stops the run at that checkpoint. A missing EOS here is the single
check that the failed 600-step run and its $0.88 could not deliver.

## 8 · Corrected data artifacts — prepared, not published

| Artifact | Value | State |
|---|---|---|
| Corrected audit | `ce0b9a29a39f5d4fb25666fdbf6292fb377ca550847428bb7e27b5d3b8224148` | committed |
| Policy `DQ-2026-003` | `bb6f64d8ab244db71512aa4fad166ba505a07fc6959fef40491ead3d61774229` | **unpublished** |
| `ADOPTION.json` | binds raw COMPLETE `a4c0211eb83f3830…` + policy `bb6f64d8…` | **not published** |
| Dataset fingerprint | `ad8c63d157419cbdbadc1d6a2cf8790c0766d76b848152dbd1be4a1373288275` | computed |

**Dry run, nothing written:**

```
eligible BEFORE exclusions : 4620
deferred rows removed      : 19
eligible AFTER exclusions  : 4601   ← exactly
sampled rows               : 4601
NEW fingerprint            : ad8c63d157419cbd…
OLD fingerprint            : 77c7ce61edba96c8…
OLD REJECTED (differs)     : True
```

The published v2 `ADOPTION.json` binds the **20-row** policy by sha256, so the
loader will refuse the 19-row policy until a new adoption is published. That
refusal is the mechanism working, not an obstacle to route around.

## 9 · Image — pinned, costed, not built

| | |
|---|---|
| Current | `sha256:fc6972a5…` bakes `202b005` — predates every fix. **Unusable.** |
| New | built from the final corrected commit, tagged with the full 40-char SHA |
| Base | `python:3.12-slim-trixie@sha256:cab2dbf5…` |
| Deps | `torch 2.13.0+cu130`, `transformers 5.14.1`, `peft 0.20.0` — image-locked |
| Build | `publish_bundle.py` → verified bundle → `builder_userdata.sh` on c6i.2xlarge; `TAR_SHA256` from user-data, never S3 |
| Scan | ECR scan-on-push must reach **COMPLETE**; gate at CRITICAL 0 / HIGH 0 / MEDIUM 0 / LOW 0 |
| Exceptions | per-CVE, package-version-pinned, severity-matched. Allowlist `review_by` **2026-10-28** — valid at execution; a run after that date fails the gate until renewed |
| Provenance | digest read back from ECR; baked `MEDZEN_GIT_SHA` read from the image config blob and matched to the commit |
| Cost | **$0.07** build + $0.06/month ECR |

## 10 · MLflow — parent and children, history immutable

**Run `23868bab2d8448759fc1b9ed26156952` is never modified.** It accurately
records a completed optimization run and no quality claim (`MLFLOW-2026-001`).

```
parent: b4-corrected-<git-sha12>          tags: purpose=training_system_validation
│                                                promotable=false
│                                                parent_failed_run=23868bab…
├── child: lr-1e-4   ├── child: checkpoint-100 … 600
├── child: lr-3e-4   │
└── child: lr-5e-4   └── (final run children)
```

Every run logs immutable hashes as params: `code_git_sha`, `image_digest`,
`code_tar_sha256`, `dataset_fingerprint`, `policy_sha256`, `adoption_key`,
`base_manifest_sha256`, plus each of the nine `val_manifest_sha256_<lang>`.

Metrics per checkpoint: `train_loss`, `grad_norm`, and per language
`val_wer_<lang>`, `val_cer_<lang>`, `val_eos_rate_<lang>`,
`val_cap_hit_rate_<lang>`, `val_gen_tokens_median_<lang>`,
`val_gen_tokens_max_<lang>` — plus `val_wer_macro`, `val_eos_rate_min`,
`val_cap_hit_rate_max`, `val_worst_language_regression`.

Lineage is **forward only**: the corrected run points at the failed one. The
failed run is not edited to reference a successor that did not exist when it
was written.

## 11 · Artifact locations — unique, non-overwriting

```
candidates/evaluations/<training-run-id>/checkpoint-<step>/evaluation.json
candidates/asr/<training-run-id>/checkpoint-<step>/
```

Every `evaluation.json` sha256 is logged as an MLflow param **and** recorded in
the evidence record. No shared or "latest" path exists, so two checkpoints can
never overwrite each other's results.

## 12 · Stop conditions

| Condition | Action |
|---|---|
| Alignment assertion fails | refuse at step 0 |
| `L_final > 0.05 × L0` or `≥ 0.5` after 200 steps | stop before the sweep |
| Generation smoke: no EOS, cap hit, non-finite, or inert LoRA | stop at that checkpoint |
| `val_eos_rate < 1.0` or `val_cap_hit_rate > 0` on any language | stop |
| Any language regresses > +0.05 WER vs the in-run base | stop |
| Non-finite loss or gradient | stop |
| Cumulative spend > **$6.00** | stop |
| Watchdog: 4 h per instance | terminate |

## 13 · AWS resources that would be created

| Resource | Count | Lifetime |
|---|---|---|
| ECR image (new digest) | 1 | persistent |
| EC2 c6i.2xlarge builder | 1 | ~12 min, self-terminating |
| EC2 g6.xlarge on-demand | up to 4 sequential (3 sweep + 1 final) | ≤ 4 h each, self-terminating |
| EBS gp3 root, DeleteOnTermination | 1 per instance | with the instance |
| S3 objects | bundle, adapters, evaluation JSONs, MLflow DB | persistent, `candidates/` only |
| S3 `curated/_versions/v2/ADOPTION.json` + policy | 2 | persistent, one-time |

No EKS, no Spot, no load balancer, no EIP, no registry entry.
