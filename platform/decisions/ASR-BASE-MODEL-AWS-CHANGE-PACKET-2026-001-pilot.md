# ASR base-model AWS change packet 2026-001 — deterministic three-model pilot

Status: **DRAFT — OWNER RISK DECISION RECORDED — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL — NOT EXECUTABLE**

Required approval phrase, usable only after independent review PASS of the
committed packet and risk-acceptance hashes:

> Approve ASR base-model AWS change packet 2026-001 only, including ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-001 at SHA-256 7c6b07fd6db18f888a18ea5bf4f7c278167fd8b021d4e0790c461e9dc1fcabb9 and two non-transferable 10,800-second offline evaluation attempts within the $10 reservation.

The owner decision is recorded but is not executable authorization. No AWS
mutation, ECR push, model download, GPU start or inference may occur before an
independent reviewer returns PASS and the owner then uses the exact phrase
above against the committed packet SHA-256.

## Purpose and outcome boundary

Run one bounded research pilot comparing the existing Whisper large-v3
operational control with Meta Omnilingual ASR CTC-1B-v2 and LLM-1B-v2 on the
expanded prospective evaluation suite. This is an offline base-model decision
aid. It is not training, B5 promotion, model adoption, serving publication or
a production deployment.

The pilot may conclude only `PASS_PILOT`, `INCOMPLETE_MEASUREMENT`,
`BLOCKED_INPUT_FREEZE`, `BLOCKED_IMAGE_SCAN`,
`BLOCKED_NETWORK_ISOLATION`, or `FAILED_CLOSED_EXECUTION`. `PASS_PILOT`
authorizes preparation of a separately reviewed full-suite packet only. It
does not declare a winning model.

## Owner risk decision

The owner prospectively accepts the exact four PyTorch high findings for this
offline pilot only, subject to every control in
`platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-001.json`, SHA-256
`7c6b07fd6db18f888a18ea5bf4f7c278167fd8b021d4e0790c461e9dc1fcabb9`.

The acceptance is:

- CVE-specific, not a blanket package waiver;
- limited to the exact image, source, models, frozen inputs and finding set;
- valid for at most two non-transferable 10,800-second attempts and no longer
  than seven days after exact owner approval;
- void on any scan, severity, package, image, source, model, tokenizer or
  input drift;
- non-precedential for serving, production, training, promotion or any
  traffic-facing workload; and
- effective only while the evaluation workload has no inbound network and
  egress is restricted to private S3/ECR endpoint paths plus endpoint-only
  DNS resolution.

Serving images retain the absolute zero-critical/zero-high rule. The
evaluation image may never be deployed behind a Service, Ingress, load
balancer or traffic route.

## Immutable evidence and source bindings

| Binding | Exact value |
|---|---|
| Unified master | `7a5040601fcd171c394aae679a9fad9d621c673b` |
| Data correction commit | `46448c66e9068026552aa65262d689201c85fe7d` |
| Evaluation runtime source commit | `bd8e14c8c4401916412b00ac899a64a03b2514ef` |
| Data inventory SHA-256 | `f89b9e432a88db7eebe618c617f9c36f49fa2678b291ce16bebafa085b68c953` |
| Correction record SHA-256 | `91da523828a9d21d69b7f01a77c1edcce49ef4c5bab708696eb4e6177a1478ad` |
| Correction addendum SHA-256 | `4960d7611baf649ff4af484a1835c352ab95009ac2697268af6991d23219125f` |
| PASS audit SHA-256 | `c5d4353b179b58d4f5c8f8770c04475ed7e2e45ef5b9518123973dc241ff930a` |
| Audit reproduction SHA-256 | `adba05093d830e3e6d56dec1e408a07e98a8e90074ab64aae2d93174928843b1` |
| Model-source record SHA-256 | `34baae05d5bc74601a2228002fe6c2d86999fddfe1e152e49b4febf62e2817eb` |
| Local qualification v2 SHA-256 | `ee5b07d20d5eee91b3224987848c562793bff70788d6f8ca76ff3691e8a57de1` |
| Risk-acceptance draft SHA-256 | `7c6b07fd6db18f888a18ea5bf4f7c278167fd8b021d4e0790c461e9dc1fcabb9` |
| Cost registry | `COST-REGISTRY-2026-006`, SHA-256 `d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da` |

Two fresh input audits produced byte-identical `PASS_INPUT_FREEZE` records:
canonical stdout SHA-256
`f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad`
and normalized evidence SHA-256
`c5d4353b179b58d4f5c8f8770c04475ed7e2e45ef5b9518123973dc241ff930a`.
The selected 14 r2 and 50 original manifests contain 24,230 rows across 49
languages with zero duplicate identities, zero `asr_train` permissions, zero
missing tiers and zero non-test splits.

The owner audit boundary is manifest namespace `eval/<language>/**`. The
audit must select `manifest.r2.jsonl` beside a frozen original when present
and must never count both. Audio object layout elsewhere beneath
`s3://medzen-speech/**` is not leakage, but every object remains bound to its
frozen SHA-256. Missing provenance, an orphan r2 or hash mismatch refuses.

## Exact image and scan subject

The clean-source image was rebuilt and qualified locally as:

| Binding | Exact value |
|---|---|
| Local tag | `medzen-asr-eval-runtime:pilot-bd8e14c` |
| OCI index | `sha256:7080c0015db46141faeffcbd0363728ce6ebc75d2b0cce1a376346cd4552ad8d` |
| Deployable linux/amd64 child | `sha256:1eb6a90efe2773abefa6a470b85e46c4f59f1ae1bdc19f0148b4f9eb0dc6aac8` |
| Config | `sha256:b5616797277916cff480eae7f51c26bb4dba84289dbb42c354c528880ef710cd` |
| Attestation manifest | `sha256:b7ed3844901cf53326fdf20af28ce419ff6a6b81ccbc5fd7a1ed6b52ac9b74b6` |
| Source label | `bd8e14c8c4401916412b00ac899a64a03b2514ef` |
| Classification label | `offline-evaluation-only` |
| Size | `7,296,779,906` bytes |
| Runtime identity | UID/GID `10001:10001`, read-only root, `/tmp` tmpfs |
| Local scan | Docker Scout 1.18.3, 305 packages, `0 critical / 4 high` |

The four local highs are exactly:

| CVE | Package | Installed | Scanner range/fix | Context control |
|---|---|---|---|---|
| `CVE-2026-24747` | torch | `2.8.0+cu128` | `<2.10.0` / `2.10.0` | Exact official model bytes only; create-only staging; SHA-256 before PyTorch start and immediately before load; no untrusted input. |
| `CVE-2026-4538` | torch | `2.8.0+cu128` | `<=2.10.0` / no scanner fix | No `.pt2` input and zero `torch.export.load` occurrences in the bound inference sources. |
| `CVE-2025-55552` | torch | `2.8.0+cu128` | `<2.9.0` / `2.9.0` | No `torch.rot90` or `torch.randn_like` occurrence and no application-level `torch.compile` dispatch. |
| `CVE-2025-55551` | torch | `2.8.0+cu128` | `<2.9.0` / `2.9.0` | No `torch.linalg.lu` occurrence and no application-level `torch.compile` dispatch. |

The exact fairseq2 loader does use `torch.load`; the packet does not call that
safe merely because `weights_only=restrict` is set. The accepted control is
that attacker-controlled checkpoint bytes cannot enter this context. The
authoritative ECR scan must still complete against this exact child with zero
critical findings and exactly the four tuple-identical highs. An added or
missing finding, severity/package/version drift, incomplete scan or child
mismatch yields `BLOCKED_IMAGE_SCAN` before any GPU scale-up.

## Exact candidates and modes

| Candidate | Immutable identity | Pilot modes |
|---|---|---|
| Whisper large-v3 | base revision `06f233fe06e710322aca913c1bc4249a0d71fce1`; CT2 float16 tree `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e` | unconditioned; exact supported language conditioning |
| Meta CTC-1B-v2 | checkpoint SHA-256 `354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c`; 3,902,956,068 bytes | unconditioned only; conditioned is `NOT_APPLICABLE` |
| Meta LLM-1B-v2 | checkpoint SHA-256 `cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5`; 9,118,733,852 bytes | unconditioned; exact supported language conditioning |
| Meta written tokenizer v2 | SHA-256 `8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e` | shared Meta tokenizer |

Meta source is `facebookresearch/omnilingual-asr` release tag `0.2.0`, commit
`145a12a668aace6c1d0d290128c1225571fc1955`, installed without dependency
resolution. The release declares internal package version `0.1.0`; both are
verified at build and startup. No HTTP ETag is accepted as a SHA-256.

After the complete disjointness gate, sort eligible rows within each
prospective manifest by audio SHA-256 and select the first 10. The hard
maximum is 540 distinct rows. All candidates receive the same ordered rows.

- primary mode: unconditioned/audio-only for all three candidates;
- secondary mode: exact reviewed language conditioning for Whisper and Meta
  LLM;
- Meta CTC conditioned mode: `NOT_APPLICABLE`; and
- no prompts, context examples, proxy IDs, per-language tuning, hidden retries
  or outcome-informed decode change.

## No-PHI and untrusted-input boundary

The pilot admits only frozen public/research evaluation data and exact model
objects listed above. No PHI is authorized. Before artifact staging, a
classification receipt must prove that every selected source has the adopted
public/research license and no PHI designation. Any uncertainty refuses before
download.

The evaluation workload accepts no client request, upload, URL, arbitrary
path, model code, expression or interactive input. It exposes no listening
port and has no Kubernetes Service, Ingress or load balancer. Raw predictions
are research evidence and are never logged to a shared service log.

## Network-isolated execution

Current read-only state is explicit: EKS `medzen-speech` uses VPC CNI
`v1.22.3-eksbuild.1`, but its add-on configuration is presently null and no
ECR/S3 endpoints exist in VPC `vpc-051aa9df8b64bf141`. Therefore network
isolation is an execution prerequisite, not an assumption.

Before any evaluation workload exists, this packet may:

1. create temporary private `ecr.api` and `ecr.dkr` interface endpoints plus
   one S3 gateway endpoint in the exact cluster VPC;
2. attach endpoint policies limited to the exact
   `medzen-asr-eval-runtime` and retained `medzen-nvidia-dra` repositories and
   the exact content-addressed research/evaluation S3 prefixes;
3. enable VPC CNI network-policy support and set
   `NETWORK_POLICY_ENFORCING_MODE=strict` so a new pod starts default-deny,
   never in the standard-mode startup-allow interval; and
4. apply a controller-owned, `hostNetwork=false` evaluation workload with
   `automountServiceAccountToken=false`, no Pod Identity, no Service, ingress
   denied, and egress limited to:
   - TCP/443 to the exact ECR interface-endpoint ENI IPs;
   - TCP/443 to the current `com.amazonaws.eu-central-1.s3` managed-prefix-list
     CIDRs through the S3 gateway endpoint; and
   - UDP/TCP 53 to VPC resolver `172.31.0.2` solely for those AWS endpoint
     names.

Before importing torch, the exact pod must prove permitted S3/ECR endpoint
reachability and refusal to the Meta public download host, a public HTTPS
control, EC2 IMDS and an inbound cross-pod control. The NetworkPolicy
`PolicyEndpoint`, strict-mode daemon state and bounded VPC Flow Log evidence
are retained. Any unexpected accepted flow yields
`BLOCKED_NETWORK_ISOLATION` and destroys the workload before model load.

Model/audio staging occurs before the evaluation workload through the existing
bounded SSM control path on the exact GPU node. The command receives only
short-lived presigned GET URLs for the exact S3 objects, writes a dedicated
node-local staging directory and verifies every byte length and SHA-256. The
evaluation workload then mounts that exact directory read-only. No URL,
Kubernetes token, cloud identity or credential enters the PyTorch container.
The container independently verifies every model, tokenizer and audio hash at
the last possible point before use. Cleanup deletes the staging directory and
terminates the node.

## Exact AWS change boundary

All operations use profile `medzen`, account `558069890522`, region
`eu-central-1`, cluster `medzen-speech` and VPC
`vpc-051aa9df8b64bf141`. The execution refuses before mutation unless caller
identity is exactly `arn:aws:iam::558069890522:user/s.fotso`, the branch and
packet hashes match the independently reviewed commit, both node groups are
healthy at desired zero, and no other billable reservation is active.

Authorized prospective changes are limited to:

- create one tag-immutable, KMS-encrypted
  `medzen-asr-eval-runtime` ECR repository and add only that repository to the
  existing automatic `SCAN_ON_PUSH` registry rule;
- push the exact local image under one immutable research tag and retain it as
  evidence;
- create-only mirror the exact Meta checkpoint/tokenizer bytes and pilot
  bundle beneath a content-addressed, non-serving S3 research prefix;
- create the three temporary private endpoints and their dedicated endpoint
  security group/policies;
- temporarily enable strict VPC CNI network-policy enforcement;
- apply the isolated evaluation namespace, policies and one workload;
- scale only the existing GPU node group from zero to one for the approved
  attempt; and
- write immutable research receipts/evidence.

No IAM role, production SSM parameter, serving alias, approved artifact,
registered model or language registry field may be created or changed. The
execution plan must enumerate exact resource addresses and refuse if it
contains any unlisted add/change/destroy operation.

## Deadline-first execution stages

1. **deadline_identity_and_acceptance** — fsync the hard deadline and refusal
   receipt before mutation; prove account, region, source, packet, signed risk
   authorization, unexpired time box and attempt allowance.
2. **input_freeze_and_no_phi** — reproduce the two identical input-freeze
   hashes, deterministic row list and public/research no-PHI classification.
3. **cost_and_zero_state** — open the single `$10` reservation; require CPU 0,
   GPU 0 and no competing billable packet.
4. **image_publication_and_scan** — create/reverify the evaluation repository,
   push only the bound child, wait for automatic child scan `COMPLETE`, and
   require zero critical plus the exact four accepted high tuples.
5. **artifact_stage** — create-only mirror the exact Meta assets and bundle;
   verify byte length and SHA-256 by read-back. Existing Whisper is read-only.
6. **private_endpoint_and_policy_gate** — create private endpoints, enable
   strict network-policy enforcement, render policies from live endpoint IPs
   and the current S3 prefix-list CIDRs, then prove allowed and denied flows.
7. **gpu_and_sampler_gate** — scale one existing L4 worker, require DRA ready,
   then obtain 120 numeric GPU samples from the exact execution context.
8. **node_local_input_stage** — use the bounded SSM path and short-lived exact
   presigned S3 GETs to populate a dedicated node-local directory, verify all
   hashes, then mount it read-only and prove the PyTorch container has no URL,
   token, cloud identity or credential.
9. **pilot_rows** — run every required candidate/mode with fsync'd
   receipt-per-row durability and deterministic resume. Missing, malformed,
   OOM, cap or termination results remain visible and fail that mode closed.
10. **aggregate_report** — calculate WER/CER micro totals, language-macro,
    per-language/source values, errors/EOS/caps, median, p95 and RTF, load time
    and peak/baseline/sample-count GPU memory.
11. **cleanup_and_expiry** — destroy the evaluation container and namespace;
    delete staged ephemeral files and temporary endpoints; restore the exact
    pre-window VPC CNI configuration; scale GPU to zero; terminate/replace the
    network-policy node; prove zero workers and no endpoint residuals; close
    the attempt and reconcile cost.

Every stage writes PASS or REFUSED immediately and fsyncs before the next
stage or cleanup. A refusal never erases a completed receipt. Safe pre-audio
diagnostics may retain bounded raw errors; after audio staging, diagnostics
contain no audio, reference or prediction body.

## Cost and attempt request

`COST-REGISTRY-2026-006` records `$74.4286064216` committed, `$0` active
reservations and `$225.5713935784` headroom under the `$300` ceiling. The
recorded `g6.xlarge` Linux on-demand rate in `eu-central-1`, effective
2026-08-01, is `$1.0064/hour`.

This packet requests exactly:

- one new `$10.00` conservative reservation;
- two non-transferable attempts of 10,800 seconds each;
- one existing `g6.xlarge` worker maximum and no CPU worker;
- maximum GPU compute 21,600 seconds = 6 hours = `$6.0384`; and
- `$3.9616` maximum for temporary endpoint hours, ECR scan/storage, S3
  transfer/storage and API overhead.

Unused seconds may not move between attempts. The acceptance and allowance
expire at the earliest event listed in the risk record, including seven days
after exact approval. A third attempt, longer window, full-suite evaluation,
additional node, reservation increase or post-expiry rerun requires a new
current scan, risk acceptance and packet.

## Cleanup and retained evidence

After every attempt, even on interruption or refusal:

- evaluation workload, namespace and node-local staging directory: absent;
- temporary ECR API/DKR and S3 endpoints and endpoint security group: absent;
- VPC CNI configuration: restored byte-for-byte to the pre-window value;
- GPU desired/instances/nodes: `0 / 0 / 0`;
- CPU desired/instances/nodes: `0 / 0 / 0`;
- internet-access probe from an evaluation-labelled pod: impossible because
  no evaluation pod remains;
- production SSM, `approved/asr/`, MLflow registered models, language
  `artifact` and `approved_version`: unchanged.

The tag-immutable ECR image, automatic scan record, content-addressed research
objects and immutable receipts remain evidence. They are non-serving and do
not survive as an executable authorization after expiry.

## Deterministic outcomes

- `BLOCKED_INPUT_FREEZE`: data, disjointness, license or no-PHI evidence fails;
  no compute.
- `BLOCKED_IMAGE_SCAN`: authoritative scan is missing, incomplete, drifted or
  not exactly zero critical plus the four accepted highs; no compute.
- `BLOCKED_NETWORK_ISOLATION`: strict startup denial, endpoint-only egress or
  no-inbound proof fails; PyTorch never starts.
- `PASS_PILOT`: every required candidate/mode row and measurement completes.
- `INCOMPLETE_MEASUREMENT`: completed row receipts remain valid but a required
  aggregate/resource measurement is missing.
- `FAILED_CLOSED_EXECUTION`: identity, provenance, time box, cost, receipt,
  cleanup or evidence integrity cannot be proven.

## Prohibited operations

- Any AWS execution before independent review PASS and the exact owner phrase.
- Any unlisted critical/high finding, vulnerability suppression, VEX claim or
  broader waiver.
- Any serving, production, traffic-facing, training or full-suite use of the
  evaluation image or risk record.
- Any public internet egress, inbound network, Service, Ingress, load balancer,
  public IP, host networking, IMDS access or general AWS API access from the
  evaluation workload.
- Any untrusted input, PHI, user audio, upload, arbitrary URL/path, executable
  model code or unbound checkpoint.
- Training, fine-tuning, conversion, registration, MLflow stage transition,
  approved artifact publication, language approval change, production SSM
  update or deployment to a serving route.
- Writes to `approved/asr/`, production SSM, language `artifact`, language
  `approved_version` or any serving-consumed registry field.
- Full-suite scoring; this packet does not declare a winning model.
- Threshold changes, proxy-language conditioning, prompts, examples, hidden
  retries or outcome-informed decode changes.

## Deviation from the serving-image scan sequence

`runtime-image-hardening-v2.md` is explicitly active for serving image work
and requires a separate scan-only packet with zero critical/high. This subject
is not a serving image and cannot satisfy that zero-high rule under the
owner's scoped acceptance. Rather than weaken the serving standard, this
packet keeps the exception outside it and makes authoritative scan
qualification an independently receipted, precompute stage in the same pilot
packet. No GPU can start unless that stage returns the exact accepted set.

This is a disclosed evaluation-only deviation, not a change to the standard.
If the independent reviewer requires a separate scan-only authorization, the
packet must be split before approval; the split may not weaken any gate.

## Review checklist

Independent review must confirm:

1. all four CVE exploitability statements and residual ratings;
2. exact image/source/package/scan and candidate/input bindings;
3. strict-mode startup denial and S3/ECR-only egress design, including the
   VPC-resolver exception and no-inbound proof;
4. zero credential in the main PyTorch container and double hash verification;
5. risk expiry, non-precedential serving boundary and deterministic cleanup;
6. cost arithmetic, non-transferable attempts and no active prior reservation;
7. no writes to production, approved paths or serving registry state; and
8. the disclosed combined scan-stage deviation.

After reviewer PASS, the owner approval must be captured in a new immutable
authorization/signature record binding the committed source, packet SHA-256,
risk-record SHA-256 and approval timestamp. Neither this packet nor the risk
record is edited after that signature.
