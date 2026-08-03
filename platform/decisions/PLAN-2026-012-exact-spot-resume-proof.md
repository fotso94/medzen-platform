# PLAN-2026-012 — exact S3 checkpoint Spot resume proof

Status: **EXECUTED — PASSED**

Completion evidence:
`platform/evidence/CAMPAIGNRUN-2026-014-passed.json`

The first Spot lifecycle published and read back the complete step-100 tree
before deliberate operator interruption. A different Spot instance restored
that exact tree and continued from step 101 through step 200 with finite loss
and gradients. Both instances terminated and both root volumes were proved
deleted. The two lifecycles cost $0.3200 and $0.3233; aggregate B4 spend after
reconciliation is $22.5288 of $100 with no unresolved reservation.

Two operator-observer defects were repaired without repeating GPU work. The
first recovery initially lacked the MLflow experiment identity. The final
validator initially expected a boolean where the container correctly emitted
a structured finite-training record. Closure reused the immutable terminated
stage results, created no AWS resource, and wrote the missing write-once MLflow
snapshot. Registered models remain zero and B5 remains blocked.

## Authority and prerequisite

This is the final infrastructure proof already authorized by
`B4-SCOPE-2026-002` revision 3. It may start only after the attempt-5 serving
artifact passes its unchanged selection and untouched-holdout gates. The
launcher verifies the immutable artifact evaluation and selected float16 tree
before reserving spend.

## Fixed two-instance sequence

1. Reserve one `spot_checkpoint` lifecycle and launch one direct-EC2 Spot
   `g6.xlarge`. Train from the pinned base and adopted scoped data at LR
   `1e-4` to step 100. Upload checkpoint-100 and its complete tree manifest.
2. Read back the marker and tree identity. Only then deliberately terminate
   that instance and prove its root volume was deleted.
3. Reserve one `spot_resume` lifecycle and launch a different direct-EC2 Spot
   `g6.xlarge`. Download the exact authorized tree, require trainer state step
   100, resume to step 200 with finite training, upload checkpoint-200, then
   self-terminate and prove root-volume deletion.

The two stages are sequential and independently budgeted. No EKS, launch
template, Auto Scaling group or model registry is involved. A missing marker,
tree mismatch, non-finite training, wrong step, cleanup failure or unresolved
reservation stops the proof.

## Budget and outcomes

The executable aggregate B4 ledger retains its $100 ceiling. Worst-case holds
are derived from `pipeline/budget.py` and are reserved before each launch.
This proof does not select or change a checkpoint, does not alter any quality
gate, and does not register or promote the artifact. Successful completion
closes only the Spot interruption/resume exit criterion; B5 remains blocked.
