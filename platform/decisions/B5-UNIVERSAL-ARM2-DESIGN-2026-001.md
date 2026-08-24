# B5-UNIVERSAL-ARM2-DESIGN-2026-001 — preservation-aware distillation

Status: DESIGNED. Implementation landed (host-safe). Calibration packet is a
DRAFT pending the Arm-2 image digest. **No Arm-2 compute, no sealed data, no
promotion/deployment** until the calibration packet is reviewed and the
budget is approved (Codex reviews #14-#17).

## Why Arm-2

Arm-1 (`b5-universal-arm1-2026-005`) delivered a ~7.1% aggregate macro-WER
improvement (base 0.3352 → best step-14000 0.3114) with large Pidgin
(−28% rel) and Kinyarwanda (−10% rel) gains, but **regressed Lingala** on
every one of the 20 checkpoints (base 0.1845 → 0.199; confirmed on the
386-row sentinel, non-inferiority not established). Lingala and Swahili are
**strong regression sentinels** where the design's rule is *any loss is
disqualifying* (`B5-UNIVERSAL-PILOT-DESIGN-2026-001`). Arm-1 was therefore
rejected and routed here.

## Approach: KD anchoring on the preservation languages only

Train all 7 languages as before (full-FT, gb9, ARCH-2026-001 ACK), but add a
**knowledge-distillation term that anchors the student to a FROZEN teacher on
the preservation/sentinel languages** (english, french, swahili, lingala).
The student stays free to move on the target languages (pidgin, kinyarwanda,
ewe) where Arm-1's gains came from; the KD term penalises drift away from
what the base already serves well, preventing the Lingala-style regression.

Combined per-batch loss (`pipeline/omniasr_train.py::_batch_loss_kd`) — ONE
clean objective, each term separately normalized (see "Round 18/19
corrections" below; the earlier double-normalized `(ctc + α·KD)/batch_size`
form is obsolete):

```
CTC_mean  +  alpha * KD_mean
  CTC_mean = loss_ctc / batch_size
  KD_mean  = ( sum_r weight[r] * mean_over_valid_frames KL(teacher_r||student_r)
               / count(weight>0) ) * T^2
```

CTC stays batch-size-normalized so the LR calibration walls (≤1e-4 full-mode)
hold. The KD term is a per-row mean over valid frames, weighted per language
and averaged over the UNWEIGHTED count of preservation rows (Codex review #19:
a weighted numerator over a weighted denominator cancelled the weight). `alpha`
fixes the KD-to-CTC balance and is chosen by the hyperparameter comparison.

## Teacher eligibility (alignment-constrained)

KD-by-KL over CTC frame log-probs requires the teacher and student to share
the **exact vocabulary ordering and frame-subsampling grid**. Proven ONLY for
the CTC family under unconditioned `ctc_greedy`:

- **Base teacher (used for calibration):** card `medzen_omniASR_CTC_1B_v2`,
  tokenizer `medzen_omniASR_tokenizer_written_v2` (sha `8aa11a10…`) — the same
  card and bytes the student stages, so alignment is by construction.
- **Kinyarwanda v1:** alignment-eligible (a rank-32 encoder-scoped LoRA merged
  onto that exact base with the identical tokenizer), but **not yet wired** —
  it needs its own reviewed, sha-verified fairseq2 card and research-prefix
  staging (its S3 path contains the screen-forbidden `approved/asr`, so it can
  only ever be named by card). `MEDZEN_KD_TEACHER_MODE=kw_v1` refuses today.
- **Barred:** Kinyarwanda v2 (governance: `B5-KW-V2-FUTILITY-2026-001/-002`),
  the LLM variant, and any language-conditioned decode path. Tokenizer-
  compatible ≠ approved teacher.

`assert_kd_alignment` re-checks identical (frames, vocab) at the first batch
and refuses before any GPU-hours if a teacher is misaligned.

## Teacher freezing & determinism

The teacher is a **second, independent** base instance
(`omniasr_distill.load_teacher`): `eval()` + `requires_grad_(False)`, obtained
**before** the student's full-mode unfreeze so the student's updates cannot
mutate it, forward-only under `no_grad`. It holds no optimizer state and is
nothing in the checkpoint (deterministically reconstructed from its
sha-verified card on resume). `eval()` disables dropout so it draws no RNG,
preserving kill/resume trajectory equivalence. `teacher_freeze_audit` asserts
every teacher parameter is non-trainable.

## Resume & non-finite (unchanged guards, extended coverage)

- The KD knobs (`MEDZEN_KD_ENABLE/ALPHA/TEMPERATURE/TEACHER_CARD/TEACHER_MODE/
  PRESERVATION_LANGUAGES`) enter `TrainerConfig.fingerprint_payload`, so a KD
  run **cannot resume a non-KD checkpoint directory** (fail-closed at
  `read_resume_state`).
- The three existing non-finite guards (step-loss, grad-norm, parameters)
  cover a diverging KD term automatically → `TRAINING_DIVERGED_NONFINITE`
  (exit 43), no poisoned checkpoint. Mitigation is numeric: fp32 KL, T² scaling,
  finite temperature/alpha rejected at parse time.

## Files

- `pipeline/omniasr_distill.py` — KD numerics (host-safe reference +
  differentiable torch), alignment, mask, teacher load/freeze.
- `pipeline/omniasr_train.py` — KD knobs in `TrainerConfig`/`parse_config`,
  `_batch_loss_kd`, `make_batch_loss`, `CalibrationMetrics`, teacher load and
  metrics-artifact write in `main()`.
- `pipeline/omniasr_data.py` — AUTHORITATIVE per-row language tag
  (`authoritative_language`, manifest `_lang`, conflicts refused) for the mask.
- `scripts/verify_arm2_calibration.py` — machine-enforced acceptance checker
  over the `calibration-metrics.json` artifact (Codex review #19 F3).
- `.github/workflows/arm2-trainer-image.yml` — native-amd64 build that
  EXPLICITLY runs `--target trainer-test`, builds+scans+SBOMs the final image,
  and prints the digest to pin (Codex review #19 F5).
- `tests/test_omniasr_distill.py` — host-safe + torch-marked KD tests.
- `tests/test_arm2_calibration.py` — host-safe metrics/verifier/semantics tests.

## Open items before compute

1. **Logits contract (in-image):** confirm fairseq2 v0.6.0 `Wav2Vec2AsrModel`
   exposes per-frame CTC log-probs without targets, for both models — the only
   substantive fairseq2 change, validated only in the trainer image (C3).
2. **Frame-alignment mid-run:** confirm the CTC frame count is a function of
   input length/stride (positional), not of learned weights, so a full-FT
   student keeps identical frame counts to the un-adapted teacher.
3. **KD hyperparameters** (alpha 0.5, temperature 1.0 are draft placeholders):
   the calibration run fixes them; a sweep would need multiple packets.
4. **Image + calibration packet:** run `arm2-trainer-image.yml` (native amd64;
   it builds `--target trainer-test`, then the final image, scans + SBOMs it,
   and prints the digest), pin that digest into the committed
   `B5-UNIVERSAL-ARM2-FTCAL-SAGEMAKER-BINDINGS-2026-001.json`, then the
   independent reviewer issues `reviews/b5-universal-arm2-ftcal-2026-001.json`.

## Round 18 corrections (Codex review #18)

The first implementation had real defects, fixed here and validated with real
torch (`tests/test_omniasr_distill.py`: 19 passed, peak-memory needs CUDA):

- **fairseq2 contract:** `_batch_loss_kd` now UNPACKS `(loss, logits, layout)`
  from one student call with `return_logits=True` and `(logits, layout)` from
  the teacher — the round-17 code treated the return as a bare tensor and would
  have crashed on the first batch.
- **KD reduction:** the term is a MEAN over only the VALID, preservation-
  weighted encoder frames. The previous code summed over frames but divided by
  rows (frames-times too large) and included padded frames (making the weight
  depend on clip length). Student and teacher output lengths must match.
- **One clear objective:** `CTC_mean + alpha * KD_mean` (both terms cleanly
  normalized), not the previous double-normalization.
- **Config hardening:** strict boolean `MEDZEN_KD_ENABLE` (a garbage value
  refuses, not silently disables); `alpha ∈ (0, 1]` (0 refused); teacher card
  == student card == pinned `CTC_CARD`; authoritative language tags (a missing
  tag refuses rather than silently dropping preservation).
- **Per-language KD weights** (`MEDZEN_KD_LANGUAGE_WEIGHTS`): Arm-1 improved
  French/English, so a uniform preservation weight could suppress those gains;
  weights let the comparison put heavier pressure on the regressed sentinels
  (lingala, swahili) and lighter on the anchors (english, french).
- **Trainer image:** `Dockerfile.trainer-omniasr` COPYs the module + tests into
  both stages and RUNS all distillation tests at build.

## Round 19 corrections (Codex review #19)

Round 18 fixed the tuple/padding/scaling crashes but left higher-severity
gaps; all six are addressed here and validated with real torch:

- **Per-language weights now WORK (F1):** the old reduction divided a weighted
  numerator by a weighted denominator, so a single-preservation-language batch
  (common at batch size 2) cancelled the weight — 0.5 and 1.5 produced the
  same loss. KD is now a per-row mean over valid frames, the weight scales that
  per-row mean, and the normaliser is the UNWEIGHTED preservation-row count.
  Regression proves 1.5 gives exactly 3× the loss AND gradient of 0.5.
- **Authoritative language (F2):** the batch keys KD off the manifest-derived
  `_lang` (not free-text `primary_language`/`language`), refuses a row without
  it, and refuses metadata that conflicts; strict masking now also refuses a
  non-empty tag outside the training-language set (was: silent zero KD).
- **Structured metrics + executable verifier (F3):** `CalibrationMetrics`
  writes `calibration-metrics.json` (separate CTC/KD/total per step,
  per-language KD row/frame coverage, peak GPU memory, throughput);
  `scripts/verify_arm2_calibration.py` machine-checks the full acceptance set
  and FAILS CLOSED on any gap — including the `serve{readyz}` and
  `dev_sentinel_wer` fields the in-image wrapper must fill post-training.
- **Packet semantics enforced (F4):** `validate_arm2_semantics` cross-checks
  the top-level `distillation` recipe against the `environment` KD variables
  (alpha, temperature, teacher card/mode, preservation set, per-language
  weights), requires a non-empty `acceptance_criteria`, and binds the
  `result_verifier`. A recipe/env disagreement or emptied criteria now refuses.
- **Image test auto-enforced (F5):** `arm2-trainer-image.yml` explicitly builds
  `--target trainer-test` (the in-image distillation tests + fairseq2 contract),
  builds the final image from the same commit, scans it fail-closed, attaches
  SBOM + provenance, and surfaces the digest to pin — no path skips the tests.
- **Docs/cost reconciled (F6):** this doc's combined-loss formula (above) is
  corrected; the packet's cost is the launcher-authoritative $3.20 (one figure,
  not three), its authorization language matches the below-tier calibration
  branch it actually routes through, and KD refuses valid frame lengths outside
  `1..frames`.

## Round 20 corrections (Codex review #20)

Round 19 fixed the training math but the image + calibration pipeline was not
executable or secure. All six blocking findings fixed:

- **Dedicated publisher role (F1):** the workflow assumed a role that trusts
  only `model-images-publish.yml` and grants two unrelated ECR repos (AWS sim:
  implicitDeny). `infra/trainer_image_publisher.tf` is a new non-deploy role
  trusting `arm2-trainer-image.yml@master`, scoped to `medzen-trainer-omniasr`
  only; the workflow uses `MEDZEN_TRAINER_IMAGE_PUBLISHER_ROLE_ARN`.
- **Injection-hardened workflow (F2):** `inputs.sha` must be exactly 40-hex
  (validated first), reaches shell ONLY as the quoted `$SHA` env, and the
  checkout is proven `== sha` BEFORE any credentials are acquired.
- **Real calibration entrypoint (F3):** `pipeline/omniasr_calibrate.py` runs
  train → export → readyz → dev-sentinel WER → finalize → verify → exit
  non-zero on failure, and is rendered as the ContainerArguments for KD
  packets. It fails CLOSED: an exception in any stage leaves serve/dev-WER
  unset, so the verifier refuses and the job exits non-zero — a broken decode
  can never produce a false PASS. WER is a pure, host-tested word-level edit
  distance (no new image dependency).
- **Non-bypassable verifier + canonical contract (F4):** both
  `load_verifier_spec` and `validate_arm2_semantics` pin the canonical contract
  (script, artifact, `expected_steps == MEDZEN_MAX_STEPS`, ceiling ≤ the L4's
  physical 24 GiB, dev-languages a non-empty subset of the preservation set
  that includes lingala+swahili) and require the calibration env inputs. Every
  bypass Codex reproduced now refuses.
- **Evidence binding + samples/s + resume (F5):** metrics carry an `identity`
  block (run fingerprint, job name, export manifest/model sha, per-dev-manifest
  sha, scorer, packet sha, verifier sha); the verifier binds the metrics to the
  reviewed packet's canonical sha (launcher-injected, no self-reference) and to
  its own bytes; samples/s is recorded; the loss equation and step contiguity
  are checked; and the accumulator is persisted/restored across a checkpoint so
  a resumed run keeps the full trajectory.
- **In-image verifier tests (F6):** the Dockerfile COPYs and RUNS
  `tests/test_arm2_calibration.py` in the trainer-test stage and ships the
  entrypoint, verifier and packet in the final image.

**Honest prerequisites** (parallel to the image digest): the dev-sentinel slice
manifests (`platform/manifests/dev-sentinels/{lingala,swahili}.jsonl`) must be
authored, committed and baked into the image, and the wrapper's model-touching
stages (export reload, readyz, CTC-greedy decode) validate only in-image via
`arm2-trainer-image.yml`. The orchestration, metric-binding, verifier and WER
math are host-tested.

## Round 21 corrections (Codex review #21)

Round 20 made the pipeline real; round 21 makes it assumable, loud, and
artifact-authenticated:

- **Caller → reusable executor (F1):** the credential-bearing job moved into
  the reusable `arm2-trainer-image-publish.yml` (the proven model-images
  structure); the role trust binds `job_workflow_ref` to that file — the
  documented home of the claim — instead of relying on its shape for a
  top-level workflow. Positive canary (`arm2-image-canary.yml`, dispatches the
  real executor with `canary=true`: assume role + identity print, no build)
  and negative wrong-ref canary (`arm2-image-canary-wrongref{,-exec}.yml`,
  requires explicit AccessDenied) added.
- **No silent green skip (F2):** the caller has NO job-level `if:` on the role
  variable — a missing configuration FAILS a preflight loudly. The executor
  additionally proves the commit is ON `origin/master` (ancestor gate) and the
  checkout equals it, before credentials; the 40-hex gate is strict
  single-line.
- **Authoritative verification authenticates artifacts (F3):** the verifier
  now requires `--export-model` (model.pt is HASHED; must equal the manifest's
  declared sha and the metrics' identity) and `--receipt`
  (DescribeTrainingJob JSON machine-checked: Completed status, pinned image
  digest, exact rendered environment incl. the injected packet sha + job name,
  KMS key, derived S3 output path, instance, max runtime, spot flag, and the
  calibration ContainerArguments). `--smoke` alone is explicitly
  non-authoritative. The wrapper performs the in-image halves: it hashes the
  actual export bytes against the manifest before readyz.
- **Dev data predeclared and bound (F4):**
  `platform/manifests/dev-sentinels/{lingala,swahili}.jsonl` are AUTHORED —
  all 60 frozen rows per language copied verbatim (uri+checksum+reference)
  from `B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001` — and the packet's
  `result_verifier.dev_manifests` predeclares path+sha256+rows+source. The
  launcher verifies the committed files against the declaration; the wrapper
  refuses to score an undeclared slice in-image; the verifier hard-binds
  `identity.dev_manifest_shas` to the declaration (a plausible hash no longer
  passes); a provenance test proves every committed row exists in the frozen
  selection. The trainer role already holds `s3:GetObject` on the dev audio.
- **Cumulative resume timing (F5):** the metrics sidecar persists elapsed wall
  seconds; a resumed run's throughput divides the FULL trajectory by the
  cumulative time, not the last process's runtime.
- **Prose = machine contract (F6) + DRAFT refusal:** `acceptance_criteria`
  must byte-equal the canonical list derived from `result_verifier`
  (`arm2_acceptance_criteria`); `["PASS"]` refuses. Launch mode refuses any
  packet carrying `DRAFT_STATUS` or a `.DRAFT.` filename.

## Round 22 corrections (Codex review #22)

Round 21's "authoritative" verification and image lifecycle had structural
faults; all three blockers plus the four concerns are fixed:

- **Live authoritative verification (blocker 1):** local files are
  caller-suppliable, so ONLY `--live` may claim authoritative: the verifier
  itself pins account 558069890522 / eu-central-1, calls DescribeTrainingJob,
  verifies the COMPLETE job request (role, network isolation, VPC, volume,
  instance count, checkpoint config, NO input channels, exact rendered
  environment incl. the injected packet+contract shas), follows
  `ModelArtifacts.S3ModelArtifacts`, fetches that exact KMS-encrypted object
  (VersionId captured), safe-extracts and hashes model.pt/manifest/metrics
  itself. Local modes self-label `smoke` / `local-crosscheck`, never
  authoritative.
- **Circularity-free image lifecycle (blocker 2):** the image bakes a
  SELF-REFERENCE-FREE **execution contract**
  (`B5-UNIVERSAL-ARM2-FTCAL-EXECUTION-CONTRACT-2026-001.json`: job_id +
  environment + distillation + result_verifier, no digest/cost). The launch
  packet binds the contract's sha alongside the image digest; the launcher
  verifies byte-equality of the shared blocks and injects
  `MEDZEN_EXECUTION_CONTRACT{,_SHA256}`; the wrapper refuses a contract whose
  bytes do not hash to the injected declaration. Pinning the digest never
  requires an image rebuild.
- **Safe Terraform activation (blocker 3):** `infra/terraform.tfvars` now
  PERSISTS every live activation flag (arm-launch, image-publisher,
  promotion-admission, the exact live github_repo values read back from the
  deployed trusts) plus the new trainer flag; the saved plan
  `arm2-trainer-image-publisher.tfplan` shows exactly **2 add / 0 change /
  0 destroy** (the plain-plan 6-deletion hazard is gone).
- **Per-row dev receipts:** the wrapper records per-row (audio checksum,
  normalized hypothesis, edit distance, ref words); the verifier RECOMPUTES
  row coverage, every edit distance and the corpus WER against the committed
  slices — a scalar can no longer stand alone.
- **Owner-approval environment:** the publish job runs in the
  `trainer-image-publish` environment (required reviewer: the owner — the
  arm-launch-approval pattern) and the role trust's `sub` binds to that
  environment, compensating for the unprotected master branch.
- **Canary asserts identity:** the positive canary requires account
  558069890522 AND `assumed-role/medzen-trainer-image-publisher-role`, not a
  printed identity.
- The packet's verifier-command note now documents the `--live` form.

## Round 23 corrections (Codex review #23)

- **Complete canonical request comparison (critical):** hand-picked receipt
  checks missed `ContainerEntrypoint`, `TrainingInputMode`,
  `CheckpointConfig.LocalPath`, `VolumeKmsKeyId` and `Tags` — a swapped
  entrypoint could run arbitrary code with the checked image/args/env intact.
  `verify_training_receipt` now renders the expected request via the
  launcher's OWN `render_request(packet)` and compares every rendered field
  the API echoes (env exact, no smuggled `VolumeKmsKeyId`, no input channels,
  Tags — injected from `ListTags` in live mode — must equal the rendered
  tags). All five adversarial reproductions are regression tests.
- **Pinned artifact identity (high):** the exact expected path
  `<S3OutputPath>/<TrainingJobName>/output/model.tar.gz` is required by full
  equality (the `output-evil/` startswith bypass is dead), and live mode
  selects the ONE object version created inside the job's AWS-recorded
  execution window (+15 min upload slack) via `list_object_versions`, then
  fetches that explicit VersionId — zero or multiple in-window versions
  refuse; an unpinned fetch fails the bundle check.
- **Decode parity with the pinned OmniASR pipeline (medium):** the dev scorer
  now truncates logits to the model's RETURNED output layout `seq_lens`
  (padded frames cannot vote) and creates its decoder with
  `skip_special_tokens=True`; `SCORER_ID`/`CANONICAL_SCORER` bumped to
  `.../corpus-word-error-rate/2` in lock-step. A fairseq2-gated in-image test
  proves the truncation behaviorally over the real committed slice.
- **Negative canary isolation (medium):** the wrong-ref probe now runs in the
  same `trainer-image-publish` environment as the real publisher job, so the
  token's `sub` matches the trust and ONLY `job_workflow_ref` differs.
- **Tar extraction caps (medium):** per-member size caps (metrics 64 MB,
  manifest 4 MB, model 8 GB) enforced on the declared size AND mid-stream.

## Calibration is a two-step gate

1. **Mechanics + memory** (this DRAFT packet, one 30-step run): validates the
   KD numerics in-image, separate CTC/KD/total loss, per-language KD coverage,
   peak GPU memory, throughput, export/serve, and a directional dev-sentinel
   read. Acceptance criteria are enumerated in the packet.
2. **Hyperparameter selection** (separate, predeclared UNSEALED comparison): a
   single run cannot select alpha/temperature/weights scientifically — a small
   predeclared set of KD settings is compared on the frozen dev sentinels,
   authored and reviewed before any full Arm-2 training.
