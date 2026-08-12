# ASR base-model AWS change packet 2026-002 — complete-executor pilot successor

Status: **DRAFT — INDEPENDENT REVIEW AND NEW EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

Usable only after independent review PASS of the committed packet, execution
assets, qualification and risk-acceptance hashes:

> Approve ASR base-model AWS change packet 2026-002 only, including ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 dc40fc0eaad8bbd546478cab231c03fda55aa8a0c9b5084f03b462f7c4361579 and two non-transferable 10,800-second offline evaluation attempts within the $10 reservation.

This draft is not authorization. A new write-once authorization record must
capture the exact post-review phrase and its timestamp before any AWS mutation.

## Purpose and immutable history

Run the same bounded 540-row, three-model offline pilot intended by packet
2026-001, now with a locally qualified complete executor. Compare:

- Whisper large-v3, CTranslate2 float16;
- Meta Omnilingual ASR CTC-1B-v2; and
- Meta Omnilingual ASR LLM-1B-v2.

This is evaluation only. It is not training, model adoption, B5 promotion,
serving publication or production traffic.

The following write-once records remain unchanged and are superseded only by
reference:

| Historical record | SHA-256 | Treatment |
|---|---|---|
| `platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-001-pilot.md` | `2aea086984e973fed5a51a0e2cecd5d48fc2ad35038cd26fb300e0038ae796b2` | Not executable; not amended |
| `platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-001.json` | `7c6b07fd6db18f888a18ea5bf4f7c278167fd8b021d4e0790c461e9dc1fcabb9` | Prior image only; not amended |
| `platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-001.json` | `30446fb7a603e03a31007dffd14cdac3ef89056d175df2caf30045820ec9d31f` | Preserved; unusable for this successor |
| `platform/evidence/ASR-BASE-MODEL-PACKET-2026-001-PREFLIGHT-REFUSAL.json` | `64ff34b122765b98be7a5fada9966a8adb87a4ae255643d1febdd34fa37b1e39` | Valid refusal evidence; not amended |

The refusal consumed no attempt, performed zero AWS mutation and incurred zero
spend. This successor must obtain a new exact authorization because its source
and image identities differ.

## Exact successor bindings

| Binding | Value |
|---|---|
| Prepared source commit | `e000ccf49f942bc9955fb225bb341053eeef628e` |
| Input-freeze canonical SHA-256 | `f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad` |
| Pilot manifests / rows / languages | `54 / 540 / 47` |
| Pilot row-list SHA-256 | `2170eb450ae9b42c64e02f8753469eb7d74b7b3f2363ae3f770fbd3062e488b6` |
| Pilot bundle identity SHA-256 | `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee` |
| Qualification record | `platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-003.json`, SHA-256 `ab01acd5d6e9df1f297e7a197538e4dd135117913dd21ede177b89849364ea86` |
| Risk acceptance | `platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002.json`, SHA-256 `dc40fc0eaad8bbd546478cab231c03fda55aa8a0c9b5084f03b462f7c4361579` |
| Cost registry | `platform/finance/COST-REGISTRY-2026-006.json`, SHA-256 `d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da` |

### Exact image

| Binding | Value |
|---|---|
| Local tag | `medzen-asr-eval-runtime:pilot-e000ccf` |
| OCI index | `sha256:694690cca82882f40bd9baf3442e715653772f271b206125292c655fbc1db14c` |
| Linux/amd64 child | `sha256:6829d6f9b634b0a3c75023fb273be4b715e847bb8d9260d28402a03bf16317b6` |
| Config | `sha256:7f2d6b19d99f3a2a6c1a6e858cb6df2e9ddb48d2e809709232f9212db3ef2c7a` |
| Attestation | `sha256:45a8c91b455fb3d88aba9f3b0bb68789516699f6ce1bf4594eac69bcd56bebf9` |
| Image size | `7,296,787,640` bytes |
| Local scan | `platform/evidence/ASR-EVAL-RUNTIME-LOCAL-SCAN-2026-003.sarif.json`, SHA-256 `fdeb1de1dc1a5100be0ace067cca516557ca90b93620d354a0626371da8a907a` |
| Finding gate | `0 critical`, exactly the four risk-record high tuples |

The image is non-root `10001:10001`, read-only with `/tmp` tmpfs, contains no
pip/build tools, and is classified `offline-evaluation-only`. The authoritative
ECR scan must bind the same linux/amd64 child. Any drift stops before compute.

## Execution-asset completeness review gate

Every claimed stage has one exact runner function, one real implementation and
one fake implementation exercised by the cold rehearsal:

| Stage | Runner function | Real operation | Local fake |
|---|---|---|---|
| `deadline_identity_and_acceptance` | `scripts.asr_base_model_pilot_runner.stage_deadline_identity_and_acceptance` | `scripts.asr_base_model_pilot_live.LiveOperations.deadline_identity_and_acceptance` | `scripts.asr_base_model_pilot_fake.FakeOperations.deadline_identity_and_acceptance` |
| `input_freeze_and_no_phi` | `scripts.asr_base_model_pilot_runner.stage_input_freeze_and_no_phi` | `scripts.asr_base_model_pilot_live.LiveOperations.input_freeze_and_no_phi` | `scripts.asr_base_model_pilot_fake.FakeOperations.input_freeze_and_no_phi` |
| `cost_and_zero_state` | `scripts.asr_base_model_pilot_runner.stage_cost_and_zero_state` | `scripts.asr_base_model_pilot_live.LiveOperations.cost_and_zero_state` | `scripts.asr_base_model_pilot_fake.FakeOperations.cost_and_zero_state` |
| `image_publication_and_scan` | `scripts.asr_base_model_pilot_runner.stage_image_publication_and_scan` | `scripts.asr_base_model_pilot_live.LiveOperations.image_publication_and_scan` | `scripts.asr_base_model_pilot_fake.FakeOperations.image_publication_and_scan` |
| `artifact_stage` | `scripts.asr_base_model_pilot_runner.stage_artifact_stage` | `scripts.asr_base_model_pilot_live.LiveOperations.artifact_stage` | `scripts.asr_base_model_pilot_fake.FakeOperations.artifact_stage` |
| `private_endpoint_and_policy_gate` | `scripts.asr_base_model_pilot_runner.stage_private_endpoint_and_policy_gate` | `scripts.asr_base_model_pilot_live.LiveOperations.private_endpoint_and_policy_gate` | `scripts.asr_base_model_pilot_fake.FakeOperations.private_endpoint_and_policy_gate` |
| `gpu_and_sampler_gate` | `scripts.asr_base_model_pilot_runner.stage_gpu_and_sampler_gate` | `scripts.asr_base_model_pilot_live.LiveOperations.gpu_and_sampler_gate` | `scripts.asr_base_model_pilot_fake.FakeOperations.gpu_and_sampler_gate` |
| `node_local_input_stage` | `scripts.asr_base_model_pilot_runner.stage_node_local_input_stage` | `scripts.asr_base_model_pilot_live.LiveOperations.node_local_input_stage` | `scripts.asr_base_model_pilot_fake.FakeOperations.node_local_input_stage` |
| `pilot_rows` | `scripts.asr_base_model_pilot_runner.stage_pilot_rows` | `scripts.asr_base_model_pilot_live.LiveOperations.pilot_rows` | `scripts.asr_base_model_pilot_fake.FakeOperations.pilot_rows` |
| `aggregate_report` | `scripts.asr_base_model_pilot_runner.stage_aggregate_report` | `scripts.asr_base_model_pilot_live.LiveOperations.aggregate_report` | `scripts.asr_base_model_pilot_fake.FakeOperations.aggregate_report` |
| `cleanup_and_expiry` | `scripts.asr_base_model_pilot_runner.stage_cleanup_and_expiry` | `scripts.asr_base_model_pilot_live.LiveOperations.cleanup_and_expiry` | `scripts.asr_base_model_pilot_fake.FakeOperations.cleanup_and_expiry` |

Supporting boundaries are implemented by:

- deterministic selection and create-only staging:
  `scripts.asr_base_model_pilot_assets`;
- exact mutation inventory: `scripts.asr_base_model_pilot_plan.exact_plan`
  and `validate_plan`;
- no-service digest-pinned workload and strict egress:
  `scripts.asr_base_model_pilot_k8s.render` and `verify`;
- pre-torch positive/negative probe:
  `medzen_asr_eval.network_probe.probe_network`;
- single-load multi-row execution: `medzen_asr_eval.pilot.run_pilot`;
- exact conditioning: `medzen_asr_eval.conditioning` and
  `services/asr-eval-runtime/assets/language-conditioning-v1.json`;
- WER/CER, latency, RTF and GPU aggregation: `medzen_asr_eval.metrics`; and
- write-once fsync receipts: `pipeline.asr_base_model_pilot_receipts`.

The packet refuses if any mapping is missing, if a stage is not represented in
all three implementations, or if the committed source hash differs.

## Local qualification and cold rehearsal

Canonical focused suites: **23 passed, 0 failed, 0 skipped, 0 deselected**.

The deterministic cold rehearsal invokes the complete runner with fake
AWS/kubectl operations:

- one full `PASS_PILOT` run through all 11 stages;
- isolation-probe failure -> `BLOCKED_NETWORK_ISOLATION` plus PASS cleanup;
- deadline failure -> `FAILED_CLOSED_EXECUTION` plus PASS cleanup;
- cleanup-path failure -> durable cleanup `REFUSED`, while fake resource state
  is already zero;
- `0` real AWS calls, `0` real kubectl calls and `0` mutations.

The immutable cold receipt is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002-COLD/cold-rehearsal.json`,
SHA-256 `08f5c3497a3a4183edad705b1871031ae0bce30eea18015fbb87719999148b04`.
Two final runs were byte-identical.

## Exact input, conditioning and measurement behavior

The two fresh metadata audits remain byte-identical and select
`manifest.r2.jsonl` where present. The selection sorts every prospective
manifest by frozen audio SHA-256 and takes the first ten: 540 rows, 54
manifests, 47 languages. No audio was downloaded during packet preparation.

All candidates run unconditioned. Whisper and Meta LLM additionally run only
with exact supported IDs from the versioned map; unsupported exact IDs are
`NOT_APPLICABLE`. Meta CTC conditioned is `NOT_APPLICABLE`. Proxy languages,
prompts and outcome-informed decode changes are prohibited.

The container loads each candidate once, then processes all rows/modes. Every
backend-load and row result is written and fsync'd immediately. Load failure,
inference exception, malformed result, OOM, termination failure or token-cap
hit writes a refusal receipt and stops fail-closed. Aggregate completeness is
calculated from the exact support map, not a lower-bound row count.

Metrics include WER/CER micro, language-macro, per-language/source, EOS/caps,
latency median/p95, RTF median/p95, load time and numeric GPU-memory
baseline/peak/sample count.

## Network and workload boundary

Before workload creation the runner creates temporary private ECR API/DKR
interface endpoints and an S3 gateway endpoint. Policies allow only:

- pulling the exact evaluation and retained DRA repositories, plus the exact
  current EKS system-image repositories required for CNI, network-policy,
  Pod Identity and kube-proxy readiness after worker scale-up;
- reading the content-addressed pilot and read-only Whisper prefixes; and
- reading ECR layer bytes from the regional starport S3 bucket.

VPC CNI network policy is enabled with strict enforcement before the workload.
The workload has no Service, Ingress, load balancer, host network, Pod Identity
or service-account token. Ingress is empty. Egress is endpoint IP/S3 prefix
CIDRs on TCP 443 plus VPC-resolver DNS.

Before torch import the exact pod proves private endpoints reachable and Meta
public host, public HTTPS control and IMDS refused. It then opens a temporary
port-8080 listener only to prove a second evaluation-image pod cannot connect.
The listener closes before model load. Any unexpected accepted flow returns
`BLOCKED_NETWORK_ISOLATION`.

## Exact prospective AWS boundary

Account `558069890522`, region `eu-central-1`, profile `medzen`, cluster
`medzen-speech`, VPC `vpc-051aa9df8b64bf141` only.

Permanent create-only:

- immutable KMS-encrypted ECR repository `medzen-asr-eval-runtime` and exact
  image/scan evidence; and
- exact content-addressed research bundle under
  `s3://medzen-speech/research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/`.

Bounded update:

- add only the exact evaluation repository to automatic scan-on-push rules.

Temporary create then delete:

- deadline scale-to-zero action;
- one encrypted 60 GiB gp3 volume and GPU-node attachment;
- one endpoint security group, two ECR interface endpoints and one S3 gateway
  endpoint;
- VPC CNI strict network-policy configuration;
- evaluation namespace, DRA namespace/resources, ResourceClaimTemplate,
  NetworkPolicies, evaluation Job, inbound-control Pod and node-local staging;
- existing GPU node group desired `0 -> 1 -> 0`.

The 60 GiB volume is required because the existing GPU worker has a 20 GiB
root disk while the image plus packet-bound models exceed safe root capacity.
It is encrypted, dedicated, unmounted/deleted during cleanup and does not alter
the node group or launch template.

Prohibited: IAM/KMS creation or policy changes, production SSM, `approved/asr/`,
MLflow registration/stage, language `artifact` or `approved_version`, serving,
training, full-suite scoring and any public endpoint.

## Deadline, cost and cleanup

Each attempt is exactly 10,800 seconds and non-transferable. The deadline
envelope and ASG scale-zero action are persisted before later mutation.
`COST-REGISTRY-2026-006` records `$74.4286064216` committed and
`$225.5713935784` headroom. This packet requests one `$10` reservation for at
most two attempts, one `g6.xlarge`, temporary endpoints/EBS/storage and no CPU
worker. The risk acceptance expires no later than seven days after exact owner
approval.

Cleanup is status-keyed and runs in `finally` after every refusal or exception:
workloads/namespaces absent, staging removed, volume detached/deleted,
endpoints/SG deleted, VPC CNI restored, GPU and CPU desired/instances/nodes at
zero, deadline removed, reservation closed. Retained ECR image/scan and
content-addressed research evidence are non-serving.

Deterministic outcomes remain: `PASS_PILOT`, `INCOMPLETE_MEASUREMENT`,
`BLOCKED_INPUT_FREEZE`, `BLOCKED_IMAGE_SCAN`, `BLOCKED_NETWORK_ISOLATION`, or
`FAILED_CLOSED_EXECUTION`. A pass authorizes preparation of a separately
reviewed full-suite packet only; it does not select or adopt a model.

## Deviations from packet 2026-001

1. **Complete executor added.** This is the direct correction for refusal
   `REVIEWED_EXECUTION_ASSETS_ABSENT`.
2. **Dedicated 60 GiB EBS volume.** Packet 2026-001 did not account for the
   20 GiB GPU root disk. The volume prevents capacity-driven partial staging
   and is temporary, encrypted and cleanup-bound.
3. **Temporary pre-torch listener for inbound proof.** The risk boundary still
   prohibits inbound connectivity. A listener is necessary to distinguish a
   NetworkPolicy refusal from “nothing was listening”; it is unreachable,
   synthetic/control-only and closes before torch or model/audio inference.
4. **No separate scan-only packet.** As in packet 2026-001, the image is an
   offline exception subject, not a serving image. The authoritative exact
   child scan is its own precompute stage and cannot start GPU on drift.

No other silent adaptation is made.
