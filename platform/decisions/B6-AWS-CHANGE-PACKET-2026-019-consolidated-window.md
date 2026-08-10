# B6 AWS change packet 2026-019 — consolidated integration window

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL OF THE R7 ALLOWANCE**

This packet implements every required change in
`B6-WINDOW-DESIGN-REVIEW-2026-001` (R1–R7). This draft authorizes no AWS,
Terraform, Kubernetes, worker, ALB, secret, SSM, model, or deployment action.
Execution remains fail-closed until an independent reviewer binds this packet's
SHA-256 and prepared commit and the owner approves this packet and its fresh
allowance.

## Outcome requested

Authorize one compute-free persistent-secret bridge followed by at most two B6.6
integration attempts. Each attempt has its own non-transferable 4,500-second CPU
and GPU deadline and must pass the exact cold rehearsal immediately before any AWS
or Kubernetes stage. A successful attempt proves the deployed synthetic
file/WebSocket/cancellation/failure/isolation path and returns every temporary
resource and worker to zero.

This packet does not change B5's BLOCKED model decision, publish a production SSM
pointer, write `approved/asr/`, register a model, adopt a fine-tune, use PHI, invoke
Bedrock or Fish, or authorize production traffic.

## R1 — persistent synthetic credential

The exact secret `medzen/client-api-keys` persists between attempts. Its resource
policy permanently permits plaintext reads only to the orchestrator role and
explicitly denies every other principal. Three additive `prevent_destroy` guards
cover the secret, its resource policy, and the orchestrator KMS reader policy.

Stage 0 of each attempt does only this credential work:

1. verify the exact persistent secret ARN, name and KMS key and require no
   `DeletedDate`;
2. generate exactly 32 new random bytes;
3. URL-safe-base64 encode them to 43 bytes without padding;
4. create a mode-`0600` file containing exactly those 43 bytes plus one LF
   (`44` bytes total), with `fsync`;
5. publish one new `AWSCURRENT` secret version containing only the bearer SHA-256;
6. bind the version ID to the canonical secret-value SHA-256;
7. prove the new version is `AWSCURRENT`, the file shape and hash are exact, and
   operator `GetSecretValue` is explicitly denied; and
8. persist the stage receipt before proceeding.

The verifier never counts historical versions, tags, old receipts or any other
incidental state. Old plaintext is never read or reused. Cleanup deletes only the
local token; it retains the secret and its permanent policies.

### One-time current-state bridge

Packet 2026-018 correctly cleaned up by scheduling this secret for recoverable
deletion. Therefore the first execution under this new design requires one
compute-free transition before attempt 1:

- verify the exact existing secret is pending recoverable deletion;
- call `RestoreSecret` for that exact ARN without reading its value and
  immediately install the exact operator-deny resource policy before any
  Terraform state or plan work;
- import the existing secret at its historical indexed Terraform address;
- apply a machine-guarded plan limited to the exact secret resource policy and
  orchestrator KMS reader boundary, with no delete or replacement action;
- prove the stable ARN/name/KMS identity and operator read denial; and
- persist `persistent_secret_bridge.json` through the one receipt engine.

The bridge starts no worker and creates no window resource. It is a one-time
transition from the actual packet-018 end state, not a restoration lifecycle. No
future restore or deletion step exists in the canonical window path.

## R2 — one receipt engine

`pipeline/b6_integration_receipts.py` is the sole receipt implementation. It uses
schema `MEDZEN_B6_INTEGRATION_RECEIPT_V2`, write-once hard-link publication,
`fsync`, dependency hashes and exactly `PASS`, `REFUSED`, or
`WARNING_NON_FATAL`. The runner invokes all operations through one `finally`
boundary, so an attempted stage persists before control can advance or cleanup can
start. Cleanup is also a stage. A top-level fault persists a `runner_exception`
receipt with terminal classification `EXCEPTION`, then invokes cleanup.

The only non-fatal warning is the already reviewed post-create ALB tag-mutation
rule. Every unknown/malformed state and every other error refuses.

## R3 — attached cold rehearsal

| Binding | Value |
|---|---|
| Receipt | `platform/evidence/receipts/B6-2026-019-COLD/cold_rehearsal.json` |
| Receipt SHA-256 | `b60436ebe21161e46d8c93b6ff0775f6e3408285604f77abd0d9728030c588af` |
| Scenario-results SHA-256 | `2e3f75248730da81e7159147954c9fd3f933e9e35caecac635d9c062a19cd3cf` |
| Full PASS runs | `1` |
| Injected-failure runs | `23` — one at every enumerated stage |
| PASS receipts in full run | `23 / 23` |
| Refusing stage receipt persisted | `23 / 23` |
| Cleanup completed from induced failure | `23 / 23` |
| Real AWS calls / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Frozen source hashes | `40` |

Before each paid attempt, the real runner executes this same rehearsal in an
isolated temporary directory and compares the enumerated stages, source hashes,
scenario hash, counts and zero-call assertions to this receipt. Any difference
refuses before the credential rotation or deadline stage.

## R4 — one canonical family

The two forked families named `b6_6_successor_*` and
`b6_6_images_before_endpoints_*`, both prior receipt CLIs, the v1 runner and the
old orchestration shell were deleted. Git history and all immutable decision,
authorization, result and receipt records remain unchanged. The live family is
now only `b6_6_*`, centered on:

- `scripts/b6_6_runner.py` — ordering and receipt guarantees;
- `scripts/b6_6_operations.sh` — exact stage dispatcher;
- `scripts/b6_6_cleanup.sh` — zero-state cleanup;
- `scripts/b6_6_credential.py` — invariant-only in-place rotation; and
- `scripts/b6_6_cold_rehearsal.py` — full fake-layer rehearsal.

## R5 — standing verifier policy

`B6-WINDOW-VERIFIER-POLICY-2026-001` is the standing rule: verify only safety and
function, never incidental version counts, tag totals or historical receipt
counts. Unknown, malformed or ambiguous values still fail closed.

### Exact stage order and invariant list

| # | Stage | Required invariants |
|---:|---|---|
| 1 | `stage0` | reviewed bindings; cold gate passed; persistent exact secret; fresh 32-byte material; 0600/44-byte/single-LF file; fresh AWSCURRENT; bearer/version binding; operator denied; workers and temporary resources initially zero |
| 2 | `deadline` | independent CPU and GPU scale-to-zero actions share one deadline no later than 4,500 seconds; compute is still zero before arming |
| 3 | `workers_ready` | at most two `m6i.large` CPU nodes and one `g6.xlarge` GPU node; exact labels/taints; bounded 1,200-second registration poll |
| 4 | `dra_ready` | scan-passed child digest; one ready DRA pod on GPU; endpoints still absent |
| 5 | `rag_ready` | one Ready RAG pod; pinned child digest; synthetic embedded provider; ClusterIP; endpoints absent |
| 6 | `asr_ready` | one Ready ASR pod; exact loader/runtime child digests; zero-shot Whisper large-v3 v0; endpoints absent |
| 7 | `tts_ready` | one Ready TTS pod; pinned child digest; text-only provider; ClusterIP; endpoints absent |
| 8 | `llm_ready` | one Ready LLM pod; pinned child digest; fake provider; ClusterIP; endpoints absent |
| 9 | `orchestrator_ready` | one Ready orchestrator; pinned child digest; exact non-serving registry snapshot; all six workload child digests resident; endpoints absent |
| 10 | `controller_window` | guarded Terraform delta exactly `1 add / 0 change / 0 destroy`; controller only |
| 11 | `controller_ready` | one Ready controller; scan-passed child digest; namespace watch limited to `medzen`; endpoints absent |
| 12 | `pre_endpoint_images` | exactly seven Running/Ready application pods on nodes; all eight declared child digests match `imageID`; endpoint set remains absent |
| 13 | `terraform_window` | controller no-op; guarded temporary delta exactly `11 add / 0 change / 0 destroy`; no secret delta |
| 14 | `endpoints_ready` | two interface plus one S3 gateway endpoint available within 900 seconds; principal-independent pull-only policies; temporary self-referenced endpoint SG |
| 15 | `fargate_probe` | one private task; no public IP; exact RAG digest; backend plus temporary endpoint SG; `/readyz` returns ready |
| 16 | `alb_ready` | one internal ALB; exact SG; one HTTP listener; three exact route rules; one healthy target; creation-time tags exact |
| 17 | `alb_tag_mutation_warning` | PASS when no denial; warning only for AddTags/RemoveTags on the exact listener/rule set after function proof; the documented always-fatal list remains fatal |
| 18 | `file_proof` | one synthetic file request returns transcript, cited reply, text-only TTS and all registry/model versions; no content logged |
| 19 | `websocket_proof` | adopted speech-v1 contract; partial queue 4; audio queue 8; final result preserved; clean disconnect |
| 20 | `cancellation_proof` | synthetic barge-in/cancel completes within 250 ms and preserves protocol integrity |
| 21 | `failure_drills` | auth/validation/timeout/backpressure and dependency refusal are controlled; RAG selector drill restores without pod recreation; no real Bedrock/Fish call |
| 22 | `isolation_proof` | orchestrator is the only ingress; dependencies remain ClusterIP; exact backend SG is the ALB source; no public load balancer |
| 23 | `cleanup` | Fargate task, ingress/ALB, workloads, DRA, controller, endpoints, temporary SG/IAM/ECS and workers all zero; production pointer unchanged; local token absent; persistent secret retained and operator denied; deadlines disarmed only after zero |

## R6 — settled physics retained

- All Kubernetes images are pulled, running, Ready and proven resident before
  private ECR DNS endpoints are enabled.
- Endpoint policies use wildcard principal only where AWS requires a principal;
  action and resource boundaries remain pull-only.
- The temporary endpoint SG admits TLS only from its own self-reference and is
  attached only to the one probe task; it is deleted in cleanup.
- Worker registration remains a bounded 1,200-second poll.
- Deadline arming precedes every compute scale-up.
- Any post-endpoint Kubernetes image-pull failure is fatal and receipted.
- The four exact post-create AddTags/RemoveTags pairs may warn only after the ALB
  and Fargate proofs; create, listener/rule, routing, health and cleanup denials
  remain fatal.

## R7 — fresh two-attempt allowance request

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$63.5288` |
| Existing active reservation | `$10.00` |
| Committed plus reserved | `$73.5288` |
| Headroom after reservation | `$226.4712` |
| Attempts authorized | at most `2` |
| Maximum per attempt | `4,500` worker-seconds |
| Maximum fresh allowance | `9,000` worker-seconds |
| Estimated compute for both caps | approximately `$3.20` |
| Cold rehearsal | mandatory before each attempt |

The two attempt caps are independent. Unused seconds from attempt 1 cannot extend
attempt 2 beyond 4,500 seconds. Attempt 2 is permitted only after attempt 1 has a
terminal receipt trail, cleanup PASS and a fresh unchanged cold rehearsal. The
existing $10 reservation covers the requested estimate; no new reservation or
aggregate-budget increase is requested.

## Plan and cleanup guards

The one-time bridge guard permits only non-destructive actions on the three
persistent credential addresses and refuses unrelated resources, replacement or
deletion. During an attempt:

| Phase | Exact maximum guarded delta |
|---|---:|
| Controller before endpoint DNS | `1 add / 0 change / 0 destroy` |
| Endpoint/probe boundary after resident-image proof | `11 add / 0 change / 0 destroy` |
| Full temporary cleanup | `0 add / 0 change / 12 destroy` |

Partial cleanup may delete only the subset already created. The persistent secret
and both permanent policies are excluded from every window cleanup target set.

## Immutable child-image identities

| Component | Scan-passed linux/amd64 child digest |
|---|---|
| AWS load-balancer controller | `sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f` |
| NVIDIA DRA | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |
| Model loader | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| ASR runtime | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| RAG index | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` |
| LLM gateway | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` |
| TTS gateway | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` |
| Orchestrator | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` |

## Complete frozen source-hash table

The authorization record must reproduce this table and add the cold receipt hash.

| Path | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_6_endpoint_policy_override.tf` | `9dc7e893cd8e0e4612bd082541d7f884cd35e37e964202b577901a26f3b05dae` |
| `infra/b6_6_persistent_secret_override.tf` | `abe501946e6545b8d844d115de95e7f7f6736c840dfec20d2efead05c4a0ad68` |
| `infra/b6_6_window_override.tf` | `c9dc7ebfd17b4ea0e9bf9b50fee7af529405ab44ee4e08827d3a5bf06ef39962` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/b6_integration_window.tf` | `df4fec719aa8a709e94d89040c1bc283d9f847aa894a803fb00bb93e99f3c144` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `59c1226f9a797e13756575ef77b45ce9324e1f1fb4743bc7d84fa8bec4f272dd` |
| `pipeline/b6_integration_receipts.py` | `3010e6b6062aea498f2599fd33c63feba8fbde4cc5b09883b2a772908b705f36` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
| `platform/decisions/B6-WINDOW-VERIFIER-POLICY-2026-001.json` | `73eacb9cc6a9d9850098464f70380c92e25c46ac4aff7e4b67515c0269b5a236` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/designs/B6-WINDOW-DESIGN-REVIEW-2026-001.md` | `b55198105f9a8de95191ad9032679e73bbb4f33df4f9a9c47e3359b3d759fd2a` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json` | `95735b36a225a3558cc95430258ec3d3b3a6ceb4976387498fa82004f5b3ca62` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `9fe3f02680560afc13ea80d7d2de97f2a4f12d8e32ea11f3e28599c980cb45c6` |
| `scripts/b6_6_cleanup.sh` | `819d6bf33f1adbb89b58127a173e81c826fbb2bc558cd80ff3436ea71400789b` |
| `scripts/b6_6_cold_rehearsal.py` | `8ba7f444375c22c3c5947f333d75bb7c21698554ebef7133ba2b594554daef18` |
| `scripts/b6_6_credential.py` | `5d8c7a60cf28f68267b3e47373a5699b4e59776d8ffdaa4e6fb020975352be4d` |
| `scripts/b6_6_deadline.py` | `fd3962f185c91359e3e046959801294bbbedd1c1db918c823c00a58abe2fa0e0` |
| `scripts/b6_6_fargate_probe.py` | `b7a904aaea8a148121c7959fd79fd9af44eb12c3645dcc76dbc4ab35c21f3c8c` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `5eae072a78914921520b95491a1df0c0ea9b6f448df1072f472a83c1b3db5ada` |
| `scripts/b6_6_persistent_secret_bridge.py` | `50dab57436c519d1975706b3add0e28f1ca9a50d0e6f83ab07c8b7c2ba0a2f72` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `a38377a37b8f53556389bcf947a9115ec02ca92db109fb9daa05ea2ab5db684d` |
| `scripts/b6_6_runner.py` | `634053e0cf5f3ee256db63142be02e9dc43ec59cba4ed44263de75067aea0a20` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `6d726830b1fd895ca3c444760696b267aa57c7e9c992448c5e14d7c346f6caa0` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `ab634923a1329348cd4b4f75edd80e62a893e55999296da973e81226ab9d7527` |
| `platform/evidence/receipts/B6-2026-019-COLD/cold_rehearsal.json` | `b60436ebe21161e46d8c93b6ff0775f6e3408285604f77abd0d9728030c588af` |

## Deviations

There is one disclosed implementation deviation caused by the current state, not
a disagreement with R1–R7:

- **One-time bridge before attempt 1.** R1 describes the desired steady state in
  which the secret already persists. Packet 2026-018 ended with the secret pending
  recoverable deletion, so packet 2026-019 must restore that existing ARN once and
  install its permanent policies before the steady-state stage-0 rotation can run.
  The bridge is outside the 23-stage window, performs no plaintext read, starts no
  compute, is machine-guarded to the three credential resources, and persists a v2
  receipt. It is then absent from every future attempt and packet.

There are no other deviations from R1–R7.

## Pre-execution authorization boundary

Independent review must verify the six acceptance criteria from the design review,
bind the exact prepared commit and this packet's SHA-256, and confirm the R7
arithmetic. Only then may the owner approve:

> Approve B6 AWS change packet 2026-019 and its fresh two-attempt allowance only.

An owner-approved `B6-AWS-AUTH-2026-019` record must then bind the reviewed commit,
packet hash, complete source table, cold receipt, persistent-secret rules and exact
allowance before the bridge or any attempt can execute.
