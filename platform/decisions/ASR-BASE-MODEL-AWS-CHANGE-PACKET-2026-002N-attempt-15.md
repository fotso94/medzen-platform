# ASR base-model AWS change packet 2026-002N — diagnosed DRA successor attempt 15

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002N only, authorizing numbered attempt 15 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002N` must bind this
packet. A committed, read-only `deadline_identity_and_acceptance` dry run must
then PASS against the actual authorization, bindings and packet before any AWS
call or attempt envelope.

## Attempt-14 refusal and diagnosis

Attempt 14 is consumed. Six stages passed, including the 90-second GPU-node
registration fix. DRA applied but never reached the one-ready/one-available
contract within 300 seconds. Cleanup returned all temporary state to zero.
Observed GPU lifetime to termination request was about 511 seconds.

- refusal: `0782c3b972dbc85c10843fa7ac7b08af7849ae4119daf5ccb204fa2519736509`;
- diagnosis: `6718be3f2e697fdb3c98a25cfdd2c71d8745d8ef66bdcb5b221f07acfccfaada`;
- timing audit: `a663045cb79048900395723f2b5c9e32fe3c58d9dd0f317c58242bd1700df3aa`;
- Kubernetes API destination capture: `7e41d6306156428851f194dfb13d950b590051eee12c93d3858bc2ab9c0995a8`.

The repository contains 23 B6 live-attempt directories, but only 13 independent
`dra_ready` receipts with a measurable `workers_ready` predecessor. Duplicate
embedded copies, cold rehearsals and Stage A receipts were not counted. The 13
intervals have minimum 12 s, median 23 s, mean 26 s and maximum/P95 61 s;
zero exceeded 300 s. Three B6A stable-readiness receipts corroborate at 20–26
poll-derived seconds. Therefore the DRA helper remains capped at 300 seconds.

The B6.6-proven and attempt-14 DRA manifests are byte-identical at SHA-256
`0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897`,
including child image digest `sha256:7fb313758a20...c361d59246`. There is no
image, tag, configuration or namespace-manifest drift.

The execution-order difference is material: B6 deployed DRA before temporary
private endpoints and strict CNI network-policy enforcement. Evaluation enabled
strict/default-deny mode before DRA. Attempt 14 retained apply success and one
Running 0/1-ready pod, but no pod conditions, events, probe error or container
logs. The exact live root cause cannot be asserted after cleanup. The diagnosis
therefore records an evidence-backed leading cause, not a proven root cause:
the DRA API client lacked an explicit path to the Kubernetes API while strict
default-deny policy applied.

## Bounded correction

The locked B6 DRA deployment stays byte-unchanged. A separate evaluation-only
manifest creates the same temporary `nvidia-dra-driver` namespace and selects
only the DRA kubelet-plugin pods. It permits only TCP/443 to the live-read,
hash-bound Kubernetes service ClusterIP `10.100.0.1/32`; no other egress is
allowed. The manifest SHA-256 is
`8913060ac5921b5ca4c2109ff5ebac2d28910e5578bf77a0f3da4c4d7f9421e7`.
It is applied before the unchanged DRA manifest and deleted with the namespace.

On every future DRA wait refusal, the real stage now persists a write-once,
sanitized `dra-refusal-diagnostics.json` before cleanup. It is bounded to 30
seconds per query, four pods, eight containers per pod, 100 events, and 200 log
lines per container. It retains DaemonSet status/describe, pod conditions and
container states, events, DeviceClass, ResourceSlices and safe logs. This runs
before any model, audio, transcript or prediction exists on the node, so it
contains no PHI by construction. Diagnostic capture is best-effort and can
never suppress the terminal refusal or cleanup.

## Host resource gate

A keep-list was committed before deletion. Only three exact, closed external
scratch directories were deleted; they are not locally recoverable, while the
immutable S3 prestage and repository evidence remain. The packet-bound eval
image, its ECR digest and the pinned CUDA base were reverified unchanged.

- cleanup keep-list: `249fc4a843c69cbe6e7e4f478d22ef0221e56ea92a4ee44261516dbd21650df7`;
- cleanup result: `c03b96c410350104da6f92c72a4f2dcef01f93dc6781d8e21c46af118d50b4f8`;
- measured free space after cleanup: 51.88 GiB;
- fresh resource qualification: `392eef51adeab4a1a27c2aed131a6c6efc55b3e34113ddbac7dcf10fd5323c5e`;
- qualification free bytes: 55,664,517,120, which exceeds the 40-GiB floor by
  12,714,844,160 bytes.

The pre-envelope gate remeasures disk, memory, CPU, process limits, required
tools, Docker, workdir and Scout credentials immediately before execution. A
failure does not create an attempt envelope or consume attempt 15.

## Execution completeness and cold rehearsal

All 20 executor modules are hash-bound in
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002N.json`, SHA-256
`53b6f5badacd3357241a010544fb1dbd038a80a33bd571b6c4147bb94521d2f3`.
The exact plan has 0 permanent creates, 0 permanent bounded updates, 18
temporary create/delete entries and one bounded GPU capacity change. The new
NetworkPolicy is explicitly included in the temporary inventory.

The receipt-last cold rehearsal is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002N-COLD/cold-rehearsal.json`,
SHA-256 `d5a1145f67cf3593faa928caf730993799e9a99449b484d271f256b3d83bcc76`.
It executes real `LiveOperations` with fakes only at AWS, kubectl and scanner
boundaries. It must record two full PASS paths and all refusal scenarios,
including a DRA-not-ready run that persists bounded diagnostics before cleanup.
It also binds 20/20 module hashes, all 48 bounded helper call sites, zero-state
cleanup, exact plan count 18, and no AWS/kubectl call.

The packet and final rehearsal SHA-256 values are populated only after the
receipt-last run at the final reviewed source. No executor source may change
afterward without rebinding and re-rehearsing.

## Cost reconciliation and request

Cost Explorer was queried read-only after attempt 14 under
`arn:aws:iam::558069890522:user/s.fotso`. Current-day data is still estimated
and contains no `g6.xlarge` row even though immutable evidence proves 511 GPU
seconds. Zero is not reported as actual cost. `COST-REGISTRY-2026-010`, SHA-256
`dedc3160ced2374277961f7cdad0a2609f36682d71101051d3cdd9e38985f53e`,
conservatively recognizes the entire prior $10 ceiling pending ingestion.

- project ceiling: $300;
- recognized committed guardrail: $114.4286064216;
- active reservations: $0;
- current headroom: $185.5713935784;
- requested one-attempt ceiling: $10;
- headroom if approved: $175.5713935784.

## Exact attempt-15 scope

Unchanged: qualified image/build context; four accepted offline-only PyTorch
HIGH tuples; risk record; frozen 540-row/47-language evaluation; pre-staged
13,116,686,091-byte bundle; network isolation; one GPU node maximum.

Only after review and exact authorization:

1. attempt 15 only; 10,800 seconds; one GPU node; fresh $10 ceiling;
2. pre-envelope local-resource gate and committed stage-1 dry validation;
3. exact digest rescan with no image upload, then verify-only S3 artifacts;
4. temporary S3/ECR endpoints and strict workload network isolation;
5. temporary DRA namespace plus exact API-only egress policy, unchanged DRA,
   one encrypted 60-GiB volume, GPU sampler and offline 540-row pilot;
6. immediate receipts, bounded DRA refusal diagnostics, status-keyed cleanup
   and zero-state proof on every outcome.

Prohibited: attempt reuse/extension; IAM or KMS changes; registry-wide scanning;
internet egress; inbound routes; PHI/untrusted inputs; training; serving;
promotion; `approved/asr`; production SSM; MLflow registration; registry
language mutation.

## Post-approval order

1. write and commit authorization 002N;
2. commit the real-artifact stage-1 dry validation;
3. remeasure the pre-envelope local-resource gate;
4. only on PASS create the attempt envelope and execute once;
5. persist every stage receipt and always clean up;
6. commit terminal evidence and later reconcile finalized billing.

## Deviations

The requested 25 independent B6 `dra_ready` receipts do not exist in the
repository. Thirteen independent, measurable B6 receipts and three independent
B6A readiness receipts were audited instead; no copies were counted as new
measurements. Attempt 14 cannot yield exact probe-failure text because the old
runner did not persist it. This limitation is explicit, and the successor makes
that diagnostic retention structural. Actual attempt-14 billing is pending, so
the full ceiling is recognized conservatively. There are no silent deviations.
