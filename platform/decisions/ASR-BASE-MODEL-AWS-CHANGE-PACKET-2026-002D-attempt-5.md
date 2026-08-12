# ASR base-model AWS change packet 2026-002D — exact digest-rescan successor

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

Usable only after independent review PASS of this committed packet and its
exact hashes:

> Approve ASR base-model AWS change packet 2026-002D only, authorizing numbered attempt 5 for one non-transferable 10,800-second offline evaluation attempt within a $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This packet is not authorization. After review and the exact owner phrase, a
new write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002D` must capture that phrase,
this packet's SHA-256, the reviewed commit, expiry, and exactly attempt 5.
Before live invocation, the actual committed authorization blob must pass
read-only validation through
`scripts.asr_base_model_pilot_runner.validate_authorization_payload`; its
write-once validation receipt must be committed. No AWS operation is permitted
before that committed dry validation passes.

## Purpose and immutable history

Attempt 4 proved the exact multipart publisher and retained the exact image in
the immutable KMS-encrypted ECR repository. It then correctly refused because
ECR Basic scanning returned zero findings while the accepted offline-risk set
contains four pip-installed PyTorch HIGH findings. Attempts 1, 2, 3, and 4 are
consumed; attempt 4 cannot be reused. No time or authorization transfers from
them.

Write-once records remain unchanged:

- packet 2026-002C, SHA-256
  `3728a07deb9f1cf5f67b9eea7cdb86adf8aceeab1d7fd6628c26826fe17d9282`;
- AUTH-2026-002C, SHA-256
  `ea15bb9a6bfead18770fd97579828112774a37f8a2e9122ae61eb79a1eca4141`;
- attempt-4 refusal, SHA-256
  `23751027f8bd2717a4aff4b99a7bea50517b9c7989eb869c415edfb6d473bfab`;
- risk acceptance 2026-002, SHA-256
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`.

## Scanner-capability diagnosis

The committed record
`platform/evidence/ASR-ECR-BASIC-SCANNING-CAPABILITY-DIAGNOSIS-2026-001.json`,
SHA-256
`e27ccbb75aec0694a1cab2e1eacaac8f06aa0ad86258f748e7df279a64445962`,
establishes the capability boundary:

- ECR Basic scanning examines operating-system packages, not Python packages;
- ECR Enhanced scanning integrates Amazon Inspector and covers operating-system
  and programming-language packages;
- attempt 4 empirically returned COMPLETE with zero findings on the exact child
  while the digest-bound Docker Scout evidence retained the four PyTorch HIGH
  tuples;
- therefore the empty Basic result was structurally unable to prove the
  accepted pip package set and did not show remediation.

This pilot adopts no Inspector Enhanced scanning, service-linked role, billing
scope, or registry-wide scanning mutation. Enhanced scanning belongs in a
future serving-pipeline evaluation under a separate design and authorization.

## Exact image and corrected security gate

The image is unchanged and already published:

| Binding | Value |
|---|---|
| Immutable tag | `pilot-5d1b8a0` |
| OCI index | `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa` |
| linux/amd64 child | `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e` |
| Config | `sha256:5cdc428267ae873aaea299c1e64fd6fbdf1d84119c4c0b2ee8d307f722e2ff9a` |
| Attestation | `sha256:c8ad9bbae25dda5dbd3db33114fac380b9436076857aaa416b9ca33074e112e1` |
| Source commit | `5d1b8a0d87539a50a1d98915893a2ed640207304` |

Attempt 5 must skip every image upload. It must read the immutable tag and
verify the index, linux/amd64 child, config, attestation and every referenced
blob byte-by-byte from ECR. Every descriptor's exact size and SHA-256 must
match before the scan starts.

The prospective two-part gate is:

1. ECR Basic remains a supplementary OS gate: status COMPLETE, zero CRITICAL,
   zero HIGH.
2. Docker Scout CLI `1.18.3`, git commit
   `aa68fc25c596bea659d54867443238fd30218d23`, scans the reconstructed exact
   ECR bytes and must return exactly these tuples and no others:

   - `CVE-2025-55551|torch|2.8.0+cu128|HIGH`
   - `CVE-2025-55552|torch|2.8.0+cu128|HIGH`
   - `CVE-2026-24747|torch|2.8.0+cu128|HIGH`
   - `CVE-2026-4538|torch|2.8.0+cu128|HIGH`

Wrong digest, changed bytes, missing or extra finding, severity/package/version
drift, scanner-version drift, incomplete Basic scan, or a Basic critical/high
finding blocks before artifact staging or compute.

### Scanner prerequisite

The read-only qualification downloaded and byte-verified the exact ECR image,
then Docker Scout refused because this workstation's Scout session is logged
out. The runner now checks an explicit ephemeral
`DOCKER_SCOUT_HUB_USER`/`DOCKER_SCOUT_HUB_PASSWORD` pair before writing the
attempt envelope or making any AWS call. Credentials are neither committed nor
persisted by the runner. Missing credentials fail locally with
`SCOUT_AUTHENTICATION_ABSENT` and do not consume attempt 5.

The final exact-byte Scout scan remains a live gate, not a replay of the prior
SARIF. Independent review may validate the local source and deterministic
rehearsal now; live execution remains prohibited until both scanner credentials
are available and the owner issues the exact approval phrase.

## Bindings

| Binding | Value |
|---|---|
| Pre-packet executor commit | `d5483250a8ac85da1f386148a9fe8b21b5ad08a9` |
| Pilot bindings | `platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002D.json`, SHA-256 `b846739200e3513b7170d80d44b73c2f3572e5ecec71d0410dcf7daeb60a9a13` |
| Digest-rescan bindings | `platform/manifests/ASR-EVAL-RUNTIME-ECR-DIGEST-RESCAN-BINDINGS-2026-001.json`, SHA-256 `82f352517995151c66cb6221f651398d1e4320678674bdded4ee47c0b38fe717` |
| Capability diagnosis | `platform/evidence/ASR-ECR-BASIC-SCANNING-CAPABILITY-DIAGNOSIS-2026-001.json`, SHA-256 `e27ccbb75aec0694a1cab2e1eacaac8f06aa0ad86258f748e7df279a64445962` |
| Cold rehearsal | `platform/evidence/receipts/ASR-BASE-MODEL-2026-002D-COLD/cold-rehearsal.json`, SHA-256 `69c17dba06137704bd8833c29eb0f5d8cfa9376a7c3e362894844c21a2427906` |
| Risk acceptance | `platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json`, SHA-256 `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c` |
| Input freeze | `f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad` |
| Pilot bundle | `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee` |
| Cost registry | `platform/finance/COST-REGISTRY-2026-006.json`, SHA-256 `d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da` |

Executor source bindings:

| Source | SHA-256 |
|---|---|
| `scripts/asr_base_model_pilot_runner.py` | `a1957530f803245505d8cf5b65316a6d06c988f7760cea1a5ad319371f2854ee` |
| `scripts/asr_base_model_pilot_live.py` | `3655bc9a69d693c5a6bfc8bd585d99ef1d38017c08576d3e41b3ee4616ae1f10` |
| `scripts/asr_base_model_pilot_fake.py` | `cac6bf15b735990bc69ed9b45a258023a4616128b96e582b35d4b1d7b2104001` |
| `scripts/asr_base_model_pilot_plan.py` | `c0e534bf1df46a05317cecad57804c6c146bab6fa99c07960f44940415906204` |
| `scripts/asr_base_model_pilot_k8s.py` | `e4b4b5f1e7f68eb8b3f3852aad3738c45cd3988fbd2b9c081077792fae123573` |
| `scripts/asr_base_model_pilot_cold_rehearsal.py` | `f4172ed430fc9fa1284990b400b1e1764cd7f1960b14edb86f1f024509103929` |
| `scripts/asr_eval_digest_rescan.py` | `52cf1bec00ca82c28cd7d6f2a69ec67369facab56fb899974e0e0f0b59c6ed29` |
| `scripts/asr_eval_digest_rescan_qualification.py` | `834da4981aba43705f8ff91ee26c21670311e21bd61045e9e04aa225700d2b1e` |

## Rehearsal and test evidence

Two complete cold rehearsals are byte-identical. The fake execution covers one
aligned PASS plus five injected failures. Required security cases are:

- aligned exact digest plus exact four tuples: PASS;
- wrong child digest: `BLOCKED_IMAGE_SCAN`;
- an extra critical/high tuple: `BLOCKED_IMAGE_SCAN`.

Isolation, deadline and cleanup refusals also remain covered. Every scenario
returns to zero state. There are zero registry-scanning configuration calls,
zero real AWS calls and zero kubectl calls in rehearsal. Missing Scout
credentials separately refuse before attempt-envelope creation or any staged
operation.

Scoped packet suite at presentation: **56 passed, 0 failed, 0 skipped, 0
deselected**. Cold rehearsal: **1 PASS + 5 injected refusals**, byte-identical
twice.

## Execution stages and exact implementations

| Claimed stage | Exact implementation |
|---|---|
| Pre-attempt Scout identity/auth | `scripts.asr_eval_digest_rescan.validate_scout_prerequisites` |
| Deadline and authorization | `scripts.asr_base_model_pilot_live.LiveOperations.deadline_identity_and_acceptance` |
| Frozen input/no-PHI | `scripts.asr_base_model_pilot_live.LiveOperations.input_freeze_and_no_phi` |
| Cost and zero state | `scripts.asr_base_model_pilot_live.LiveOperations.cost_and_zero_state` |
| Existing image + dual scan | `scripts.asr_base_model_pilot_live.LiveOperations.image_publication_and_scan`; `scripts.asr_eval_digest_rescan.scan_exact_ecr_child` |
| Artifact stage | `scripts.asr_base_model_pilot_live.LiveOperations.artifact_stage` |
| Private endpoints/policy | `scripts.asr_base_model_pilot_live.LiveOperations.private_endpoint_and_policy_gate` |
| GPU/sampler | `scripts.asr_base_model_pilot_live.LiveOperations.gpu_and_sampler_gate` |
| Node-local input | `scripts.asr_base_model_pilot_live.LiveOperations.node_local_input_stage` |
| Pilot rows | `scripts.asr_base_model_pilot_live.LiveOperations.pilot_rows` |
| Aggregate | `scripts.asr_base_model_pilot_live.LiveOperations.aggregate_report` |
| Cleanup/expiry | `scripts.asr_base_model_pilot_live.LiveOperations.cleanup_and_expiry` |

## Attempt-5 boundary

If independently reviewed and exactly approved:

- exactly one new write-once authorization for numbered attempt 5;
- one non-transferable 10,800-second attempt and `$10` ceiling;
- at most one GPU node; CPU and GPU begin and end at desired zero;
- exact frozen 540-row pilot only;
- Whisper large-v3, Meta CTC-1B-v2 and Meta LLM-1B-v2 only;
- S3/ECR private endpoints only during evaluation; no public internet, inbound
  traffic, PHI, user audio or untrusted inputs;
- status-keyed cleanup after every outcome;
- exact digest reconstruction and the two-part security gate before model/audio
  staging or GPU scale-up.

## Prohibited operations

- reuse of attempts 1–4, a sixth attempt, time transfer, duration extension or
  spend above the ceiling;
- execution before review PASS, exact owner approval, write-once authorization
  and committed dry validation;
- image upload, ECR registry-scanning configuration mutation, Inspector
  activation, IAM/KMS mutation or new registry-wide resources;
- serving, production traffic, training, full-suite scoring or declaring a
  winning base model;
- production SSM, `approved/asr/`, language registry, MLflow registration or B5
  status changes;
- any image, model, tokenizer, input, accepted tuple, scanner identity, risk
  record or reviewed source drift.

## Deviations

1. ECR Basic is retained but reclassified prospectively as a supplementary OS
   package gate because the official capability boundary and attempt-4 result
   prove it cannot validate pip-installed torch.
2. Inspector Enhanced scanning is deliberately not adopted in this pilot; its
   registry-wide lifecycle is deferred to a future serving-pipeline evaluation.
3. Attempt 5 skips publication because attempt 4 already published and read back
   the exact immutable image. The new runner verifies the remote bytes again.
4. The final read-only Scout qualification is deferred to execution only because
   this workstation currently lacks Docker Scout authentication. A new
   fail-before-AWS prerequisite prevents that local condition from consuming
   the attempt.
5. Risk acceptance 2026-002 remains unchanged because the image, packages,
   models, input freeze and accepted CVE tuples are unchanged. Its original
   expiry and drift rules still apply.

No other adaptation is made.
