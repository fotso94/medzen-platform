# ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-003B-S6-attempt-39

Shard-6 RE-RUN. Attempt 38 completed all 8,634 Meta-only inferences and
was destroyed by a runner-side validator still demanding whisper-era
counts (postmortem + fix 6b9d09e; salvage-before-validation 166dd45).
This attempt re-evaluates the SAME frozen shard-6 selection under the
fixed validator:

- suite selection: shard 6 of ASR-FULL-EVAL-SUITE-SHARD-MANIFEST-2026-002
  (english[0:1040], igbo[0:1393], maka[0:148], pulaar[0:149],
  sepedi[0:148]; 2,878 rows, row-list sha d834852a...c4a71f)
- pilot bundle 753a23f6...24bdf (unchanged; prestage proof-007 and
  read-fixture capture-007 remain the binding evidence)
- image pilot-8f63996 (unchanged, Meta-only), publication_required=false
- protocol: Meta-only per owner directive 2026-08-16; termination
  protocol and poll tolerance unchanged from attempt 38
- validator changes vs attempt 38 (the ONLY functional delta):
  aggregate completeness derives from medzen_asr_eval.identity.CANDIDATES
  (6b9d09e) and the raw aggregate is persisted to the runner workdir
  BEFORE validation (166dd45); both pinned by
  tests/test_asr_base_model_attempt39_aggregate_completeness.py
- window: bindings attempt_window unchanged (18000s each, job deadline
  16200s); attempt boundary bumped to 39 in the three executor files
- cost: $10 conservative ceiling under registry revision 036
  (to be reserved at launch, after the T5 reservation closes —
  one-active-reservation control)
- launch gating: unchanged full discipline — committed dry validation,
  shared-file review with the numbered approval phrase, AUTH revision
  alone, then caffeinated launch. Launch strictly after the T5
  calibration job reaches a terminal state.
