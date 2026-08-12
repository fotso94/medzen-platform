# ASR base-model AWS change packet 2026-002B — attempt-3 scan-rule successor

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

Usable only after independent review PASS of this committed packet and its
exact hashes:

> Approve ASR base-model AWS change packet 2026-002B only, authorizing numbered attempt 3 for one non-transferable 10,800-second offline evaluation attempt within a $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This packet is not authorization. After review and exact owner approval, a new
write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002B` must capture the phrase, packet
SHA-256, reviewed commit, expiry, and exactly this attempt boundary.

Before any live invocation, the actual committed authorization blob must be
passed read-only through
`scripts.asr_base_model_pilot_runner.validate_authorization_payload`, and that
validation receipt must be committed. Validation of a fixture or working-tree
copy does not satisfy this gate. No AWS operation is permitted before it passes.

## Purpose and terminal history

This requests one fresh numbered attempt 3 after:

- attempt 1 refused on authorization representation;
- attempt 2 refused at `image_publication_and_scan` because the executor
  appended a second ECR `SCAN_ON_PUSH` rule;
- neither attempt started GPU capacity, downloaded models, evaluated audio, or
  incurred GPU compute cost.

Attempt 2's refusal is immutable at SHA-256
`ce93ccb24047ac2bca0bd7abe38828fb7e529998254177973f24c682b80705cf`.
CloudTrail event `cda77ee3-bdd9-424e-b3b6-dc313696261c` records AWS's exact
rejection: duplicate scan frequencies. Attempts 1 and 2 cannot be reused and
no unused seconds transfer into attempt 3.

## Exact bindings

| Binding | Value |
|---|---|
| Pre-packet qualification/binding commit | `e0dceaec7b5a826e28eda1c0dba60c9888ccc9c1` |
| Reviewed executable source commit | `f29ea243ca1c77d9f245cfc1cfbc7e43e6b5d9ab` |
| Bindings manifest | `platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002B.json`, SHA-256 `f725a22b4c8fd217e3e6ce91899bfc984ff9c8a824dcad6fd9eec3c02d3b4b6f` |
| Qualification | `platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-005.json`, SHA-256 `a900d14dca7100358ad5c532af38022cfb032be38a5657e875e1ac21af6f52df` |
| Cold rehearsal | `platform/evidence/receipts/ASR-BASE-MODEL-2026-002B-COLD/cold-rehearsal.json`, SHA-256 `c663dd77df2de9af8a00ba14e869a5bc5b68fb5a92654f28caa4e40d43423444` |
| Real ECR response capture | `platform/evidence/ASR-ECR-SCANNING-READ-FIXTURE-CAPTURE-2026-001.json`, SHA-256 `f0fdc59f2616e6319a410b60a9e6ca594cf714c8474035435bdd50a13089ec7c` |
| Real ECR response fixture | `tests/fixtures/aws/ecr-get-registry-scanning-configuration-basic-before-asr-eval.json`, SHA-256 `91501916d7e4e5755ad428dd94f57befabc83cd9e592f95a08bfcf9d7909dae7` |
| Risk acceptance | `platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json`, SHA-256 `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c` |
| Input freeze | `f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad` |
| Pilot bundle | `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee` |
| Cost registry | `platform/finance/COST-REGISTRY-2026-006.json`, SHA-256 `d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da` |

Executor source bindings:

| Source | SHA-256 |
|---|---|
| `scripts/asr_base_model_pilot_runner.py` | `cdcfa0d66f3183201f3359fccc1442604a0a1f08fcfbc714da4d30bb3b337559` |
| `scripts/asr_base_model_pilot_live.py` | `35b494afa8675cabdf3091677881b83a03d161767fa2dfaafe9a2535a1ee0c77` |
| `scripts/asr_base_model_ecr_scanning.py` | `6e67bc09aa49289d3e0a2c3307dfcd99b9fabefb9a23faa30ac1a58dbfdc27f7` |
| `scripts/asr_base_model_pilot_fake.py` | `f5ddd7a235e9b8fc360aa4f8b7655943e319ca401216512d266c041563965758` |
| `scripts/asr_base_model_pilot_plan.py` | `13f26c6173e91496b60dd0b7a8bdd2cdaa49fc7002c39300c6750e2cf9f1fa94` |
| `scripts/asr_base_model_pilot_k8s.py` | `417fb03c0a21ce5c2a13e26d2b1d1e5de9be02c471dd50723bd57fd8c6a21f2c` |
| `scripts/asr_base_model_pilot_cold_rehearsal.py` | `71d1947dd20a06aea375b0f08dba5bd336c882c5417abe48af2731bae2fbedda` |

## ECR correction and restoration

The executor must:

1. read and validate the real ECR Basic Scanning response;
2. refuse duplicate scan frequencies, duplicate filters, malformed filters, or
   an ambiguous existing eval-repository filter;
3. persist the exact prior configuration before mutation;
4. add `medzen-asr-eval-runtime` to the existing `SCAN_ON_PUSH` rule;
5. require two stable, exact post-mutation observations before image push;
6. run the authoritative scan against the bound linux/amd64 child manifest;
7. in cleanup after every terminal outcome, restore the exact prior scan type,
   rule structure, filter ordering, filter values, and filter count;
8. require two stable, exact restoration observations.

The canonical prior scanning-configuration SHA-256 is
`4b05e0d75d86932de6b79e2de8d84600fdb3ffc55fd230668112b46f9caa5f88`.
It contains one `SCAN_ON_PUSH` rule and eight filters, with no evaluation filter.

The empty immutable KMS-encrypted `medzen-asr-eval-runtime` repository created
under attempt 2 is now existing read-only infrastructure. Attempt 3 may push
only the exact bound tag/digests into it; it may not create, delete, replace, or
reconfigure the repository.

## Rehearsal and tests

Two cold rehearsals were byte-identical. The fake now enforces AWS's actual
one-rule-per-frequency constraint. The clean pass and all injected failure
paths restore the exact prior configuration. The cleanup-refusal scenario
first reaches zero state and restores ECR configuration, then emits its
deliberate refusal receipt.

- scoped suites: **73 passed, 0 failed, 0 skipped, 0 deselected**;
- cold rehearsal: one full PASS plus deadline, isolation, and cleanup injected
  refusals; zero real AWS/kubectl calls and zero mutations;
- repository suite: **1,568 passed, 59 failed, 0 skipped, 7 deselected**.

The 59 repository failures are the independently disclosed, pre-existing stale
generated-language/B5-scope failures on mainline. They are unrelated to this
executor-only change and must be fixed separately, not bundled into this
packet.

## Unchanged image and risk continuation

No file under `services/asr-eval-runtime/` changed from image source commit
`5d1b8a0d87539a50a1d98915893a2ed640207304`.

- local tag: `medzen-asr-eval-runtime:pilot-5d1b8a0`;
- OCI index: `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`;
- linux/amd64 child: `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
- local scan: zero critical and exactly four owner-accepted PyTorch highs.

Risk acceptance 2026-002 continues only for this exact offline image and
attempt window. It remains non-precedential for serving, expires under its
original time rule, and is void on any source, image, model, tokenizer, input,
finding, or severity drift. The authoritative child scan remains mandatory
before compute.

## Attempt-3 execution boundary

If independently reviewed and exactly approved:

- authorization: one new write-once authorization for numbered attempt 3;
- duration: one non-transferable 10,800-second attempt;
- cost: `$10` ceiling, no amount transferred from attempts 1 or 2;
- capacity: at most one GPU node; CPU remains zero;
- inputs: exact frozen 540-row pilot only;
- models: Whisper large-v3, Meta CTC-1B-v2, Meta LLM-1B-v2 only;
- network: S3/ECR endpoints only, no public internet, inbound traffic, PHI,
  user audio, or untrusted inputs;
- cleanup: status-keyed and mandatory after every result.

The permitted terminal outcomes remain `PASS_PILOT`,
`INCOMPLETE_MEASUREMENT`, `BLOCKED_INPUT_FREEZE`, `BLOCKED_IMAGE_SCAN`,
`BLOCKED_NETWORK_ISOLATION`, and `FAILED_CLOSED_EXECUTION`.

The eventual write-once authorization must bind its
`prepared_repository_commit` to the final commit independently reviewed after
this packet is committed. The pre-packet commit above is evidence lineage, not
permission to execute from an earlier tree.

## Prohibited operations

- reuse of attempts 1 or 2, a fourth attempt, time transfer, duration extension,
  or spend beyond the approved ceiling;
- IAM or KMS creation or modification;
- serving, production traffic, training, full-suite scoring, or declaring a
  winning base model;
- production SSM, `approved/asr/`, language registry, MLflow registration, or
  B5 status changes;
- any image, model, tokenizer, input, scan-finding, severity, or execution-source
  drift after review.

## Deviations

1. A fresh numbered attempt 3 is requested because attempt 2 is terminal; no
   prior attempt is reinterpreted or reused.
2. The scan filter is temporary and restored, correcting packet 2026-002A's
   earlier permanent-update interpretation.
3. The ECR repository is existing state because attempt 2 created it exactly as
   authorized; packet 2026-002B neither creates nor deletes it.
4. The evaluation image is unchanged, so risk acceptance 2026-002 is continued
   rather than rewritten; the authoritative live scan still gates compute.

No other adaptation is made.
