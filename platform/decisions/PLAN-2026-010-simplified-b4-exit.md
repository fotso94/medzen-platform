# PLAN-2026-010 - Simplified B4 servable-artifact exit

**Status: OWNER APPROVED FOR IMPLEMENTATION AND EXECUTION.** Revision 1,
2026-08-01. This plan implements `B4-SCOPE-2026-002` and
`B4-BUDGET-2026-003`. Where Base-v5 conflicts with those records, the owner
decision controls B4; the conflict remains visible in `A5-2026-001`.

## Goal

Keep the existing eight-language training mix and the Lingala/Luganda/Oromo
checkpoint-selection surface, train one fresh candidate, select the safest
passing checkpoint under the predeclared rule, produce a merged CTranslate2
`int8_float16` artifact, measure conversion delta, and prove exact S3
checkpoint recovery using EC2 Spot. No model is registered and B5 remains
blocked.

## Ordered execution

1. Preserve the prior failure record and checkpoint-selection implementation
   in a clean commit.
2. Verify checkpoint selection locally and inside the newly pinned image.
3. Freeze a post-selection Lingala holdout whose speakers and sessions do not
   intersect training or checkpoint-selection data. Prefer a different source
   or domain; if unavailable, record that limitation rather than claiming it.
4. Build and scan one commit-pinned trainer image.
5. Before any GPU launch, report commit, bundle hash, image digest, every
   stage's worst-case reservation, and aggregate committed spend against the
   $100 ceiling.
6. Run one fresh base/preflight, one targeted `1e-4` confirmation, and one
   fresh final campaign. Stop on a hard-gate failure. Select the lowest macro
   WER among earlier passing checkpoints, with the earlier step winning an
   exact tie.
7. Evaluate the selected checkpoint and same-run base on the untouched
   Lingala holdout. This happens after selection and cannot change which
   checkpoint was selected.
8. Merge the selected adapter into the pinned base, convert to CTranslate2
   `int8_float16`, and re-evaluate on the same frozen Lingala/Luganda/Oromo
   validation sets. Record per-language pre/post conversion WER, CER, EOS and
   cap-hit deltas.
9. Launch a Spot proof instance, produce and read-back verify a durable S3
   checkpoint, interrupt it, then launch a replacement Spot instance that
   verifies and resumes that exact checkpoint. Reserve each lifecycle before
   launch and reconcile only after AWS-observed termination.
10. Preserve immutable evidence and MLflow records, confirm zero registered
    models, and close B4 only against the revised exit criteria.

## Explicit deviations

- Code-switch is `NOT EVALUATED`; no licensed slice exists.
- English/French replay and replay-regression are `NOT EVALUATED`; the artifact
  is not protected against English/French forgetting, and EN/FR-facing
  promotion remains blocked.
- Every other unavailable A5 gate is individually listed in `A5-2026-001`.
- These omissions are reversible future-data gaps, not passing results.

## Stop conditions

Stop only if an active hard gate fails, provenance or artifact hashes differ,
the selected checkpoint has no post-selection Lingala holdout evidence, Spot
resume does not prove exact-checkpoint identity, conversion fails, converted
metrics are missing, an AWS lifecycle cannot be reconciled, or aggregate
worst-case spend would exceed $100.

## Exit state

Expected registered models: **zero**. Expected B5 status: **blocked**. The
deliverable is a verified, servable but non-registered artifact plus complete
evidence for a later per-language approval decision.
