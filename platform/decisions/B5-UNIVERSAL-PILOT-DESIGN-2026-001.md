# B5-UNIVERSAL-PILOT-DESIGN-2026-001 — bounded universal-model pilot (DRAFT for owner numbers approval)

Basis: ARCH-2026-001. Goal: test whether a preservation-aware multilingual
recipe can lift target languages WITHOUT the wave-1 interference — the
recipes review #5 correctly noted we never explored.

## Candidate + roles
- Base/candidate family: omniASR CTC 1B (real-time serving candidate; the
  LLM variant stays an offline accuracy comparator, still refused for
  training until its own calibration exists).
- kinyarwanda — improvement target (ceiling = v1 now, v2 when its sealed
  verdict lands; the ceiling is the yardstick, not the product).
- english, french — replay anchors (non-inferiority mandatory).
- swahili, lingala — strong regression sentinels (the base already serves
  them well untrained; any loss is disqualifying).
- ewe — weak-language transfer probe (does multilingual help where data is
  thin?).
- All other supported languages — replay evidence at gate time.

## Required environment (rev3 — Codex review #7: the documented config
refused to run because gb6's adoption binds DQ-2026-006)
Every pilot arm binds AT LEAST:
- MEDZEN_TRAIN_MODE=full, MEDZEN_MULTILINGUAL_FULL_ACK=ARCH-2026-001
- MEDZEN_MANIFEST_VERSION=gb6
- MEDZEN_LANGUAGES=english,ewe,french,kinyarwanda,lingala,swahili (exactly)
- MEDZEN_EXCLUSIONS_REF=s3://medzen-speech/curated/_versions/gb3/DQ-2026-006-gb3-pulaar-question-mark-deferral.json
- MEDZEN_EXPECT_EXCLUDED=1579  (gb6 ADOPTION binds this policy's sha)
- MEDZEN_TEMPERATURE=0.5 (trainer refuses > 0.5 multilingual)
- MEDZEN_CHECKPOINT_EVERY=2000 (trainer refuses < min(1000, max_steps):
  the 50-step default would write ~2 TB on a 250 GB disk)
- MEDZEN_LR (0 < lr <= 1e-4), MEDZEN_WARMUP_STEPS (1 <= w < max_steps),
  MEDZEN_LR_SCHEDULE (constant|cosine), per-language MEDZEN_AUDIO_CAP_HOURS
The ack now rides the run fingerprint. All bounds are trainer-enforced
refusals, not conventions.

## Arms (sequential, each gated on the last; stop early on a winner)
1. Balanced dense full FT (preservation via mixture balance + low LR +
   replay data in-mix). ~40k steps, 1 x ml.g6.xlarge.
2. Preservation-aware FT with distillation from the kinyarwanda teacher
   (KL/logit matching on teacher languages; needs a distillation loss the
   trainer does not yet have — engineering item, in-image tests +
   calibration before spend).
3. Broader encoder adaptation (wider module scope than q/v) ONLY if 1-2
   both fail their gates.
Internal MoE/adapters: only if all simpler arms show interference.

## Data — gb6 REQUIRED (rev2 correction, Codex review #6)
Rev1 claimed gb5 suffices — FALSE on two counts: gb5 is physically
trainable for kinyarwanda ONLY (its own COMPLETE record says so; the
trainer resolves by /version/ path), and english is entirely absent from
its manifest list. BEFORE any pilot spend: assemble **gb6**, a physical
immutable revision with /gb6/ manifests + audits for EVERY pilot language
(kinyarwanda, english, french, swahili, lingala, ewe), adopted per the
proven gb3 record shape. The trainer now REFUSES silent partial coverage
(every requested language must contribute rows) and multilingual full FT
requires MEDZEN_MULTILINGUAL_FULL_ACK=ARCH-2026-001 — both machine gates,
both tested. Mixture: temperature 0.5, no language over ~35% of steps,
replay share for anchors/sentinels >= 15% combined.

## Selection vs evidence (rev2 — two-tier, Codex review #6)
Rev1 had one sealed set per language shared across sequential arms —
earlier arms' sealed results would have steered later arms (adaptive
contamination). Corrected structure:
- TIER 1 (development): arms are compared and the winner chosen on dev
  surfaces ONLY (existing demoted pools / dev halves, plus new dev splits
  for languages lacking one).
- TIER 2 (promotion): ONE untouched final holdout per language, frozen
  with audits BEFORE arm 1 launches, consumed EXACTLY ONCE by the single
  winning arm under PROMOTION-PROTOCOL-2026-001. No arm-vs-arm comparison
  ever touches Tier 2.

## Estimated cost (on-demand, owner rule applied at report time)
- Arm 1: ~35-40 h g6.xlarge ≈ $60-70
- Arm 2: +calibration (~$1) + ~40 h ≈ $65-75 (if arm 1 fails gates)
- Sweeps/gates: ~$10-15 per arm (eval boxes)
- Arm 3 (if reached): ~40 h ≈ $60-70
- Worst case all three arms + all gates: ~$260 → reported max $360
  (self-review 2026-08-20: the first draft said $230, below the sum of
  its own per-arm lines — corrected upward, never quietly down)
NOTHING launches until the owner approves these numbers per the standing
packet ceremony.

## Rev4 — 2026-08-21: the owner's final 7-language set + gb7

Owner verbatim: keep english and french; add pidgin ("well keep french and
english and depending on how long the ingest complete well decide either
to add pidgin or to proceed without it" -> ingest completed same day at
699 h, Tier-B PASS -> "well wait for pidgin to build the final pilot
set"). Recipe: FULL FINE-TUNE (owner-confirmed; adapters only as the
interference fallback — the shared-adapter family already failed 0/9).

FINAL SET: kinyarwanda (improvement target), pidgin (second target —
698.2 h conversational, the domain-closest corpus in the fleet), english
+ french (replay anchors), swahili + lingala (regression sentinels), ewe
(weak-language transfer probe).

Dataset: **gb7** (13 corpora, sha-pinned, adopted; pidgin joined after
TIER-B-REVIEW-2026-001). Mixture: temperature 0.5 + per-language audio
cap 100 h (bounds any language's share; kinyarwanda and pidgin train on
100 h slices this arm — preservation first, scale later if the recipe
holds). Selection surfaces: existing dev sets + pidgin soreva-v1-tier2-dev.
Tier-2: pidgin sealed half built + RESERVED (development-grade,
placeholder speakers — honestly graded in B5-TIER2-HOLDOUTS-2026-002).

Costs (unchanged envelope): arm 1 ~31 h ≈ $50-70; arms 2-3 only if arm 1
fails its gates; all-arms + gates worst case ~$260 → **reported max $360**.

## Rev5 (2026-08-21T20:58:12Z) — gb8 + pilot-grade pidgin surfaces (Codex review #18)
Dataset: **gb8** (B5-GB8-COMPLETE-2026-001; supersedes gb7 for training).
gb7's pidgin manifest predates the held-out-speaker carve and text cap;
gb7 itself stays immutable at its adopted bytes — the correction is a NEW
version, a rule the trainer enforced itself when an in-place amendment
was attempted (ftcal-2026-002 refused, failed closed).
Pidgin surfaces upgraded to REAL-speaker sets (B5-TIER2-HOLDOUTS-2026-003):
- selection (Tier 1): eval/pidgin/asr/av-heldout-dev (1,500 rows / 25
  speakers) PRIMARY; soreva-v1-tier2-dev stays as a secondary probe.
- Tier 2 (promotion): eval/pidgin/asr/av-heldout-sealed (1,500 rows / 24
  speakers, ledger entry 17 RESERVED). Honest caveat carried on every
  use: speaker-disjoint but NOT text-disjoint (~80% of eval texts occur
  in training — prompted corpus), so pidgin evidence is RELATIVE
  (model-vs-model); absolute claims wait for code-switch/commissioned sets.
Chain proof: calibration rev3 (medzen-b5-b5-universal-ftcal-2026-003) on
gb8 PASSED end-to-end — 13 manifests at gb8, 7,315 eligible rows, 1,579
exclusions bound, finite loss at step 30, PASS_MERGED_EXPORT.
Arm-1 recipe (calibration-proven config, scaled): 40,000 steps, LR 1e-5
constant, warmup 500, batch 2 x grad-accum 8, temperature 0.5,
100 h/language caps, checkpoint every 2,000 (the v2 kinyarwanda full-FT
disk/checkpoint profile, proven at 36k+ steps on this instance type).
Costs (unchanged envelope): arm 1 <= 40 h ceiling ~= $64 worst case;
all-arms + gates worst case ~$260 -> **reported max $360**.

## Rev6 (2026-08-21T21:36:50Z) — Codex review #19: eval integrity + authorization controls
Dataset: **gb9** (B5-GB9-COMPLETE-2026-001) = gb8 minus the 4 english rows
of the one CV contributor who also voices 19 kinyarwanda dev-selection
rows. Pidgin surfaces: the -e1 EVAL-ONLY successors (split=test,
allowed_use=[asr_eval], validator-verified; B5-TIER2-HOLDOUTS-2026-004) —
the earlier objects carried training labels and were superseded, never
overwritten (ledger entries 18-19). Pidgin grading TIGHTENED: conditional
within-corpus evidence only (candidate-saw-prompts asymmetry disclosed);
never sole promotion evidence; production-grade pidgin awaits the
code-switch set. Promotion protocol: -004; checker: latest-covering-record
supersession. Arm launches now require BOTH the independent review AND an
owner authorization COMMITTED at git HEAD (mutable shared-file text can
no longer authorize — reproduced forgery closed). Arm 1 = exact $70
allocation (B5-ARM1-2026-001, PENDING_OWNER_AUTHORIZATION); arms 2-3
separately authorized only if arm 1 justifies them.
