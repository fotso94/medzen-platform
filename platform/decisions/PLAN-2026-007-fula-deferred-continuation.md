# PLAN-2026-007 — Fula-deferred B4 continuation

**Status: APPROVED, PREPARED, NOT YET EXECUTED.** Revision 1,
2026-08-01.

## Authorization and purpose

The platform owner explicitly approved deferring Fula and raising the
cumulative `b4-scoped` ceiling from $9 to $12. This remains a
`training_system_validation` campaign and is structurally non-promotable.
Nothing in this decision deletes or rewrites source data, historical evidence,
prior MLflow runs, frozen manifests, or model artifacts.

The fresh 11-language confirmation in `CAMPAIGNRUN-2026-008-FAILED` passed the
WER gates but failed closed because 3 of 51 Fula rows exhausted the 440-token
generation budget without EOS. All five languages retained below passed both
termination gates. Oromo and Luganda therefore remain active.

## Immutable scope

| Surface | Languages |
|---|---|
| Training (10) | akan, hausa, igbo, lingala, luganda, oromo, pidgin, shona, swahili, yoruba |
| Validation (5) | akan, lingala, luganda, oromo, shona |
| Deferred (4) | acholi, amharic, ewe, fula |

The source corpus and 19-row deferral policy remain byte-identical. For this
scope, 13 policy rows apply and six belong to deferred languages. Read-only
derivation from the adopted v2 manifests produced:

- 2,822 eligible rows before policy exclusions;
- 2,809 eligible rows after exclusions;
- 2,810 deterministically sampled rows;
- dataset fingerprint
  `65cb36952b8a565b0f0dc5e3a6fec97bac722fc5559cf6f70d57ce0985821559`;
- adoption key
  `curated/_versions/v2/ADOPTION-B4-SCOPED-NO-ACHOLI-FULA.json`.

The adoption must be conditionally created and read back before launch. No
earlier adoption or candidate may be overwritten or reused.

## Execution and gates

Execution is direct on-demand EC2 in `eu-central-1`, never EKS and never Spot:

1. Build and scan one commit-pinned trainer image.
2. Run one base-plus-preflight stage on the five retained validation sets.
3. Train a fresh 100-step `1e-4` confirmation on the ten-language mix.
4. Require finite training, saved-adapter reload, EOS rate 1.0, cap-hit rate
   0.0, macro WER not worse than the in-run base, and no language worse than
   its in-run base by more than +0.05 absolute.
5. Only if every gate passes, train a fresh final run from scratch and evaluate
   checkpoints at 100-step boundaries through step 600.

No least-bad selection is permitted. A failed gate means no final run or no
later checkpoint, as applicable. Expected registered models: zero. Promotion,
deployment, and B5 production transition remain prohibited.

## Budget and cleanup

`B4-BUDGET-2026-002` authorizes a $12 cumulative ceiling without resetting the
$6.6415 already reconciled. The corrected builder-plus-GPU worst case is
$4.2530, producing a cumulative worst case of $10.8945 and $1.1055 contingency.
Reservations cover the container watchdog plus both operator termination grace
windows and are reconciled only after AWS confirms termination and root-volume
deletion.

Every stage must leave immutable S3 results and MLflow snapshots, then prove
instance termination, root-volume deletion, no active Spot request, and no
orphan B4 resource. A failure is reported before any relaunch.
