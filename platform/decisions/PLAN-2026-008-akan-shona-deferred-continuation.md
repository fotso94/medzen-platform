# PLAN-2026-008 - Akan/Shona-deferred B4 continuation

**Status: EXECUTED, FAILED CLOSED AT CHECKPOINT 600.** Revision 2,
2026-08-01. The immutable outcome record is
`platform/evidence/CAMPAIGNRUN-2026-010-failed.json`.

## Authorization and purpose

The platform owner explicitly approved Option 2: defer Akan and Shona after
the 10-language `1e-4` confirmation failed their unchanged hard gates. This is
a reversible experiment-scope decision. No source data, frozen evaluation set,
registry entry, historical evidence, MLflow run or model artifact is deleted
or overwritten.

The campaign remains `training_system_validation`, is structurally
non-promotable and cannot complete Base-v5 B4. It does not add the missing
English/French replay or code-switch slice, prove Spot interruption/resume,
merge the adapter, convert to CTranslate2, register a model, or enter B5.

## Immutable scope

| Surface | Languages |
|---|---|
| Training (8) | hausa, igbo, lingala, luganda, oromo, pidgin, swahili, yoruba |
| Validation (3) | lingala, luganda, oromo |
| Deferred (6) | acholi, akan, amharic, ewe, fula, shona |

The adopted v2 corpus and 19-row policy remain byte-identical. Read-only
derivation for this scope produced 2,309 eligible rows before exclusions,
2,300 after nine applicable exclusions, 2,298 sampled rows and fingerprint
`8fcef27d63635f837611c20c946d04a6ede6caf73320a0a3ee3e391e34e2f749`.
The new write-once adoption key is
`curated/_versions/v2/ADOPTION-B4-SCOPED-8LANG.json`.

## Execution and gates

Execution uses direct on-demand EC2 in `eu-central-1`, not EKS and not Spot:

1. Build and scan one commit-pinned trainer image.
2. Run one base-plus-preflight stage on the three retained validation sets.
3. Train a fresh 100-step `1e-4` confirmation on the eight-language mix.
4. Require finite training, exact saved-adapter reload, EOS rate 1.0, cap-hit
   rate 0.0, macro WER not worse than the in-run base, and no language worse
   than its in-run base by more than +0.05 absolute.
5. Only if every gate passes, train a fresh final run from scratch and gate
   checkpoints at 100-step boundaries through step 600.

No earlier adapter is reusable. No least-bad selection or gate weakening is
permitted. Expected registered models: zero.

## Executed outcome

The base-plus-preflight stage and the fresh `1e-4` confirmation passed. The
fresh final run then passed every unchanged gate at checkpoints 100, 200, 300,
400 and 500. Checkpoint 500 improved macro WER by 34.9% relative to the in-run
base, with EOS rate 1.0 and cap-hit rate 0.0 for Lingala, Luganda and Oromo.

Checkpoint 600 failed closed. Lingala WER rose to 1.1109 against its 0.9207
base, one of 35 Lingala rows exhausted the 440-token budget, and EOS rate fell
to 0.9714. Luganda and Oromo remained healthy, and the macro still improved,
but the per-language and termination gates correctly prevented the aggregate
from hiding the Lingala regression. No checkpoint was selected, registered,
promoted or deployed. Checkpoint 500 remains diagnostic evidence only.

The trajectory supports a late-horizon/overtraining diagnosis more strongly
than removing Lingala: all three retained languages improved monotonically
through step 500 and only Lingala degraded in steps 501-600. A future run must
predeclare its corrected stopping rule and cannot reuse this candidate as a
promotion artifact.

## Budget and cleanup

The cumulative `b4-scoped` ceiling remains $12. At authorization, $7.6861 was
reconciled and $4.3139 remained. The previously verified builder-plus-GPU
worst case is $4.2530, so reservations must fail closed if any intervening
spend removes the $0.0609 margin. Each stage must self-terminate, delete its
root volume, publish immutable results and MLflow snapshots, and reconcile
only after AWS proves cleanup. A failure is reported before any relaunch.
