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

## Data
gb5 already contains every pilot language's curated corpora. Mixture:
temperature 0.5 with per-language caps sized so no language exceeds ~35%
of steps; replay share for anchors/sentinels >= 15% combined.

## Selection vs evidence (sealed discipline, per language)
Selection: existing dev surfaces only. Evidence: NEW sealed
speaker-disjoint holdouts per pilot language, frozen with audits BEFORE
the first arm launches (kinyarwanda already has one; build the rest the
same way).

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
