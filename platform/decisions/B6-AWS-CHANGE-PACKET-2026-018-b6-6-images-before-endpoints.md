# B6 AWS change packet 2026-018 — images-before-endpoints successor

**Status:** DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL
**Prepared:** 2026-08-10
**Starting master:** `fa52175c4aa264c8c1e8c2d17b5af1c8e1c6d01e`
**Execution authorization:** absent; this document authorizes nothing

## Why this packet exists

Packet 2026-017 proved the principal-independent ECR endpoint policies and
the temporary self-isolated endpoint security group, then refused at
`dra_ready`. Enabling ECR interface-endpoint private DNS redirected the GPU
node's later DRA pull to the probe-exclusive endpoint, where TCP/443 was
correctly unavailable to the node.

This successor keeps that stronger endpoint boundary. It removes the race by
making every Kubernetes image resident and every pod Ready before the private
endpoints exist. The endpoints remain exclusively for the private Fargate
probe and are created only after the pre-endpoint image receipt is durable.

## Exact allowed outcome

One bounded synthetic integration window may:

1. restore and rotate the synthetic test credential without reading or
   reusing old material;
2. arm both shutdown deadlines before compute;
3. start at most two `m6i.large` CPU workers and one `g6.xlarge` GPU worker;
4. pull and start the digest-pinned DRA, five application deployments and the
   load-balancer controller before private ECR DNS exists;
5. persist a pre-endpoint proof covering seven applications, seven Running
   and Ready pods, their scheduled nodes and eight exact child digests;
6. create the unchanged probe-only endpoint, Fargate, ALB-rule and IAM
   boundary;
7. run the private probe, file, WebSocket, cancellation, refusal and isolation
   proofs; and
8. delete every temporary resource, return both worker groups to zero and
   schedule recoverable deletion of the synthetic secret.

No production traffic, production serving pointer, approved model artifact,
model registration, MLflow transition, deployment adoption or B5 decision
change is permitted.

## Receipt-per-stage rule

Receipt protocol v2 makes persistence structural. The runner invokes every
attempted execution stage through `b6_stage_execute`; the same wrapper
persists `PASS` and `REFUSED` receipts before returning to the cleanup trap.
The previously approved tag-mutation classification alone may instead persist
`WARNING_NON_FATAL`. A stage cannot advance unless its predecessor receipt is
`PASS` (or that one bounded warning). Cleanup failures persist `REFUSED`, and
cleanup may bind a refusing receipt; it therefore cannot erase the last
successful or refusing stage.

The exact ordered chain is:

`local_bindings → deadline → workers_ready → dra_ready → rag_ready → asr_ready → tts_ready → llm_ready → orchestrator_ready → controller_window → controller_ready → pre_endpoint_images → terraform_window → endpoints_ready → fargate_probe → alb_ready → alb_tag_mutation_warning → file_proof → websocket_proof → cancellation_proof → failure_drills → isolation_proof → cleanup`.

The historical v1 receipt code, packet 2026-017 authorization, result record
and receipts remain byte-identical.

## Images-before-endpoints boundary

Before `terraform_window` may create an endpoint:

- the endpoint-absence check must pass;
- DRA must be Ready on the GPU worker;
- RAG, ASR, TTS, LLM and orchestrator pods must be Running and Ready;
- the controller must be Running and Ready;
- each pod must have a scheduled node;
- every declared image must be digest-pinned; and
- every corresponding `imageID` must prove that exact child digest is present
  on the pod's scheduled node.

The ingress object is deliberately excluded from the pre-endpoint manifest
slice. It is applied only after endpoint availability, so the controller
cannot create the ALB during image qualification.

As soon as the endpoint Terraform apply succeeds, post-endpoint failure
classification is enabled, including while endpoint availability is still
being verified. After endpoint availability, no stage intentionally creates or replaces a
Kubernetes pod. The RAG outage drill changes the Service selector and restores
it; it does not scale or recreate the RAG deployment.

If any post-endpoint Kubernetes pod reports `ErrImagePull`,
`ImagePullBackOff` or `RegistryUnavailable`, the structural wrapper replaces
the stage's ordinary reason with
`POST_ENDPOINT_NEW_KUBERNETES_IMAGE_PULL_FATAL`, persists the refusing
receipt and starts cleanup. This known class is fatal. The private Fargate
probe's one qualified `medzen-rag-index` pull is the only intended new image
pull after endpoints exist.

## Split Terraform plan

The prior single create is split without changing infrastructure definitions:

| Phase | Required guarded delta |
|---|---:|
| Controller, before endpoint DNS | `1 add / 0 change / 0 destroy` |
| Probe endpoints and boundary, after image proof | `11 add / 0 change / 0 destroy` |
| Full cleanup after all resources exist | `0 add / 0 change / 15 destroy` |

Read-only previews from the live zero state passed:

| Preview | SHA-256 | Guard |
|---|---|---|
| `/private/tmp/b6-018-controller-preview.tfplan` | `2bbfffb440ffe3fc36513e3ea193b14963cbb9a503bc8ef65558c9129698095a` | `PASS_B6_6_IMAGES_FIRST_CONTROLLER changes=1` |
| `/private/tmp/b6-018-endpoints-preview.tfplan` | `e6e241830e28f9349ba2157aa6210b355d403fb98a16fe54149e70b9778e2f0c` | `PASS_B6_6_IMAGES_FIRST_PREVIEW-ENDPOINTS changes=11` |

At execution, the second guard additionally requires the controller resource
to be a no-op. Cleanup still requires the exact 15-resource destroy plan when
both create receipts exist; partial creation uses only the bounded subset
cleanup guard.

## Credential stage 0

The compute-free credential stage is versioned by
`B6-CLIENT-API-KEYS-RESTORE-2026-005`. It starts only from:

- secret version `201f9790-72c4-45f7-a05b-967551532aef` as `AWSCURRENT`;
- the three older versions unstaged;
- the secret pending recoverable deletion;
- resource and KMS reader policies absent;
- local token absent; and
- CPU/GPU desired zero.

It restores the exact ARN, imports and reconciles only the three proven
Terraform addresses, generates fresh random material, verifies the new
version and hashes, and proves all four prior versions are unstaged. Failure
before verification starts no compute and triggers the proven recoverable
cleanup.

## Budget and one-attempt boundary

| Control | Value |
|---|---:|
| Aggregate project ceiling | `$300` |
| Recognized committed guardrail | `$63.5288` |
| Existing active reservation | `$10` |
| Cumulative window seconds already charged | `7,548` |
| Remaining seconds before this packet | `6,852` |
| Maximum packet-2026-018 worker deadline | `4,500` |
| Remaining after the full cap | `2,352` |

The 6,852-second balance is sufficient for exactly one full attempt. If this
window stops before the conversation proofs, no retry is permitted under the
remaining arithmetic: a fresh owner allowance decision is required first,
even if some seconds remain. This packet creates no new reservation.

## Stage failure and cleanup

Any unknown state, malformed payload, missing receipt, failed plan guard,
readiness timeout, image drift, endpoint drift, post-endpoint image pull,
probe refusal or unexpected AWS/Kubernetes result fails closed. The stage
wrapper persists the refusal first; the outer trap then:

1. stops the one permitted Fargate task if it exists;
2. deletes the ingress and waits for ALB absence;
3. deletes all synthetic workloads and DRA objects;
4. applies only the machine-guarded Terraform deletion set;
5. proves endpoint and endpoint-SG absence;
6. scales GPU and CPU groups to desired zero;
7. removes local credential material and schedules recoverable secret
   deletion;
8. waits for both autoscaling groups to contain zero instances;
9. disarms the deadline actions only after zero; and
10. persists the content-addressed cleanup receipt.

## Immutable image identities

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

## Principal source bindings

The eventual authorization record must bind every source required by
`scripts/b6_6_images_before_endpoints_bindings.py`. Principal prepared files
are currently:

| Path | SHA-256 |
|---|---|
| `pipeline/b6_integration_receipts_v2.py` | `9cbaa1785ee1feda603635e652141170358c3a9e8dc1551260c90d91c740a84c` |
| `platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-005.json` | `2cd0d9b5e53bbd507223021112c72bef3f902402ef3ebb33ff07fdfe51036e5e` |
| `scripts/b6_6_images_before_endpoints_bindings.py` | `34069ae7191dca81dc172cb50dcc773f4b8b661412f12f7923a050f0188cfac6` |
| `scripts/b6_6_images_before_endpoints_cleanup.sh` | `432259b26b3c22210bea20239a31d8817b5824929349916f9907070fdb7b65a2` |
| `scripts/b6_6_images_before_endpoints_credential_stage.py` | `a72d1187011da126f8df02a68a9360e677471ca1ed82d2b398bb01e8424c39f0` |
| `scripts/b6_6_images_before_endpoints_credential_stage.sh` | `6f2785b7b7536539f1ba87ca00619c71624c757b0a34479ecb8a1884f072e14a` |
| `scripts/b6_6_images_before_endpoints_secret_preflight.py` | `dcecef295d0ea22e29af25ac3e563e5df960771f6683e8d1fe782606d5310a89` |
| `scripts/b6_6_images_before_endpoints_window.sh` | `26635b0249bffd7ff08c45a1d2b93dcf107986a445eb803c99a638e35e873405` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_receipt_v2.py` | `8306e847dc2f1e9f98bf7c1823952152512d31262ba2c77d41e3335e84ceac2b` |
| `scripts/b6_6_stage_runtime.sh` | `ee2cc8976d17b92fba735bae88d2816bb2119c656f847992049eaf69daf65935` |
| `scripts/check_b6_6_images_before_endpoints_plan.py` | `62710e3375daaffd9b3eb9e6ebe4a8b811c86bf1817edee236901a9ad8011115` |
| `tests/test_b6_6_images_before_endpoints.py` | `ed35d302c3efb952f3ece3c2f6f502c69a383d16e02985023c1070cab9ff28ff` |

The complete source set, including immutable prior infrastructure, image-scan,
registry, cost and refusal evidence, is enforced by the validator and must be
listed with exact hashes in the later owner authorization record.

## Explicit non-events during preparation

- AWS mutations: `0`
- Kubernetes mutations: `0`
- workers started: `0`
- synthetic credential restored or read: `false`
- endpoints, Fargate tasks or ALBs created: `0`
- production SSM writes: `0`
- approved ASR writes: `0`

## Approval boundary

Independent review must bind the exact prepared commit and this packet's
SHA-256. Only after that review may the owner state exactly:

> Approve B6 AWS change packet 2026-018 only.

An authorization record must then bind the reviewed commit, packet hash,
complete source-hash set and unchanged cost limits. Until that record exists,
do not execute any stage of this packet.
