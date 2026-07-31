# PLAN-2026-006 — Language-scoped B4 continuation

**Status:** PREPARED, NOT EXECUTED  
**Purpose:** training-system validation  
**Promotable:** no  
**Model registration:** prohibited

## Decision

Continue B4 with the approved reversible scope in
`B4-SCOPE-2026-001-language-deferral.json` (SHA-256
`3ff90e5bc80a28aa615efcf50750144e43275c169ea5a11fb28990fff3c0393e`).
Amharic and Ewe are absent from training, validation, learning-rate selection,
final checkpoint gates and MLflow campaign parameters. Their data and all prior
artifacts remain intact for a later continuous-training campaign.

Acholi, Oromo and Luganda remain active because the immutable attempt-5
evidence shows that LR 3e-4 resolves their smaller termination problems without
weakening any gate. Ewe is deferred because it still fails the termination gate
at LR 3e-4; the LR that fixes Ewe causes other retained languages to fail.

## Immutable inputs

- Adopted source corpus: v2, adoption
  `curated/_versions/v2/ADOPTION-B4-CORRECTED.json`.
- Row policy: `DQ-2026-003-policy-deferral-corrected.json`, 19 total rows.
- Applicable policy rows in the scoped mix: 15; four Amharic policy rows are
  recorded as out of scope rather than deleted or reclassified.
- Active training languages: Acholi, Akan, Fula, Hausa, Igbo, Lingala,
  Luganda, Oromo, Pidgin, Shona, Swahili and Yoruba.
- Active frozen validation languages: Acholi, Akan, Fula, Lingala, Luganda,
  Oromo and Shona.
- Eligible rows after policy exclusions: 3,809.
- Deterministically sampled rows: 3,808.
- Scoped dataset fingerprint:
  `d71be0710c8d28d9cc82511adb863f125526127cbbd7d06a0d28b74781f6d733`.
- The complete nine-set `VAL-2026-001` record stays unchanged; the language
  scope hash separately binds the seven sets authorised for this run.

## Execution sequence

1. Commit a clean tree and recompute the code bundle hash.
2. Run the complete local and pinned-image test suites read-only.
3. Publish a commit-scoped bundle; never overwrite an earlier prefix.
4. Build and scan a new image whose baked commit matches the bundle.
5. Validate the adopted v2 corpus, policy, scope hash, scoped fingerprint,
   frozen manifests, budget, infrastructure and absence of active B4 GPU work.
6. Evaluate the untouched base and run the bounded saved-adapter preflight on
   one direct-EC2 `g6.xlarge` instance.
7. Train three fresh 100-step sweeps at 1e-4, 3e-4 and 5e-4 on the 12-language
   scoped mix. The old attempt-5 adapters are evidence only and cannot be
   resumed or selected because they contain Amharic and Ewe training.
8. Evaluate every sweep on the same seven active frozen sets and apply the
   unchanged hard gates: EOS rate 1.0, cap-hit rate 0.0, no language more than
   +0.05 absolute WER against its in-run base, macro WER not worse, and saved
   adapter smoke pass.
9. Select deterministically only among passing sweeps. Start the final run from
   scratch at the selected LR; do not resume a sweep checkpoint.
10. Gate final checkpoints at steps 100–600 while training. Stop optimisation
    immediately at the first failing checkpoint.
11. Persist immutable S3 and MLflow evidence, prove instance termination and
    root-volume deletion, and register zero models.

## Cost and infrastructure

This scoped dataset is a new experiment identity and therefore uses the fresh
`candidates/budget/b4-scoped/ledger.json` ledger. The earlier
`b4-corrected` ledger and its reconciled spend remain untouched.

The ceiling is **$6.00** for one builder plus at most five sequential direct-EC2
GPU stages (one base+preflight, three sweeps, one final). No EKS and no Spot are
involved. Every root volume is DeleteOnTermination and every GPU stage has a
watchdog plus an AWS lifecycle allowance.

## Terminal interpretation

Even a fully passing run proves only that the corrected, scoped training path
works. It does not prove promotion-grade quality, does not authorise B5, and
does not erase the future review requirements for Amharic or Ewe.
