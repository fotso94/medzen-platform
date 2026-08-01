# PLAN-2026-009 - Base-v5 B4 exit preparation

**Status: PREPARED LOCALLY, DATA AND BUDGET BLOCKED, NOT AUTHORISED FOR AWS.**
Revision 1, 2026-08-01.

## Why the eight-language campaign is not rerun

`CAMPAIGNRUN-2026-010` proved that the corrected trainer, saved-adapter
reload, interleaved gates, direct-EC2 lifecycle, immutable MLflow tracking and
cleanup controls work. Its final trajectory passed every gate through step
500, then failed Lingala at step 600. Checkpoint 500 is diagnostic evidence,
not a candidate: the campaign was non-promotable and did not contain the
Base-v5 replay or code-switch mix.

Removing Lingala or rerunning the same mix would spend money without closing
the actual B4 exit conditions. The next campaign must instead be a new data,
scope, code, image and budget decision.

## Verified data gap

`B4-REPLAY-CODESWITCH-GAP-2026-001` read all 18 manifests and all 5,400
metadata rows named by `v2-COMPLETE.json`. It found zero English rows, zero
French rows, zero `primary_language=mixed` rows and zero rows with multilingual
segments. No sampling switch can create the required data.

The preferred replay candidate is a pinned Mozilla Common Voice scripted
speech release for English and French. Common Voice declares CC0, but the
current Mozilla Data Collective terms also restrict re-hosting/re-sharing.
Before any download or S3 copy, a licence decision must say whether MedZen's
private raw/curated storage and model-training workflow complies with those
terms. An unofficial mirror is not acceptable provenance.

No currently adopted source supplies a relevant Pidgin/English,
Pidgin/French, or retained-language/English/French code-switch slice. The
code-switch source therefore needs either an owner-approved commercial data
agreement or a consented collection. Synthetic or unrelated language pairs
cannot silently substitute for the Base-v5 target.

## Data work packet

Before training:

1. Record one licence decision for the exact English release, French release
   and code-switch source. Pin source revision, terms hash and allowed use.
2. Adapt each source to the exact A3 manifest. Do not persist raw private text
   or audio in logs.
3. Freeze train, validation and untouched holdout splits. Speaker and session
   identifiers must be disjoint; normalized-text near-duplicate clusters must
   not cross splits.
4. Publish immutable completion and adoption records only after checksums,
   licence policy and consent/source fields pass.
5. Build a new training-mix decision with 35% English/French replay (inside
   the required 30-40% band). Split the replay share evenly unless the frozen
   data audit documents a different ratio. The code-switch proportion must be
   explicit and nonzero, derived from the adopted source size, and frozen
   before any evaluation is seen.

The existing eight African-language scope is not automatically carried into
this future campaign. Its active/deferred list must be rebound to the new
dataset fingerprint. Amharic remains deferred. Lingala remains present unless
a fresh, predeclared experiment proves a data-specific failure.

## Predeclared checkpoint selection correction

The next trainer may run to a maximum of 600 steps, evaluating every 100. It
must stop immediately on a hard termination or per-language regression gate.
The validation sets are a selection surface, not promotion evidence. A new
run may select the lowest-macro-WER checkpoint among those that passed every
hard gate before the stop, but only if this rule is implemented and tested
before launch. The selected checkpoint must then pass a wholly untouched
holdout; it cannot be judged on the same rows that selected it.

This rule would make a step-500-like trajectory recoverable in a future run
without retroactively promoting the existing checkpoint. Gate thresholds do
not change.

## Remaining Base-v5 B4 execution

After the data and selection controls exist:

1. Build a new digest-pinned, scan-complete trainer image.
2. Run the bounded end-to-end smoke and fresh base arms on all frozen slices.
3. Prove S3 checkpoint resume on a direct EC2 Spot trainer by deliberately
   interrupting one run after a durable checkpoint, then starting a replacement
   that verifies and resumes that exact checkpoint. Do not use EKS.
4. Run the multilingual LoRA campaign with 35% English/French replay and the
   frozen code-switch share.
5. Merge the selected adapter, convert to CTranslate2 `int8_float16`, and
   evaluate the converted artifact—not the in-memory PEFT model—against the
   full A5 table.
6. Log dataset checksums, split hashes, git SHA, image digest, per-slice
   metrics and human-review records to MLflow.

Registration remains zero until B5 receives a passing gate report and manual
approval.

## Budget and launch refusal

The durable `b4-scoped` ledger now records $9.9801 of its $12 ceiling and has
no unresolved reservation. Only $2.0199 remains, below the $2.1805 worst-case
reservation for even one equivalent final instance. A full Base-v5 run costs
more still. No AWS launch is authorised by this plan.

A future execution needs an itemised budget, a new ceiling decision and a
fresh campaign namespace. Reusing a reconciled reservation or counting only
expected cost is forbidden.

## Exit condition for this preparation phase

This plan becomes executable only when all of the following are immutable and
read-back verified: three licence decisions, English/French/code-switch
manifests, leak-free frozen splits, a new completion/adoption record, a new
dataset fingerprint, the predeclared checkpoint-selection implementation and
tests, and a separately authorised budget. Until then B4 remains open and B5
remains blocked.
