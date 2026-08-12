# ASR base-model AWS change packet 2026-002E — complete-integrity successor

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

Usable only after independent review PASS of this committed packet and its
exact hashes:

> Approve ASR base-model AWS change packet 2026-002E only, authorizing numbered attempt 6 for one non-transferable 10,800-second offline evaluation attempt within a $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft is not authorization. No AWS execution is authorized by this draft.
After review and the exact owner phrase, a new write-once
`ASR-BASE-MODEL-AWS-AUTH-2026-002E` must capture the phrase, this packet's
SHA-256, the reviewed packet commit, expiry, and only numbered attempt 6.

After that authorization is committed and before the attempt envelope or any
AWS call, the complete `deadline_identity_and_acceptance` stage must execute
in read-only dry-run mode against the actual committed authorization, bindings
and packet. Its write-once committed PASS receipt is
`platform/evidence/ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-2026-002E.json`.
The live runner independently rebinds that receipt to all three artifacts and
refuses before the attempt if the receipt is absent, malformed or different.

## Purpose and immutable history

Attempt 5 correctly refused at
`deadline_identity_and_acceptance` before any AWS mutation. The 002D bindings
omitted `oci_publication_sha256`; the reviewer and implementation review had
validated only the authorization, not the complete stage-1 gate over the real
artifact family. The unconditional shared gate therefore found the missing
field.

Attempt 5 is consumed. Its packet, authorization, bindings, refusal and receipts
are write-once and are not amended:

| Record | SHA-256 |
|---|---|
| Packet 2026-002D | `1c6859219369a0f7d8d39ca05760f5c7610cb7a00e02cddff84e47ff5fa64aa9` |
| AUTH-2026-002D | `9f8dd983178b8b0f07782996d8ae769ed992cffd20d3b57145ebd3c2fec47fc6` |
| Bindings 2026-002D | `b846739200e3513b7170d80d44b73c2f3572e5ecec71d0410dcf7daeb60a9a13` |
| Attempt-5 refusal | `777a16cad922d5d0932c9e0066bb0e46f85cfaeb9713f6affd64a4e8b7bf8c6a` |

The refusal proves zero GPU nodes, zero GPU seconds, zero cost, no image rescan,
no staging and no production change.

## Permanent integrity correction

The prospective bindings are
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002E.json`, SHA-256
`929492b135a55bda5a8e56ba4854fa1d735b69615f2070eabe7ab00eb921af56`.

Every attempt now binds all 11 live executor modules as one exact set:

| Live executor module | SHA-256 |
|---|---|
| `pipeline/asr_base_model_pilot_receipts.py` | `4636a94d0ee870aa336d9a71b9596b8e5fbcfc41980038ce3d3a37ec20ea5f11` |
| `scripts/asr_base_model_pilot_assets.py` | `c9d0fa257620e68984d6b5d23395540b2a5d6ff2067a5b8b5d20026fdef0bb97` |
| `scripts/asr_base_model_ecr_scanning.py` | `6e67bc09aa49289d3e0a2c3307dfcd99b9fabefb9a23faa30ac1a58dbfdc27f7` |
| `scripts/asr_base_model_deadline_dry_run.py` | `1d733a0a12498b1f4d799c835420ec9bd0cd0743d9002071296fb380d36b5493` |
| `scripts/asr_base_model_pilot_integrity.py` | `30bc31c53d55b42205e846792643a7f705aa18ccd2f386e4a63048add591c768` |
| `scripts/asr_base_model_pilot_k8s.py` | `5385706da620e5e72e0cff306242001bb13a0417b25b15db0a8a7acb11d39aa5` |
| `scripts/asr_base_model_pilot_live.py` | `4a4d6233df9cc3e8ed77956cac261b13cd70c6ea79ce0c808dbf534e0546c31d` |
| `scripts/asr_base_model_pilot_plan.py` | `7c500d28e1640112b4147611da3966ddacc12aa5bb90cde567ff2302199ad2e4` |
| `scripts/asr_base_model_pilot_runner.py` | `083e5aa37fc9ad47c9053d6e3b92daf4511468dbca7716c9021209fa52218a90` |
| `scripts/asr_eval_digest_rescan.py` | `52cf1bec00ca82c28cd7d6f2a69ec67369facab56fb899974e0e0f0b59c6ed29` |
| `scripts/asr_eval_oci_publication.py` | `a7f3f287990114b660873122cd4a557dfde79ec0e177afada740fad1c7369405` |

The set comparison is exact. Missing, extra, malformed or changed modules
refuse. Module checks are attempt-independent: conditional integrity guards are
prohibited, including when an attempt skips publication or another operation.

## Complete committed stage-1 dry run

The standing pre-attempt rule is implemented in
`scripts/asr_base_model_deadline_dry_run.py` and enforced by the live runner.

The dry run:

1. requires a clean repository;
2. reads the bindings, authorization and packet through `git show HEAD:<path>`;
3. requires their working bytes to equal their committed bytes;
4. runs the same
   `scripts.asr_base_model_pilot_runner.stage_deadline_identity_and_acceptance`
   wrapper and
   `scripts.asr_base_model_pilot_live.LiveOperations.deadline_identity_and_acceptance`
   implementation used live;
5. validates owner authorization, packet/risk hashes, attempt number, expiry,
   all executor hashes and the reviewed-commit lineage;
6. injects only the already bound expected caller identity so that it makes
   zero AWS calls, creates no deadline action and starts no attempt; and
7. writes a content-addressable, write-once receipt.

This receipt cannot be produced before review and approval because the actual
authorization does not yet exist. The reviewed packet therefore binds its
required path and behavior. Execution requires the later committed receipt to
match the exact packet, authorization, bindings and numbered attempt 6 before
the attempt envelope is written. Only the authorization and this dry-run
receipt may be committed after the reviewed packet commit; any other
post-review path drift refuses.

## Rehearsal and tests

The cold rehearsal reads the actual committed 002E bindings via Git; it contains
no bindings fixture. Its receipt is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002E-COLD/cold-rehearsal.json`,
SHA-256
`59176235a8aeb4da7b1b59782a008ac02d7512788b5588fb74add4f4bfcbe00a`.
Two runs were byte-identical.

The receipt proves:

- committed bindings SHA-256
  `929492b135a55bda5a8e56ba4854fa1d735b69615f2070eabe7ab00eb921af56`;
- all 11 executor hashes pass as an exact, unconditional set;
- one full PASS plus five injected refusals;
- wrong digest and extra finding refuse;
- deadline, isolation and cleanup refusals return to zero;
- no image upload or registry-scanning mutation;
- zero real AWS calls and zero kubectl calls.

Focused ASR suite at presentation: **140 passed, 0 failed, 0 skipped, 0
deselected**, with one non-blocking pre-existing Starlette/httpx deprecation
warning. Coverage includes omission, extra-module, changed-module,
uncommitted-artifact, missing dry-run and artifact-mismatch refusals.

## Unchanged image, scan, risk and scientific scope

No image was rebuilt and no image context changed:

| Binding | Value |
|---|---|
| Immutable tag | `pilot-5d1b8a0` |
| OCI index | `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa` |
| linux/amd64 child | `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e` |
| Config | `sha256:5cdc428267ae873aaea299c1e64fd6fbdf1d84119c4c0b2ee8d307f722e2ff9a` |
| Attestation | `sha256:c8ad9bbae25dda5dbd3db33114fac380b9436076857aaa416b9ca33074e112e1` |
| Image source | `5d1b8a0d87539a50a1d98915893a2ed640207304` |

Risk acceptance
`ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002`, SHA-256
`06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`,
continues unchanged because the accepted image, packages, four CVE tuples,
models, tokenizer and frozen input are unchanged. It remains offline-only,
time-boxed, non-precedential for serving and network-isolated.

Attempt 6 retains the complete 002D gate and scope:

- skip image upload because the exact immutable image already exists;
- read and verify every bound ECR image descriptor and blob;
- ECR Basic as supplementary OS gate at zero critical/high;
- pinned Docker Scout against the digest-reconstructed image, requiring exactly
  the four accepted PyTorch HIGH tuples;
- S3/ECR endpoint-only egress, no inbound network, internet or PHI;
- exact frozen 540-row pilot across 47 languages;
- Whisper large-v3, Meta CTC-1B-v2 and Meta LLM-1B-v2 only;
- at most one GPU node, 10,800 seconds and $10;
- status-keyed cleanup and CPU/GPU desired capacity zero at every terminal
  outcome.

Cost registry remains
`platform/finance/COST-REGISTRY-2026-006.json`, SHA-256
`d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da`.
Attempt 5 spent $0 and does not change the recognized total.

## Prohibited operations

- reuse of attempts 1–5, a seventh attempt, time transfer, extension or spend
  above $10;
- live execution before review PASS, the exact owner phrase, a committed
  write-once authorization and the committed complete stage-1 PASS dry run;
- any image upload, image rebuild, ECR scan-configuration mutation, Inspector
  activation, IAM/KMS mutation or new registry-wide resource;
- any conditional omission of an executor module hash;
- serving, production traffic, training, full-suite scoring or declaring a
  winning base model;
- production SSM, `approved/asr/`, language registry, MLflow registration or
  B5 status changes;
- any image, model, tokenizer, frozen input, accepted finding, scanner identity,
  risk record or reviewed source drift.

## Deviations

None. The user and reviewer requirements are implemented directly. Historical
records remain unchanged.
