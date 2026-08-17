# B5-T5-CALIBRATION-PACKET-2026-001 — DRAFT, launch gated on owner go

Status: DRAFT (work item C4). Nothing in this packet has touched AWS.
Launch requires: bindings bound, dry validation committed, a shared-file
review whose approval phrase is exactly
`authorizing training job t5-calibration-yemba ` under `DECISION: APPROVED`,
and the owner's explicit go — in that order.

## 1. Purpose

One small, cheap, instrumented training run (yemba, 2.3h of audio, CTC
variant) whose ONLY promoted output is a measured audio-hours-per-GPU-hour
figure. The B4 adaptation design §5 prices the whole campaign on the stated
assumption of ~5 audio-h/GPU-h for the 963M CTC model; T5 replaces that
assumption with a measurement and re-prices the table before any campaign
packet is written. The exported checkpoint is a calibration artifact — it
is NOT a candidate, enters no gate run, and is never promoted.

## 2. Exact mutation inventory

| # | Mutation | Tool | Bound by |
|---|---|---|---|
| 1 | ECR push of the trainer image (repo `medzen-trainer-omniasr`), scan-on-push per the registry-scanning flow | docker push | digest TO-BE-BOUND at push; local build ea774b7eba58 is the reference build |
| 2 | `sagemaker:CreateTrainingJob` `medzen-b5-t5-calibration-yemba` | scripts/b5_sagemaker_job.py launch | the byte-exact request rendered from bindings (§3); validate must PASS on the committed request file first |
| 3 | S3 writes under `research/b5-training/t5-calibration-yemba/**` only (checkpoints + output), KMS-encrypted | the job itself | OutputDataConfig/CheckpointConfig in the rendered request |

Read-only: `curated/**` at the bound manifest version, the model root
objects for card `medzen_omniASR_CTC_1B_v2`. Prohibited scopes are screened
by validate (`iam:` outside the pinned role, `approved/asr`, production
registry, `eval/`, mlflow, model-registration).

## 3. Bindings (TO-BE-BOUND at packet execution; none may be invented)

```
job_id                 t5-calibration-yemba
image_uri_with_digest  <ECR URI @sha256 from mutation 1>
instance_type          ml.g6.xlarge          (allowlisted; single GPU L4)
kms_key_arn            arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57
subnets                <private subnets, queried live at bind time>
security_group_ids     <trainer SG, queried live at bind time>
max_runtime_seconds    14400                 (4h)
max_wait_seconds       28800                 (8h spot wait)
cost_ceiling_usd       10.00
volume_gb              100
cost_registry_line     <reserved registry revision at bind time>
environment            MEDZEN_VARIANT=ctc, MEDZEN_LANGUAGES=yemba,
                       MEDZEN_MANIFEST_VERSION=<adopted version>,
                       MEDZEN_SEED=<fixed at bind>, MEDZEN_MAX_STEPS=600
```

Ceiling arithmetic (enforced in code, not prose): 4h x $1.60/h worst-case
on-demand bound = $6.40 <= $10.00; managed spot is mandatory in the
renderer, so the expected spend is ~35% of on-demand (~$2.25).

## 4. Preconditions (verified at bind time, refusals otherwise)

1. The adopted curated manifest version contains yemba training rows and
   EVERY row in the eligible pool carries `license_policy` — the T3 gate
   refuses the whole run otherwise (yemba_egra ingests record
   `commercial_ok` on owner authority; the full-version sweep is the
   bind-time check, not an assumption).
2. That version's COMPLETE.json and approved ADOPTION.json exist and bind
   (the load_mix refusals prove this at job start as well; checking at
   bind time avoids paying for a container start that refuses).
3. The trainer image digest in bindings is the digest mutation 1 pushed,
   and its scan came back clean.
4. Cost registry line reserved; ceiling within the standing delegation.

## 5. Measurement contract (the deliverable)

From the job's CloudWatch-visible stdout and training-provenance.json:

    audio_hours_processed = steps x batch_size x grad_accum x mean_row_seconds / 3600
    gpu_hours             = billable training seconds / 3600
    calibration           = audio_hours_processed / gpu_hours

Written as `B5-T5-CALIBRATION-RESULT-2026-001.json` beside this packet,
and §5 of B4-OMNIASR-ADAPTATION-DESIGN-2026-001.md is re-priced with the
measured figure in the same change. If the measured figure is within
±20% of the assumed 5.0, the campaign table stands with a note; outside
that band, the campaign packet must not be written until the owner sees
the re-priced table.

## 6. Abort criteria

- Loss not descending by step 100 (the descent-gate lesson from B4):
  stop the job, spend so far ~<$1, diagnose offline.
- Spot wait exhausts max_wait: the job never started; zero GPU spend.
- Any refusal from the trainer's gates: the container exits in seconds;
  fix the bindings, never the gates.

## 7. Bind-time amendments (2026-08-17, owner go received)

**Data**: `gb1` failed the trainer's own dedup gate live — the yemba EGRA
manifest carried 8 byte-identical audio pairs, 4 of them with CONFLICTING
transcripts. Curated version `gb2` (yemba only, from_version gb1) drops
the 4 re-listings and BOTH rows of each conflicted pair (recorded in
gb2/COMPLETE.json for source repair); 8,007 rows / 2.25h survive.
`MEDZEN_MANIFEST_VERSION=gb2`. The live gate chain passes end-to-end on
gb2 (evidence: platform/evidence/B5-T5-MIX-PROVENANCE-2026-001.json,
run fingerprint 7878c110e1e5fc2e007ab74132b9cff812638a4f3cec240d40e9a066dfda92b4).
Adapter-level fix routed to a separate session (task_8ffef221).

**Mutation inventory additions** (the VPC's current ECR/S3 endpoints are
attempt-38 TEMPORARY infrastructure and die with its teardown; SageMaker
ENIs get no public IP, so T5 carries its own):

| # | Mutation | Lifecycle |
|---|---|---|
| 4 | security-group `medzen-b5-t5-vpce` = sg-0fee72d218ac002a7 (443 self + S3 prefix list pl-6ea54007 only; default egress revoked) | CREATED at bind; deleted after T5 |
| 5 | vpc-endpoints: s3 gateway + ecr.api + ecr.dkr + logs interface (subnet eu-central-1a subnet-00232b25bc1ac407a, private DNS on) | created at LAUNCH PREP — strictly after batch-6 teardown (s3 gateway per route table is exclusive); deleted after T5 |

**Sequencing**: the cost registry enforces ONE active billable
reservation; attempt 38 holds it. Registry revision 035 closes the
attempt-38 reservation at its terminal and opens T5's $10 reservation in
the same revision. T5 launch prep (endpoints -> dry validation re-run ->
launch) begins only after batch-6 teardown completes.

**ECR**: repository `medzen-trainer-omniasr` created (immutable tags,
scan-on-push, KMS). Digest bound in the bindings file at push completion.
